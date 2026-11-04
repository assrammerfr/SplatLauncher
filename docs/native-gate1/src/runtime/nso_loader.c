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

/* Diagnostic PC probe: when SN_MARK names an address, log the register
   state and the recent pc ring exactly there and stop the run, so a check
   that fails deep inside an SDK call can be read straight from the log
   instead of being buried under the abort machinery. */
uint64_t g_mark_pc = 0;
uint64_t g_cov_lo = 0, g_cov_hi = 0;
uint64_t g_bigtrace[65536];
unsigned g_bigtrace_i;
void a64_mark_hit(A64Ctx* c);
void a64_cov_hit(A64Ctx* c);

/* called from generated fn_dispatch before the guest table lookup */
int host_dispatch(A64Ctx* c) {
    if (g_mark_pc && c->pc == g_mark_pc) a64_mark_hit(c);
    if (g_cov_lo && c->pc >= g_cov_lo && c->pc < g_cov_hi) a64_cov_hit(c);
    /* Deep pc ring for post-mortem reads: the generated 1024-entry ring
       is far too short to hold an SDK abort sequence. */
    g_bigtrace[g_bigtrace_i++ & 0xffffu] = c->pc;
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

/* ---- Horizon memory HLE (Gate 1 root-heap gate) --------------------------
   nnMain's heap setup (main 0xc9347a0) calls nn::os::QueryMemoryInfo and then
   nn::os::SetMemoryHeapSize + nn::os::AllocateMemoryBlock to carve the sead
   "RootHeap". The SDK versions of those imports are driven by the kernel
   memory manager, which this runtime does not emulate: QueryMemoryInfo returns
   {0,0}, so main computes heap_size = 0, AllocateMemoryBlock fails, ca42cf0
   constructs a zero-size RootHeap and ca3e5ec later derefs the never-created
   heap/TLS manager at 0xe6e70f8 (+0x68 -> fault 0x68).
   Bind the three imports to host implementations that hand out one real block
   in a fixed low region (guest addresses stay below 4 GB). */
/* 1 GB guest-low arena the HLE carves memory blocks out of. One block is
   requested for the nn::os memory heap (32 MB) and one for the sead RootHeap
   (about 222 MB), so the stub must hand out distinct regions. */
#define MEMBLOCK_ARENA 0x50000000ull
#define MEMBLOCK_ARENA_SIZE 0x40000000ull
static uint64_t g_memblock_base, g_memblock_next, g_memblock_size;



static int arena_reserve(void) {
    static const uint64_t cand[] = { 0x50000000ull, 0x90000000ull, 0x70000000ull, 0xa0000000ull };
    if (g_memblock_base) return 1;
    for (unsigned i = 0; i < sizeof(cand) / sizeof(cand[0]); i++) {
        if (VirtualAlloc((void*)(uintptr_t)cand[i], (SIZE_T)MEMBLOCK_ARENA_SIZE,
                         MEM_RESERVE, PAGE_NOACCESS)) {
            g_memblock_base = g_memblock_next = cand[i];
            LOGF("[host] memory arena %llx..%llx reserved\n",
                 (unsigned long long)cand[i],
                 (unsigned long long)(cand[i] + MEMBLOCK_ARENA_SIZE));
            return 1;
        }
        LOGF("[host] arena base %llx unavailable (%lu)\n",
             (unsigned long long)cand[i], GetLastError());
    }
    return 0;
}

static uint64_t arena_alloc(uint64_t n) {
    n = (n + 0x1fffffull) & ~0x1fffffull;
    if (!n) return 0;
    if (!arena_reserve()) return 0;
    if (g_memblock_next + n > g_memblock_base + MEMBLOCK_ARENA_SIZE) return 0;
    if (!VirtualAlloc((void*)(uintptr_t)g_memblock_next, (SIZE_T)n, MEM_COMMIT, PAGE_READWRITE))
        return 0;
    uint64_t r = g_memblock_next;
    g_memblock_next += n;
    g_memblock_size += n;
    return r;
}

uint64_t guest_scratch_alloc(uint64_t n) { return arena_alloc(n); }

static void host_query_memory_info(A64Ctx* c) {
    uint64_t out = c->x[0];
    if (out) {
        *(uint64_t*)(uintptr_t)out = 0x20000000ull;       /* total 512 MB */
        *(uint64_t*)(uintptr_t)(out + 8) = 0x10000000ull; /* used  256 MB */
    }
    c->x[0] = 0;                 /* Result success */
    c->pc = c->x[30];
}

/* nn::oe::SetCpuBoostMode has no service layer in this runtime; its SDK body
   derefs an uninitialized nn::oe client. The call is advisory, so succeed it. */
static void host_allocate_memory_block(A64Ctx* c) {
    uint64_t out = c->x[0];
    uint64_t size = (c->x[1] + 0x1fffffull) & ~0x1fffffull;
    uint64_t blk = arena_alloc(size);
    if (!blk) {
        LOGF("[host] AllocateMemoryBlock(%llx) FAILED (size=%llx arena_next=%llx)\n",
             (unsigned long long)size, (unsigned long long)c->x[1],
             (unsigned long long)g_memblock_next);
        c->x[0] = 0x1003;
        c->pc = c->x[30];
        return;
    }
    if (out) *(uint64_t*)(uintptr_t)out = blk;
    LOGF("[host] AllocateMemoryBlock(%llx) -> %llx\n",
         (unsigned long long)size, (unsigned long long)blk);
    c->x[0] = 0;
    c->pc = c->x[30];
}

static void host_set_cpu_boost_mode(A64Ctx* c) {
    c->x[0] = 0;
    c->pc = c->x[30];
}

static void host_set_memory_heap_size(A64Ctx* c) {
    LOGF("[host] SetMemoryHeapSize(%llx)\n", (unsigned long long)c->x[0]);
    c->x[0] = 0;
    c->pc = c->x[30];
}



static void register_host_sym(const char* name, void* fn);

/* ---- guest C heap for the SDK's libc allocator imports -------------------
   nn::fs and the rest of nnSdk reach the C allocator through undefined
   imports (malloc/free/calloc/realloc/aligned_alloc, slots below). In a real
   process those resolve to the executable's libc, whose heap the CRT startup
   (main 0xc9347a0) installs via nn::mem::Initialize (main 0x8f7da80) ->
   nn::mem::StandardAllocator::Initialize -> nn::nlibsdk::heap::CentralHeap::Init.
   That initializer builds its page map over the *physical* region descriptors
   that nnmem_nlib_physical_alloc hands it; this runtime has no physical
   address model, so the descriptor it receives is empty and CentralHeap::Init
   stores into a page map computed from a zero region address (crash at
   sdk 0xe82eb48, store to 0x008000008fb80a40, [x12+0x18]=0).
   Until a physical memory model exists, service the allocator imports from a
   real host-backed guest heap: pointers returned are ordinary guest addresses
   inside the mapped low arena, free/realloc honour the recorded block size,
   and exhaustion returns 0 exactly like a real allocator out of memory. */
#define GH_SIZE 0x20000000ull   /* 256 MB guest heap */
#define GH_HDR  16ull
static uint64_t g_gh_base, g_gh_next, g_gh_end;
static unsigned long long g_gh_calls, g_gh_fail;

static int gh_reserve(void) {
    static const uint64_t cand[] = { 0x40000000ull, 0x50000000ull, 0x60000000ull };
    if (g_gh_base) return 1;
    for (unsigned i = 0; i < sizeof(cand) / sizeof(cand[0]); i++) {
        if (VirtualAlloc((void*)(uintptr_t)cand[i], (SIZE_T)GH_SIZE,
                         MEM_RESERVE, PAGE_NOACCESS)) {
            g_gh_base = g_gh_next = cand[i];
            g_gh_end = cand[i] + GH_SIZE;
            LOGF("[host] guest C heap %llx..%llx reserved\n",
                 (unsigned long long)g_gh_base, (unsigned long long)g_gh_end);
            return 1;
        }
    }
    LOGF("[host] guest C heap reservation FAILED\n");
    return 0;
}

static uint64_t gh_alloc(uint64_t n, uint64_t align) {
    g_gh_calls++;
    if (!n) return 0;
    if (align < 16) align = 16;
    if (!gh_reserve()) { g_gh_fail++; return 0; }
    uint64_t p = (g_gh_next + align - 1) & ~(align - 1);
    uint64_t need = p + GH_HDR + n;
    if (need > g_gh_end) {
        g_gh_fail++;
        LOGF("[host] guest heap exhausted (want %llx, used %llx of %llx)\n",
             (unsigned long long)n, (unsigned long long)(g_gh_next - g_gh_base),
             (unsigned long long)GH_SIZE);
        return 0;
    }
    uint64_t commit = (need - g_gh_base + 0xFFFFull) & ~0xFFFFull;
    if (g_gh_base + commit > g_gh_next + 0) {
        static uint64_t committed;
        if (commit > committed) {
            if (!VirtualAlloc((void*)(uintptr_t)(g_gh_base + committed),
                              (SIZE_T)(commit - committed), MEM_COMMIT,
                              PAGE_READWRITE)) {
                g_gh_fail++;
                return 0;
            }
            committed = commit;
        }
    }
    *(uint64_t*)(uintptr_t)p = n;
    *(uint64_t*)(uintptr_t)(p + 8) = 0x53504c54ull;   /* 'SPLT' marker */
    g_gh_next = need;
    return p + GH_HDR;
}

static uint64_t gh_size_of(uint64_t user) {
    if (user < GH_HDR || user > g_gh_next) return 0;
    uint64_t h = user - GH_HDR;
    if (*(uint64_t*)(uintptr_t)(h + 8) != 0x53504c54ull) return 0;
    return *(uint64_t*)(uintptr_t)h;
}

static void host_malloc_(A64Ctx* c) {
    uint64_t p = gh_alloc(c->x[0], 16);
    if (g_gh_calls <= 8 || !p)
        LOGF("[cheap] malloc(%llx) -> %llx\n", (unsigned long long)c->x[0],
             (unsigned long long)p);
    c->x[0] = p;
    c->pc = c->x[30];
}

static void host_calloc_(A64Ctx* c) {
    uint64_t n = c->x[0], sz = c->x[1];
    uint64_t total = 0;
    if (n && sz && n > (~0ull) / sz) total = 0; else total = n * sz;
    uint64_t p = gh_alloc(total, 16);
    if (g_gh_calls <= 8 || !p)
        LOGF("[cheap] calloc(%llx,%llx) -> %llx\n", (unsigned long long)n,
             (unsigned long long)sz, (unsigned long long)p);
    c->x[0] = p;
    c->pc = c->x[30];
}

static void host_realloc_(A64Ctx* c) {
    uint64_t old = c->x[0], n = c->x[1];
    if (!old) { uint64_t p = gh_alloc(n, 16); c->x[0] = p; c->pc = c->x[30]; return; }
    if (!n) { c->x[0] = 0; c->pc = c->x[30]; return; }
    uint64_t osz = gh_size_of(old);
    uint64_t p = gh_alloc(n, 16);
    if (p && osz) memcpy((void*)(uintptr_t)p, (void*)(uintptr_t)old,
                         (size_t)(osz < n ? osz : n));
    c->x[0] = p;
    c->pc = c->x[30];
}

static void host_free_(A64Ctx* c) {
    uint64_t p = c->x[0];
    if (!p) { c->pc = c->x[30]; return; }
    uint64_t sz = gh_size_of(p);
    if (!sz) {
        LOGF("[cheap] free(%llx) NOT from the guest C heap (ignored)\n",
             (unsigned long long)p);
    }
    c->pc = c->x[30];
}

static void host_aligned_alloc_(A64Ctx* c) {
    uint64_t align = c->x[0], n = c->x[1];
    uint64_t p = gh_alloc(n, align);
    c->x[0] = p;
    c->pc = c->x[30];
}

/* sdk PLT slots for the undefined libc allocator imports (from the NSO's
   .rela.dyn: malloc/free/calloc/realloc/aligned_alloc, all shndx 0) */
#define SLOT_SDK_FREE1   0xf267a20ull
#define SLOT_SDK_FREE2   0xf26ba60ull
#define SLOT_SDK_MALLOC  0xf26bcc8ull
#define SLOT_SDK_CALLOC  0xf26ca88ull
#define SLOT_SDK_REALLOC 0xf26cb28ull
#define SLOT_SDK_ALIGNED 0xf26f028ull

/* ---- stack-guard region ------------------------------------------------
   nn::os::detail::StackGuardManager::Initialize (sdk 0xe847698) asks
   svcGetInfo for StackRegionAddress (0xe) and StackRegionSize (0xf) and
   manages that range; nn::os::CreateThread then maps each thread stack at a
   random address inside it (MapAliasStack -> MapAtRandomAddress).  Reserve a
   real host-backed range so those mappings have somewhere to live. */
#define SR_BASE 0x60000000ull   /* host-free span: 0x70000000+ is partly
                                   occupied in the host process (err 487) */
#define SR_SIZE 0x10000000ull   /* 256 MB */
static int g_sr_ok;

static void install_stack_region(void) {
    if (!VirtualAlloc((void*)(uintptr_t)SR_BASE, (SIZE_T)SR_SIZE,
                      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        LOGF("[host] stack region %llx..%llx reservation FAILED\n",
             (unsigned long long)SR_BASE,
             (unsigned long long)(SR_BASE + SR_SIZE));
        {
            MEMORY_BASIC_INFORMATION mbi;
            SIZE_T q = VirtualQuery((void*)(uintptr_t)SR_BASE, &mbi, sizeof(mbi));
            LOGF("[host] stack region probe err=%lu query=%llu state=%lx type=%lx protect=%lx base=%llx size=%llx\n",
                 (unsigned long)GetLastError(), (unsigned long long)q,
                 (unsigned long)mbi.State, (unsigned long)mbi.Type,
                 (unsigned long)mbi.Protect,
                 (unsigned long long)(uintptr_t)mbi.BaseAddress,
                 (unsigned long long)mbi.RegionSize);
        }
        return;
    }
    g_sr_ok = 1;
    LOGF("[host] stack region %llx..%llx ready (%llx B)\n",
         (unsigned long long)SR_BASE, (unsigned long long)(SR_BASE + SR_SIZE),
         (unsigned long long)SR_SIZE);
}

static void install_crt_alloc_hle(void) {
    register_host_sym("malloc", (void*)host_malloc_);
    register_host_sym("free", (void*)host_free_);
    register_host_sym("calloc", (void*)host_calloc_);
    register_host_sym("realloc", (void*)host_realloc_);
    register_host_sym("aligned_alloc", (void*)host_aligned_alloc_);
    if (!in_module(SLOT_SDK_MALLOC, 8)) {
        LOGF("[host] C heap HLE slots outside module image\n");
        return;
    }
    *(uint64_t*)(uintptr_t)SLOT_SDK_MALLOC  = (uint64_t)(uintptr_t)host_malloc_;
    *(uint64_t*)(uintptr_t)SLOT_SDK_CALLOC  = (uint64_t)(uintptr_t)host_calloc_;
    *(uint64_t*)(uintptr_t)SLOT_SDK_REALLOC = (uint64_t)(uintptr_t)host_realloc_;
    *(uint64_t*)(uintptr_t)SLOT_SDK_ALIGNED = (uint64_t)(uintptr_t)host_aligned_alloc_;
    *(uint64_t*)(uintptr_t)SLOT_SDK_FREE1   = (uint64_t)(uintptr_t)host_free_;
    *(uint64_t*)(uintptr_t)SLOT_SDK_FREE2   = (uint64_t)(uintptr_t)host_free_;
    LOGF("[host] C heap HLE installed: malloc@%llx calloc@%llx realloc@%llx "
         "free@%llx,%llx aligned_alloc@%llx (guest heap %llx B)\n",
         (unsigned long long)SLOT_SDK_MALLOC, (unsigned long long)SLOT_SDK_CALLOC,
         (unsigned long long)SLOT_SDK_REALLOC, (unsigned long long)SLOT_SDK_FREE1,
         (unsigned long long)SLOT_SDK_FREE2, (unsigned long long)SLOT_SDK_ALIGNED,
         (unsigned long long)GH_SIZE);
}

static void register_host_sym(const char* name, void* fn) {
    for (int j = 0; j < g_host_sym_count; j++)
        if (strcmp(g_host_syms[j].name, name) == 0) {
            g_host_syms[j].fn = fn;
            return;
        }
    if (g_host_sym_count < 4096) {
        g_host_syms[g_host_sym_count].name = name;
        g_host_syms[g_host_sym_count].fn = fn;
        g_host_sym_count++;
    }
}

/* main's PLT slots for these three imports (see main's .rela.plt) */
#define SLOT_QUERY_MEMORY_INFO 0xe469810ull
#define SLOT_SET_MEMORY_HEAP   0xe468710ull
#define SLOT_ALLOC_MEMORY_BLOCK 0xe468718ull
#define SLOT_SET_CPU_BOOST     0xe468708ull

static void install_memory_hle(void) {
    register_host_sym("_ZN2nn2os15QueryMemoryInfoEPNS0_10MemoryInfoE",
                      (void*)host_query_memory_info);
    register_host_sym("_ZN2nn2os17SetMemoryHeapSizeEm",
                      (void*)host_set_memory_heap_size);
    register_host_sym("_ZN2nn2os19AllocateMemoryBlockEPmm",
                      (void*)host_allocate_memory_block);
    register_host_sym("_ZN2nn2oe15SetCpuBoostModeENS0_12CpuBoostModeE",
                      (void*)host_set_cpu_boost_mode);
    if (in_module(SLOT_QUERY_MEMORY_INFO, 8)) {
        *(uint64_t*)(uintptr_t)SLOT_SET_CPU_BOOST = (uint64_t)(uintptr_t)host_set_cpu_boost_mode;
        *(uint64_t*)(uintptr_t)SLOT_QUERY_MEMORY_INFO = (uint64_t)(uintptr_t)host_query_memory_info;
        *(uint64_t*)(uintptr_t)SLOT_SET_MEMORY_HEAP = (uint64_t)(uintptr_t)host_set_memory_heap_size;
        *(uint64_t*)(uintptr_t)SLOT_ALLOC_MEMORY_BLOCK = (uint64_t)(uintptr_t)host_allocate_memory_block;
        LOGF("[host] memory HLE installed: QueryMemoryInfo@%llx SetMemoryHeapSize@%llx AllocateMemoryBlock@%llx\n",
             (unsigned long long)SLOT_QUERY_MEMORY_INFO,
             (unsigned long long)SLOT_SET_MEMORY_HEAP,
             (unsigned long long)SLOT_ALLOC_MEMORY_BLOCK);
    } else {
        LOGF("[host] memory HLE slots outside module image\n");
    }
}

/* After relocation, list GOT/PLT slots that are still zero. A PLT resolver stub
   is `adrp xN; ldr xM,[xN,#imm]; add xN,xN,#imm; br xM`; the ldr slot is the
   GOT entry, so a zero there is an import that neither a relocation nor a host
   patch filled. */
static void log_plt_zero_slots(void) {
    for (int mi = 0; mi < g_module_count; mi++) {
        uintptr_t base = g_modules[mi].base;
        size_t span = g_modules[mi].span;
        const uint32_t* w = (const uint32_t*)base;
        unsigned total = 0, zeros = 0;
        for (size_t off = 0; off + 16 <= span; off += 4) {
            uint32_t w0 = w[off / 4], w1 = w[off / 4 + 1];
            uint32_t w2 = w[off / 4 + 2], w3 = w[off / 4 + 3];
            if ((w3 & 0xFFFFFC1Fu) != 0xD61F0000u) continue;
            if ((w0 & 0x9F000000u) != 0x90000000u) continue;
            if ((w1 & 0xFFC00000u) != 0xF9400000u) continue;
            if ((w2 & 0xFFC00000u) != 0x91000000u) continue;
            if ((w0 & 0x1F) != ((w1 >> 5) & 0x1F)) continue;
            if ((w1 & 0x1F) != ((w3 >> 5) & 0x1F)) continue;
            uint64_t va = base + off;
            int64_t imm21 = (int64_t)((((w0 >> 5) & 0x7FFFF) << 2) | ((w0 >> 29) & 3));
            if (imm21 & 0x100000) imm21 -= 0x200000;
            uint64_t slot = (va & ~0xFFFull) + ((uint64_t)imm21 << 12)
                            + (((w1 >> 10) & 0xFFF) << 3);
            if (slot < base || slot + 8 > base + span) continue;
            total++;
            if (*(volatile uint64_t*)(uintptr_t)slot == 0) {
                zeros++;
                LOGF("[reloc] %s GOT slot %llx is ZERO (unstubbed import)\n",
                     g_modules[mi].name, (unsigned long long)slot);
            }
        }
        LOGF("[reloc] %s PLT slots: %u total, %u zero\n",
             g_modules[mi].name, total, zeros);
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
    install_memory_hle();
    install_crt_alloc_hle();
    install_stack_region();
    log_plt_zero_slots();
}
