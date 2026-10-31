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
/* physical reservation ranges [start,end) actually held in the address space */
static uintptr_t g_res[8][2];
static int g_res_count = 0;

/* Reserve [start,end) free parts, committing any part already reserved by a
   neighbour module (adjacent modules can overlap by rounding down to 64 KB). */
static int reserve_covering(uintptr_t start, uintptr_t end) {
    uintptr_t pos = start;
    for (int i = 0; i < g_res_count && pos < end; i++) {
        uintptr_t r0 = g_res[i][0], r1 = g_res[i][1];
        if (r1 <= pos || r0 >= end) continue;
        if (r0 > pos) {
            if (!VirtualAlloc((void*)pos, r0 - pos, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE))
                return -1;
            pos = r0 > pos ? r0 : pos;
        }
        /* [r0, r1) is already a neighbour's reservation; commit the overlap
           (its pages are within one reservation, so MEM_COMMIT is legal) */
        uintptr_t ov0 = r0 > start ? r0 : start;
        uintptr_t ov1 = r1 < end ? r1 : end;
        if (ov1 > ov0 && !VirtualAlloc((void*)ov0, ov1 - ov0, MEM_COMMIT, PAGE_READWRITE))
            return -1;
        if (r1 > pos) pos = r1;
    }
    if (pos < end) {
        if (!VirtualAlloc((void*)pos, end - pos, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE))
            return -1;
    }
    if (g_res_count < 8) { g_res[g_res_count][0] = start; g_res[g_res_count][1] = end; g_res_count++; }
    return 0;
}

static uint32_t ru32(const uint8_t* p) {
    uint32_t v; memcpy(&v, p, 4); return v;
}

/* Decompress one LZ4 block (raw block format, same as Python lz4.block). */
int LZ4_decompress_safe(const char* src, char* dst, int srcSize, int dstCapacity);

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

    /* Windows rounds fixed addresses to 64 KB allocation granularity:
       request the aligned-down address and keep the delta as a prefix. */
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    uintptr_t gran = si.dwAllocationGranularity;
    uintptr_t aligned = base & ~(gran - 1);
    uintptr_t delta = base - aligned;
    if (reserve_covering(aligned, aligned + delta + span) != 0) {
        LOGF("[load] %s: reserve [%llx,%llx) failed (%lu)\n", name,
             (unsigned long long)aligned,
             (unsigned long long)(aligned + delta + span), GetLastError());
        free(raw);
        return -1;
    }
    uint8_t* mem = (uint8_t*)aligned + delta;  /* image at the requested base */
    for (int i = 0; i < 3; i++) {
        if (!segs[i].size) continue;
        uint8_t* dst = mem + segs[i].moff;
        int compressed = (flags >> i) & 1;
        if (compressed) {
            int r = LZ4_decompress_safe((const char*)(raw + segs[i].foff),
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
    /* DT_RELR compressed relative relocations (DT_RELR=0x24, DT_RELRSZ=0x23).
       Entries are BYTE offsets: an even entry E relocates the u64 at E and
       sets where = E + 8; an odd entry is a bitmap whose bits 1..63 select
       the next 63 slots from where, then where advances 63*8. Values in the
       image are module-relative, so rebase each by the load base.
       (gen.py previously mis-decoded this table with the DT_RELRENT size tag
       and word-index units, leaving ~544k pointer slots unrebased - every
       vtable call then jumped to a raw relative address.) */
    {
        size_t mod0 = (size_t)-1;
        for (size_t i = 0; i + 4 < span; i++) {
            if (mem[i] == 'M' && mem[i + 1] == 'O' && mem[i + 2] == 'D' &&
                mem[i + 3] == '0') { mod0 = i; break; }
        }
        if (mod0 == (size_t)-1) {
            LOGF("[reloc] %s: MOD0 not found, no relr applied\n", name);
        } else {
            int32_t dyn_rel;
            memcpy(&dyn_rel, mem + mod0 + 4, 4);
            uint64_t relr = 0, relrsz = 0;
            if (dyn_rel > 0 && mod0 + (size_t)dyn_rel + 16 <= span) {
                const uint8_t* dyn = mem + mod0 + dyn_rel;
                for (int k = 0; k < 8192; k++) {
                    uint64_t tag, val;
                    memcpy(&tag, dyn + (size_t)k * 16, 8);
                    if (tag == 0) break;
                    memcpy(&val, dyn + (size_t)k * 16 + 8, 8);
                    if (tag == 0x24) relr = val;
                    else if (tag == 0x23) relrsz = val;
                }
            }
            if (!relr || !relrsz || relr + relrsz > span) {
                LOGF("[reloc] %s: no relr table (ptr=%llx sz=%llx)\n", name,
                     (unsigned long long)relr, (unsigned long long)relrsz);
            } else {
                uint64_t where = 0;
                int have = 0;
                unsigned n = 0, skipped = 0;
                for (uint64_t o = 0; o + 8 <= relrsz; o += 8) {
                    uint64_t e;
                    memcpy(&e, mem + relr + o, 8);
                    if (e & 1) {
                        if (!have) { skipped++; continue; }
                        for (int i = 1; i <= 63; i++) {
                            if (!((e >> i) & 1)) continue;
                            uint64_t off = where + (uint64_t)i * 8;
                            if (off + 8 > span) { skipped++; continue; }
                            uint64_t v;
                            memcpy(&v, mem + off, 8);
                            v += (uint64_t)base;
                            memcpy(mem + off, &v, 8);
                            n++;
                        }
                        where += 63 * 8;
                    } else {
                        where = e;
                        have = 1;
                        if (e + 8 > span) { skipped++; continue; }
                        uint64_t v;
                        memcpy(&v, mem + e, 8);
                        v += (uint64_t)base;
                        memcpy(mem + e, &v, 8);
                        n++;
                    }
                }
                LOGF("[reloc] %s: relr %u slots rebased (%u skipped)\n",
                     name, n, skipped);
            }
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
static HostSym g_host_syms[4096];
static int g_host_sym_count;

static void host_nvn_bootstrap(A64Ctx* c) {
    LOGF("[host] nvnBootstrapLoader called -> returning 0 stub\n");
    c->x[0] = 0;
    c->pc = c->x[30];  /* guest RET semantics */
}

static void host_generic_stub(A64Ctx* c) {
    c->x[0] = 0;  /* return NULL/0: safest failure value for unresolved imports */
    c->pc = c->x[30];
}

/* called from generated fn_dispatch before the guest table lookup */
int host_dispatch(A64Ctx* c) {
    /* guest addresses are all below 4 GB; host stubs live above, so skip
       the scan entirely on ordinary guest dispatch (hot path). */
    if (c->pc <= 0xFFFFFFFFull) return 0;
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
    for (unsigned i = 0; i < g_host_patch_count && g_host_sym_count < 4096; i++) {
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
