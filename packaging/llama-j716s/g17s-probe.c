/* Bounded, single-queue G17S microprobe. Device identity must come from the compute receipt. */
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CHECK(call) do { VkResult r = (call); if (r != VK_SUCCESS) { \
    fprintf(stderr, "%s: VkResult %d\n", #call, r); exit(2); } } while (0)
enum { GROUPS = 2048, LANES = GROUPS * 128, CHAIN_COUNT = 512, SAMPLES = 7 };
#define MIB (1024u * 1024u)
#define SOURCE_BYTES (256u * MIB)

struct buffer { VkBuffer handle; VkDeviceMemory memory; uint32_t *map; };
struct probe {
    VkDevice dev;
    VkQueue queue;
    VkCommandBuffer cmd;
    VkQueryPool queries;
    VkFence fence;
    VkPipelineLayout layout;
    VkDescriptorSet set;
    double period;
    uint64_t timestamp_mask;
};
struct duration { double gpu_ns, wall_ns; };

static void refuse(const char *why)
{
    fprintf(stderr, "%s\n", why);
    exit(2);
}

static struct buffer make_buffer(VkDevice dev, const VkPhysicalDeviceMemoryProperties *mem, VkDeviceSize size)
{
    struct buffer b;
    VkBufferCreateInfo info = { .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = size, .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT };
    CHECK(vkCreateBuffer(dev, &info, NULL, &b.handle));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, b.handle, &req);
    const VkMemoryPropertyFlags flags = VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT |
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    uint32_t type = 0;
    while (type < mem->memoryTypeCount && (!(req.memoryTypeBits & (1u << type)) ||
           (mem->memoryTypes[type].propertyFlags & flags) != flags))
        ++type;
    if (type == mem->memoryTypeCount)
        refuse("No coherent, host-visible device-local buffer type");
    VkMemoryAllocateInfo alloc = { .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = req.size, .memoryTypeIndex = type };
    CHECK(vkAllocateMemory(dev, &alloc, NULL, &b.memory));
    CHECK(vkBindBufferMemory(dev, b.handle, b.memory, 0));
    void *map;
    CHECK(vkMapMemory(dev, b.memory, 0, size, 0, &map));
    b.map = map;
    return b;
}

static void drop_buffer(VkDevice dev, struct buffer b)
{
    vkUnmapMemory(dev, b.memory);
    vkDestroyBuffer(dev, b.handle, NULL);
    vkFreeMemory(dev, b.memory, NULL);
}

static VkPipeline make_pipeline(struct probe *p, const char *path)
{
    uint32_t code[4096];
    FILE *file = fopen(path, "rb");
    if (!file) { perror(path); exit(2); }
    const size_t size = fread(code, 1, sizeof(code), file);
    if (!feof(file) || ferror(file) || size < 20 || size % 4 || code[0] != 0x07230203u)
        refuse("Invalid or oversized SPIR-V module");
    fclose(file);
    VkShaderModuleCreateInfo shader_info = { .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = size, .pCode = code };
    VkShaderModule shader;
    CHECK(vkCreateShaderModule(p->dev, &shader_info, NULL, &shader));
    VkComputePipelineCreateInfo pipeline_info = { .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = { .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = shader, .pName = "main" }, .layout = p->layout };
    VkPipeline pipeline;
    CHECK(vkCreateComputePipelines(p->dev, VK_NULL_HANDLE, 1, &pipeline_info, NULL, &pipeline));
    vkDestroyShaderModule(p->dev, shader, NULL);
    return pipeline;
}

static double monotonic_ns(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) { perror("clock_gettime"); exit(2); }
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}

static struct duration dispatch(struct probe *p, VkPipeline pipeline, uint32_t groups, uint32_t words,
                                 uint32_t count, int dependent)
{
    VkCommandBufferBeginInfo begin = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
    CHECK(vkResetCommandBuffer(p->cmd, 0));
    CHECK(vkBeginCommandBuffer(p->cmd, &begin));
    vkCmdResetQueryPool(p->cmd, p->queries, 0, 2);
    vkCmdBindPipeline(p->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(p->cmd, VK_PIPELINE_BIND_POINT_COMPUTE, p->layout, 0, 1, &p->set, 0, NULL);
    vkCmdWriteTimestamp(p->cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, p->queries, 0);
    VkMemoryBarrier dep = { .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT };
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t arg = words ? words : dependent ? 0 : i;
        vkCmdPushConstants(p->cmd, p->layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(arg), &arg);
        vkCmdDispatch(p->cmd, groups, 1, 1);
        if (dependent && i + 1 < count)
            vkCmdPipelineBarrier(p->cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &dep, 0, NULL, 0, NULL);
    }
    vkCmdWriteTimestamp(p->cmd, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, p->queries, 1);
    dep.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    vkCmdPipelineBarrier(p->cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                         0, 1, &dep, 0, NULL, 0, NULL);
    CHECK(vkEndCommandBuffer(p->cmd));
    CHECK(vkResetFences(p->dev, 1, &p->fence));
    VkSubmitInfo submit = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1,
        .pCommandBuffers = &p->cmd };
    const double start = monotonic_ns();
    CHECK(vkQueueSubmit(p->queue, 1, &submit, p->fence));
    CHECK(vkWaitForFences(p->dev, 1, &p->fence, VK_TRUE, UINT64_C(3000000000)));
    const double wall = monotonic_ns() - start;
    uint64_t ts[2];
    CHECK(vkGetQueryPoolResults(p->dev, p->queries, 0, 2, sizeof(ts), ts, sizeof(ts[0]), VK_QUERY_RESULT_64_BIT));
    const uint64_t ticks = (ts[1] - ts[0]) & p->timestamp_mask;
    if (!ticks || ticks * p->period > wall + 1000000)
        refuse("Invalid GPU timestamp interval");
    return (struct duration){ ticks * p->period, wall };
}

