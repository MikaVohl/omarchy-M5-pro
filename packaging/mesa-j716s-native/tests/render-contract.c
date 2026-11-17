#define EGL_EGLEXT_PROTOTYPES
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned char pixels[256 * 256 * 4];
static void check(const char *step) {
    GLenum error = glGetError();
    if (error != GL_NO_ERROR) {
        fprintf(stderr, "%s: GL error 0x%x; stopping without retry\n", step, error);
        exit(1);
    }
}
static GLuint shader(GLenum stage, const char *source) {
    GLuint id = glCreateShader(stage);
    glShaderSource(id, 1, &source, NULL);
    glCompileShader(id);
    GLint compiled = 0;
    glGetShaderiv(id, GL_COMPILE_STATUS, &compiled);
    if (!compiled) {
        char log[4096];
        glGetShaderInfoLog(id, sizeof(log), NULL, log);
        fprintf(stderr, "shader compilation failed: %s\n", log);
        exit(1);
    }
    return id;
}
static GLuint framebuffer(int size, int samples, GLuint *buffer) {
    GLuint fbo;
    glGenFramebuffers(1, &fbo);
    glGenRenderbuffers(1, buffer);
    glBindRenderbuffer(GL_RENDERBUFFER, *buffer);
    if (samples > 1)
        glRenderbufferStorageMultisample(GL_RENDERBUFFER, samples, GL_RGBA8, size, size);
    else
        glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, size, size);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, *buffer);
    check("framebuffer allocation");
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "incomplete %dx%d sample-%d framebuffer\n", size, size, samples);
        exit(1);
    }
    return fbo;
}
static void run_case(GLuint program, int size, int samples) {
    GLuint buffer, resolve_buffer = 0;
    GLuint fbo = framebuffer(size, samples, &buffer);
    GLuint resolve = samples > 1 ? framebuffer(size, 1, &resolve_buffer) : fbo;
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glViewport(0, 0, size, size);
    glDisable(GL_DITHER);
    glDisable(GL_BLEND);
    glClearColor(0.f, 0.f, 1.f, 1.f);
    glClear(GL_COLOR_BUFFER_BIT);
    glUseProgram(program);
    glDrawArrays(GL_TRIANGLES, 0, 3);
    if (samples > 1) {
        glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo);
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, resolve);
        glBlitFramebuffer(0, 0, size, size, 0, 0, size, size, GL_COLOR_BUFFER_BIT, GL_NEAREST);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, resolve);
    glReadPixels(0, 0, size, size, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
    check("render/resolve/readback");
    for (int i = 0; i < size * size; ++i) {
        const unsigned char *p = &pixels[i * 4];
        if (p[0] != 255 || p[1] != 0 || p[2] != 0 || p[3] != 255) {
            fprintf(stderr, "%dx%d samples=%d pixel %d: %u,%u,%u,%u, expected red\n",
                    size, size, samples, i, p[0], p[1], p[2], p[3]);
            exit(1);
        }
    }
    printf("PASS %dx%d samples=%d: %d/%d exact RGBA pixels\n", size, size, samples, size * size, size * size);
    glDeleteFramebuffers(1, &fbo);
    glDeleteRenderbuffers(1, &buffer);
    if (samples > 1) {
        glDeleteFramebuffers(1, &resolve);
        glDeleteRenderbuffers(1, &resolve_buffer);
    }
}
int main(void) {
    EGLDisplay display = eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, NULL);
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL) || !eglBindAPI(EGL_OPENGL_ES_API)) {
        fprintf(stderr, "EGL setup failed: 0x%x\n", eglGetError());
        return 1;
    }
    const EGLint config_attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE,
        EGL_OPENGL_ES3_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE};
    EGLConfig config;
    EGLint count;
    if (!eglChooseConfig(display, config_attributes, &config, 1, &count) || count != 1) return 1;
    const EGLint context_attributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attributes);
    if (context == EGL_NO_CONTEXT || !eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context)) return 1;
    const char *renderer = (const char *)glGetString(GL_RENDERER);
    printf("Renderer: %s\nVersion: %s\n", renderer, glGetString(GL_VERSION));
    fflush(stdout);
    if (!renderer || !strstr(renderer, "Apple M5") || strstr(renderer, "llvmpipe") || strstr(renderer, "zink")) {
        fprintf(stderr, "Expected real native Apple M5 Gallium renderer\n");
        return 1;
    }
    const char *vs = "#version 300 es\nvoid main(){vec2 p[3]=vec2[3](vec2(-1,-1),vec2(3,-1),vec2(-1,3));gl_Position=vec4(p[gl_VertexID],0,1);}";
    const char *fs = "#version 300 es\nprecision highp float;out vec4 c;void main(){c=vec4(1,0,0,1);}";
    GLuint vertex = shader(GL_VERTEX_SHADER, vs), fragment = shader(GL_FRAGMENT_SHADER, fs);
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glLinkProgram(program);
    GLint linked;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        char log[4096]; glGetProgramInfoLog(program, sizeof(log), NULL, log);
        fprintf(stderr, "link failed: %s\n", log); return 1;
    }
    run_case(program, 16, 1);
    run_case(program, 16, 4);
    run_case(program, 256, 1);
    glDeleteProgram(program);
    glDeleteShader(vertex);
    glDeleteShader(fragment);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglTerminate(display);
    return 0;
}
