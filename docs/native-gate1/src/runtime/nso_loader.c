/* SplatNative NSO loader: maps program modules at their absolute load bases,
   decompresses segments (LZ4 block format), applies relocations. */
#include "a64_ctx.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#define LOGF(...) do { extern FILE* g_log; if (g_log) { fprintf(g_log, __VA_ARGS__); fflush(g_log);} \
                       fprintf(stderr, __VA_ARGS__); } while (0)

extern void a64_host_log(const char* name);

typedef struct LoadedModule {
    const char* name;
    uintptr_t base;
    size_t span;
} LoadedModule;

LoadedModule g_modules[8];
int g_module_count = 0;

static uint32_t ru32(const uint8_t* p) {
    uint32_t v; memcpy(&v, p, 4); return v;
}

/* Decompress one LZ4 block (raw block format, same as Python lz4.block). */
int lz4_decompress_safe(const char* src, char* dst, int srcSize, int dstCapacity);

static int read_file(const char* path, uint8_t** out, size_t* outlen) {
    FILE* f = fopen(path, "rb");
    if (!f) return -1;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0) { fclose(f); return -1; }
    uint8_t* buf = (uint8_t*)malloc((size_t)n);
    if (!buf) { fclose(f); return -1; }
    if (fread(buf, 1, (size_t)n, f) != (size_t)n) { free(buf); fclose(f); return -1; }
    fclose(f);
    *out = buf;
    *outlen = (size_t)n;
    return 0;
}

int load_nso(const char* name, const char* path, uintptr_t base) {
    uint8_t* raw = NULL;
    size_t rawlen = 0;
    if (read_file(path, &raw, &rawlen) != 0) {
        LOGF("[load] %s: cannot read %s\n", name, path);
        return -1;
    }
    if (rawlen < 0x70 || memcmp(raw, "NSO0", 4) != 0) {
        LOGF("[load] %s: not NSO0 (%zu bytes)\n", name, rawlen);
        free(raw);
        return -1;
    }
    uint32_t flags = ru32(raw + 0x0C);
    uint32_t bss = ru32(raw + 0x3C);
    struct { uint32_t foff, moff, size, csize; } segs[3];
    size_t span = 0;
    for (int i = 0; i < 3; i++) {
        uint32_t b = 0x10 + i * 0x10;
        segs[i].foff = ru32(raw + b + 0x0);
        segs[i].moff = ru32(raw + b + 0x4);
        segs[i].size = ru32(raw + b + 0x8);
        segs[i].csize = ru32(raw + 0x60 + i * 4);
        if (segs[i].moff + segs[i].size > span) span = segs[i].moff + segs[i].size;
    }
    span += bss;  /* bss follows the data segment; VirtualAlloc zero-fills */

    uint8_t* mem = (uint8_t*)VirtualAlloc((void*)base, span,
                                          MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (mem != (void*)base) {
        LOGF("[load] %s: VirtualAlloc(%p, %zu) failed (%lu)\n",
             name, (void*)base, span, GetLastError());
        free(raw);
        return -1;
    }
    for (int i = 0; i < 3; i++) {
        if (!segs[i].size) continue;
        uint8_t* dst = mem + segs[i].moff;
        int compressed = (flags >> i) & 1;
        if (compressed) {
            int r = lz4_decompress_safe((const char*)(raw + segs[i].foff),
                                        (char*)dst, (int)segs[i].csize,
                                        (int)segs[i].size);
            if (r != (int)segs[i].size) {
                LOGF("[load] %s: seg %d LZ4 failed (%d != %u)\n",
                     name, i, r, segs[i].size);
                free(raw);
                return -1;
            }
        } else {
            memcpy(dst, raw + segs[i].foff, segs[i].size);
        }
    }
    LOGF("[load] %s mapped at %p span=0x%zx flags=%x\n", name, (void*)base, span, flags);
    free(raw);
    g_modules[g_module_count].name = name;
    g_modules[g_module_count].base = base;
    g_modules[g_module_count].span = span;
    g_module_count++;
    return 0;
}

static int in_module(uint64_t off, size_t n) {
    for (int i = 0; i < g_module_count; i++) {
        uintptr_t lo = g_modules[i].base;
        uintptr_t hi = lo + g_modules[i].span;
        if (off >= lo && off + n <= hi) return 1;
    }
    return 0;
}

void apply_patches(void) {
    uint64_t bad = 0;
    for (unsigned i = 0; i < g_patch_count; i++) {
        uint64_t off = g_patches[i].off;
        if (!in_module(off, 8)) {
            if (bad < 8)
                LOGF("[reloc] patch off %llx outside modules (skipped)\n",
                     (unsigned long long)off);
            bad++;
            continue;
        }
        *(uint64_t*)(uintptr_t)off = g_patches[i].val;
    }
    LOGF("[reloc] applied %u patches (%llu out of range)\n",
         g_patch_count, (unsigned long long)bad);
}

/* host symbol table: unresolved game imports bound to runtime stubs */
typedef struct HostSym { const char* name; void* fn; } HostSym;
static HostSym g_host_syms[64];
static int g_host_sym_count;

static void host_nvn_bootstrap(A64Ctx* c) {
    LOGF("[host] nvnBootstrapLoader called -> returning 0 stub\n");
    c->pc = c->x[30];  /* guest RET semantics */
}

static void host_generic_stub(A64Ctx* c) {
    c->pc = c->x[30];
}

/* called from generated fn_dispatch before the guest table lookup */
int host_dispatch(A64Ctx* c) {
    for (int j = 0; j < g_host_sym_count; j++) {
        if ((uint64_t)(uintptr_t)g_host_syms[j].fn == c->pc) {
            ((void (*)(A64Ctx*))g_host_syms[j].fn)(c);
            return 1;
        }
    }
    return 0;
}

static void init_host_syms(void) {
    /* filled from g_host_patches names */
    for (unsigned i = 0; i < g_host_patch_count && g_host_sym_count < 64; i++) {
        const char* n = g_host_patches[i].name;
        int seen = 0;
        for (int j = 0; j < g_host_sym_count; j++)
            if (strcmp(g_host_syms[j].name, n) == 0) { seen = 1; break; }
        if (seen) continue;
        void* fn = (void*)host_generic_stub;
        if (strstr(n, "nvnBootstrap")) fn = (void*)host_nvn_bootstrap;
        g_host_syms[g_host_sym_count].name = n;
        g_host_syms[g_host_sym_count].fn = fn;
        LOGF("[host] bind %s -> stub %p\n", n, fn);
        g_host_sym_count++;
    }
}

void apply_host_patches(void) {
    init_host_syms();
    uint64_t missing = 0;
    for (unsigned i = 0; i < g_host_patch_count; i++) {
        uint64_t off = g_host_patches[i].off;
        if (!in_module(off, 8)) continue;
        void* fn = NULL;
        for (int j = 0; j < g_host_sym_count; j++)
            if (strcmp(g_host_syms[j].name, g_host_patches[i].name) == 0) {
                fn = g_host_syms[j].fn;
                break;
            }
        if (!fn) {
            missing++;
            continue;
        }
        *(uint64_t*)(uintptr_t)off = (uint64_t)(uintptr_t)fn;
    }
    LOGF("[reloc] applied %u host patches (%llu unresolved->0)\n",
         g_host_patch_count, (unsigned long long)missing);
}