static void verify_read(const uint32_t *out, uint32_t vecs)
{
    const uint32_t n = vecs / LANES;
    const uint32_t offset = (uint32_t)((uint64_t)LANES * n * (n - 1) / 2);
    for (uint32_t i = 0; i < LANES; ++i) {
        const uint32_t s = i * n + offset;
        if (out[4*i] != s || out[4*i+1] != s + 19u*n ||
            out[4*i+2] != 3u*s + n || out[4*i+3] != 7u*s + 5u*n)
            refuse("Read probe checksum mismatch");
    }
}

int main(int argc, char **argv)
{
    if (argc != 4)
        refuse("Usage: g17s-probe READ.spv CHAIN.spv DEVICE_UUID_HEX");
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .apiVersion = VK_API_VERSION_1_2 };
    VkInstanceCreateInfo inst_info = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app };
    VkInstance inst;
    CHECK(vkCreateInstance(&inst_info, NULL, &inst));
    uint32_t n = 1;
    VkPhysicalDevice pd;
    CHECK(vkEnumeratePhysicalDevices(inst, &n, &pd));
    if (n != 1)
        refuse("Expected exactly one private Vulkan device");
    VkPhysicalDeviceIDProperties ids = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES };
    VkPhysicalDeviceProperties2 props = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &ids };
    vkGetPhysicalDeviceProperties2(pd, &props);
    char uuid[VK_UUID_SIZE * 2 + 1];
    for (uint32_t i = 0; i < VK_UUID_SIZE; ++i)
        snprintf(&uuid[2*i], 3, "%02x", ids.deviceUUID[i]);
    const VkPhysicalDeviceLimits *lim = &props.properties.limits;
    if (strcmp(uuid, argv[3]) || strcmp(props.properties.deviceName, "Apple M5 Pro (G17S B1)") ||
        lim->maxStorageBufferRange < SOURCE_BYTES || lim->maxComputeWorkGroupCount[0] < GROUPS ||
        lim->maxComputeWorkGroupInvocations < 128 || lim->maxComputeWorkGroupSize[0] < 128 || lim->timestampPeriod <= 0)
        refuse("Device UUID, G17S identity, or required limits differ from the probe contract");
    VkQueueFamilyProperties queues[32];
    n = 32;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &n, queues);
    uint32_t family = 0;
    while (family < n && (!(queues[family].queueFlags & VK_QUEUE_COMPUTE_BIT) || !queues[family].timestampValidBits))
        ++family;
    if (family == n)
        refuse("No timestamp-capable compute queue");
    const uint32_t bits = queues[family].timestampValidBits;
    struct probe p = { .period = lim->timestampPeriod, .timestamp_mask = bits == 64 ? UINT64_MAX : (UINT64_C(1) << bits) - 1 };
    float priority = 1.0f;
    VkDeviceQueueCreateInfo qinfo = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority };
    VkDeviceCreateInfo devinfo = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qinfo };
    CHECK(vkCreateDevice(pd, &devinfo, NULL, &p.dev));
    vkGetDeviceQueue(p.dev, family, 0, &p.queue);
    VkPhysicalDeviceMemoryProperties mem;
    vkGetPhysicalDeviceMemoryProperties(pd, &mem);
    struct buffer src = make_buffer(p.dev, &mem, SOURCE_BYTES);
    struct buffer dst = make_buffer(p.dev, &mem, LANES * 16u);
    for (uint32_t i = 0; i < SOURCE_BYTES / 16; ++i) {
        src.map[4*i] = i;
        src.map[4*i+1] = i + 19u;
        src.map[4*i+2] = 3u*i + 1u;
        src.map[4*i+3] = 7u*i + 5u;
    }
    const VkDescriptorSetLayoutBinding bindings[2] = {
        { 0, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL },
        { 1, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL },
    };
    VkDescriptorSetLayoutCreateInfo slinfo = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 2, .pBindings = bindings };
    VkDescriptorSetLayout set_layout;
    CHECK(vkCreateDescriptorSetLayout(p.dev, &slinfo, NULL, &set_layout));
    VkPushConstantRange pc = { VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(uint32_t) };
    VkPipelineLayoutCreateInfo plinfo = { .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1, .pSetLayouts = &set_layout, .pushConstantRangeCount = 1, .pPushConstantRanges = &pc };
    CHECK(vkCreatePipelineLayout(p.dev, &plinfo, NULL, &p.layout));
    VkDescriptorPoolSize pool_size = { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2 };
    VkDescriptorPoolCreateInfo dpinfo = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1, .poolSizeCount = 1, .pPoolSizes = &pool_size };
    VkDescriptorPool dpool;
    CHECK(vkCreateDescriptorPool(p.dev, &dpinfo, NULL, &dpool));
    VkDescriptorSetAllocateInfo dsinfo = { .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = dpool, .descriptorSetCount = 1, .pSetLayouts = &set_layout };
    CHECK(vkAllocateDescriptorSets(p.dev, &dsinfo, &p.set));
    const VkDescriptorBufferInfo buffers[2] = { { src.handle, 0, SOURCE_BYTES }, { dst.handle, 0, LANES * 16u } };
    VkWriteDescriptorSet writes[2];
    for (uint32_t i = 0; i < 2; ++i)
        writes[i] = (VkWriteDescriptorSet){ .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = p.set,
            .dstBinding = i, .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .pBufferInfo = &buffers[i] };
    vkUpdateDescriptorSets(p.dev, 2, writes, 0, NULL);
    VkCommandPoolCreateInfo cpinfo = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = family };
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(p.dev, &cpinfo, NULL, &pool));
    VkCommandBufferAllocateInfo cinfo = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1 };
    CHECK(vkAllocateCommandBuffers(p.dev, &cinfo, &p.cmd));
    VkQueryPoolCreateInfo qpinfo = { .sType = VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO,
        .queryType = VK_QUERY_TYPE_TIMESTAMP, .queryCount = 2 };
    CHECK(vkCreateQueryPool(p.dev, &qpinfo, NULL, &p.queries));
    VkFenceCreateInfo finfo = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    CHECK(vkCreateFence(p.dev, &finfo, NULL, &p.fence));
    VkPipeline read = make_pipeline(&p, argv[1]);
    VkPipeline chain = make_pipeline(&p, argv[2]);
    fprintf(stderr, "%s UUID=%s queue=%u timestamp_bits=%u period_ns=%.3f\n",
            props.properties.deviceName, uuid, family, bits, p.period);
    puts("probe,sample,bytes,dispatches,gpu_ns,submit_to_fence_ns");
    for (unsigned sample = 0; sample <= SAMPLES; ++sample) {
        for (unsigned size = 64; size <= 256; size *= 4) {
            const uint32_t bytes = size * MIB;
            struct duration t = dispatch(&p, read, GROUPS, bytes / 16, 1, 0);
            verify_read(dst.map, bytes / 16);
            if (sample)
                printf("read-%uMiB,%u,%u,1,%.0f,%.0f\n", size, sample, bytes, t.gpu_ns, t.wall_ns);
        }
        for (int arm = 0; arm < 2; ++arm) {
            const int dependent = (sample + arm) % 2;
            for (uint32_t i = 0; i < CHAIN_COUNT; ++i)
                dst.map[i] = i + sample;
            struct duration t = dispatch(&p, chain, 1, 0, CHAIN_COUNT, dependent);
            uint32_t expected = sample;
            if (dependent) {
                for (unsigned i = 0; i < CHAIN_COUNT; ++i)
                    expected = expected * 1664525u + 1013904223u;
            }
            for (uint32_t i = 0; i < CHAIN_COUNT; ++i) {
                uint32_t want = dependent ? (i == 0 ? expected : i + sample) : (i + sample) * 1664525u + 1013904223u;
                if (dst.map[i] != want)
                    refuse("Dispatch chain readback mismatch");
            }
            if (sample)
                printf("%s,%u,0,%u,%.0f,%.0f\n", dependent ? "dependent" : "independent", sample,
                       CHAIN_COUNT, t.gpu_ns, t.wall_ns);
        }
    }
    /* Same buffer and kernel as the single dispatch, but keep the GPU busy
       across a longer interval. Include the output write-order barriers. */
    enum { READ_BURST = 32 };
    for (unsigned sample = 0; sample <= SAMPLES; ++sample) {
        struct duration t = dispatch(&p, read, GROUPS, SOURCE_BYTES / 16, READ_BURST, 1);
        verify_read(dst.map, SOURCE_BYTES / 16);
        if (sample)
            printf("read-256MiB-burst,%u,%llu,%u,%.0f,%.0f\n", sample,
                   (unsigned long long)SOURCE_BYTES * READ_BURST, READ_BURST, t.gpu_ns, t.wall_ns);
    }
    vkDestroyPipeline(p.dev, chain, NULL);
    vkDestroyPipeline(p.dev, read, NULL);
    vkDestroyFence(p.dev, p.fence, NULL);
    vkDestroyQueryPool(p.dev, p.queries, NULL);
    vkDestroyCommandPool(p.dev, pool, NULL);
    vkDestroyDescriptorPool(p.dev, dpool, NULL);
    vkDestroyPipelineLayout(p.dev, p.layout, NULL);
    vkDestroyDescriptorSetLayout(p.dev, set_layout, NULL);
    drop_buffer(p.dev, dst);
    drop_buffer(p.dev, src);
    vkDestroyDevice(p.dev, NULL);
    vkDestroyInstance(inst, NULL);
    fprintf(stderr, "All bandwidth checksums and dispatch outputs matched\n");
    return 0;
}
