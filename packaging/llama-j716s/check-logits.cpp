// Bounded CM1 prefill and single-token decode regression fixture.
// Write logits separately so Vulkan validation messages cannot pollute them.
#include "llama.h"
#include "ggml-backend.h"
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>

int main(int argc, char ** argv) {
    if ((argc != 4 && argc != 5) || (std::strcmp(argv[2], "cpu") && std::strcmp(argv[2], "Vulkan0"))) {
        return 2;
    }
    const bool gpu = std::strcmp(argv[2], "Vulkan0") == 0;
    // Diagnostic reference only; the timed Vulkan runs keep fused attention.
    if (argc == 5 && (!gpu || std::strcmp(argv[4], "unfused-reference"))) {
        return 2;
    }
    ggml_backend_load_all();
    llama_backend_init();
    ggml_backend_dev_t devices[] = {gpu ? ggml_backend_dev_by_name(argv[2]) : nullptr, nullptr};
    if (gpu && !devices[0]) {
        return 2;
    }
    auto mp = llama_model_default_params();
    mp.devices = devices;
    mp.n_gpu_layers = gpu ? 99 : 0;
    std::unique_ptr<llama_model, decltype(&llama_model_free)> model(llama_model_load_from_file(argv[1], mp), llama_model_free);
    if (!model) {
        return 3;
    }
    auto cp = llama_context_default_params();
    cp.n_ctx = 512;
    cp.n_batch = cp.n_ubatch = 256;
    cp.n_threads = cp.n_threads_batch = 2;
    if (argc == 5) {
        cp.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_DISABLED;
    }
    std::unique_ptr<llama_context, decltype(&llama_free)> ctx(llama_init_from_model(model.get(), cp), llama_free);
    if (!ctx) {
        return 4;
    }
    const int n_vocab = llama_vocab_n_tokens(llama_model_get_vocab(model.get()));
    if (n_vocab < 1024) {
        return 4;
    }
    const auto close = [](std::FILE * file) { std::fclose(file); };
    std::unique_ptr<std::FILE, decltype(close)> out(std::fopen(argv[3], "wbx"), close);
    if (!out) {
        return 7;
    }
    std::array<llama_token, 256> tokens;
    for (size_t i = 0; i < tokens.size(); ++i) {
        tokens[i] = 1 + ((i * 37) % 1023);
    }
    for (int step = 0; step < 5; ++step) {
        llama_token next = 100 + step;
        auto batch = step ? llama_batch_get_one(&next, 1) : llama_batch_get_one(tokens.data(), tokens.size());
        if (llama_decode(ctx.get(), batch) != 0) {
            return 5;
        }
        const float * logits = llama_get_logits_ith(ctx.get(), -1);
        if (!logits) {
            return 5;
        }
        for (int i = 0; i < n_vocab; ++i) {
            if (!std::isfinite(logits[i])) {
                return 6;
            }
        }
        if (std::fwrite(logits, sizeof(float), n_vocab, out.get()) != static_cast<size_t>(n_vocab)) {
            return 7;
        }
    }
    return std::fclose(out.release()) == 0 ? 0 : 7;
}
