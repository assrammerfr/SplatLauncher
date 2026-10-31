/* SplatNative first-runnable-unit harness.
   Loads sdk + main NSOs from the local dump, applies relocations, runs
   init arrays, calls nnMain, and logs SVC activity until stop. */
#include "a64_ctx.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

extern FILE* g_log;
extern uint64_t g_svc_hist[0x400];
extern int load_nso(const char* name, const char* path, uintptr_t base);
extern void apply_patches(void);
extern void apply_host_patches(void);

#define LOGF(...) do { fprintf(g_log, __VA_ARGS__); fflush(g_log); \
                       fprintf(stderr, __VA_ARGS__); } while (0)

static A64Ctx ctx;

/* register watch target: fn_dispatch calls a64_hit_watch when pc matches
   (used to capture nn::diag::AbortImpl's file/func/expr assertion args). */
uint64_t g_watch = 0;

/* native fault reporter: guest loads/stores run as plain host memory access,
   so an unmapped guest address surfaces as an SEH exception here */
static LONG WINAPI on_crash(EXCEPTION_POINTERS* ep) {
    DWORD code = ep->ExceptionRecord->ExceptionCode;
    void* faddr = (code == 0xC0000005 && ep->ExceptionRecord->NumberParameters >= 2)
                      ? (void*)ep->ExceptionRecord->ExceptionInformation[1]
                      : (void*)ep->ExceptionRecord->ExceptionAddress;
    LOGF("[crash] code=%08lx fault=%p guest_pc=%llx sp=%llx\n",
         code, faddr,
         (unsigned long long)ctx.pc, (unsigned long long)ctx.x[31]);
    LOGF("[crash] x0=%llx x1=%llx x2=%llx x3=%llx x19=%llx x20=%llx x21=%llx x30=%llx\n",
         (unsigned long long)ctx.x[0], (unsigned long long)ctx.x[1],
         (unsigned long long)ctx.x[2], (unsigned long long)ctx.x[3],
         (unsigned long long)ctx.x[19], (unsigned long long)ctx.x[20],
         (unsigned long long)ctx.x[21], (unsigned long long)ctx.x[30]);
    for (int i = 4; i <= 18; i++)
        LOGF("[crash]   x%d=%llx\n", i, (unsigned long long)ctx.x[i]);
    for (int i = 0; i < 30; i++)
        if (ctx.x[i] > 0x1000 && ctx.x[i] < 0x0000800000000000ull
            && (ctx.x[i] < 0x08000000ull || ctx.x[i] > 0x22000000ull))
            LOGF("[crash]   x%d=%llx\n", i, (unsigned long long)ctx.x[i]);
    LOGF("[crash] svc_count=%llu undef=%llu\n",
         (unsigned long long)ctx.svc_count, (unsigned long long)ctx.undef_count);
    a64_dump_trace();
    return EXCEPTION_EXECUTE_HANDLER;
}

static int run_until(uint64_t stop_pc, const char* what, uint64_t max_steps) {
    uint64_t steps = 0;
    if (ctx.pc == A64_STOP) return -1;
    while (ctx.pc != stop_pc && ctx.pc != A64_STOP) {
        if (++steps > max_steps) {
            LOGF("[run] %s: step limit %llu hit at pc=%llx\n", what,
                 (unsigned long long)max_steps, (unsigned long long)ctx.pc);
            ctx.pc = A64_STOP;
            return -2;
        }
        fn_dispatch(&ctx);
    }
    return ctx.pc == A64_STOP ? -1 : 0;
}

int main(int argc, char** argv) {
    const char* exe_dir = "";
    (void)argc; (void)argv;

    g_log = fopen("boot.log", "w");
    if (!g_log) g_log = stderr;
    SetUnhandledExceptionFilter(on_crash);

    LOGF("[boot] SplatNative first runnable unit - builder: Freebuff\n");
    LOGF("[boot] modules: rtld @ 0x08500000, sdk @ 0x0e6fe000, main @ 0x08506000\n");

    /* map modules at the absolute addresses from the Ryujinx boot log.
       Order matters: main's reservation starts at 0x08506000, which rounds
       down onto rtld, so the contiguous block is reserved first and rtld
       then commits inside it. */
    if (load_nso("sdk", "E:\\S3_extract\\sdk", 0x0e6fe000) != 0) return 1;
    if (load_nso("main", "E:\\S3_extract\\main", 0x08506000) != 0) return 1;
    if (load_nso("rtld", "E:\\S3_extract\\rtld", 0x08500000) != 0) return 1;

    apply_patches();
    apply_host_patches();

    LOGF("[boot] fn table: %u functions, entry=%llx, patches=%u, host=%u\n",
         g_fn_count, (unsigned long long)g_entry, g_patch_count, g_host_patch_count);

    memset(&ctx, 0, sizeof(ctx));

    /* watch the registry Lock: dump mutex pointer, owner word, tid */
    g_watch = 0xe83da98ull;
    LOGF("[boot] watch AbortImpl @ %llx\n", (unsigned long long)g_watch);

    /* guest stack: fixed 16 MB region (nothing else maps there) */
    if (!VirtualAlloc((void*)0x20000000, 16 * 1024 * 1024,
                      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        LOGF("[boot] stack alloc failed (%lu)\n", GetLastError());
        return 1;
    }
    ctx.x[31] = 0x20000000 + 16 * 1024 * 1024 - 0x1000;
    LOGF("[boot] guest sp=%llx\n", (unsigned long long)ctx.x[31]);

    /* Thread Local Region (switchbrew): 0x200 bytes addressed by
       tpidrro_el0. Kernel zeroes it; SDK conventions:
         [0x1F0] ThreadPointer self-ref  (tpidr_el0 = tpidrro + 0x1F0;
                                          __get_tls_data reads [0x1F0]-0x18
                                          = tpidrro+0x1D8 = &ErrnoVal)
         [0x1F8] ThreadType* -> zeroed fake block until the SDK's
                  ThreadManager ctor registers the real main ThreadType
         [0x1E8] EhGlobals -> zeroed struct until C++ runtime fills it */
    if (!VirtualAlloc((void*)0x30000000, 1024 * 1024,
                      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) {
        LOGF("[boot] tls alloc failed (%lu)\n", GetLastError());
        return 1;
    }    ctx.tpidrro = 0x30000000ull;
    ctx.tpidr = 0x30000000ull + 0x1F0;
    *(uint64_t*)0x300001F0ull = 0x30000000ull + 0x1F0;
    *(uint64_t*)0x300001F8ull = 0x30000000ull + 0x400;
    *(uint64_t*)0x300001E8ull = 0x30000000ull + 0x600;
    /* Fake ThreadType at 0x30000400 (TLR +0x1F8 points here).
       SDK mutex code (nn::os) uses ThreadType.thread_id at +0x1B0 as the
       owner token, and 0 means UNLOCKED there: Lock does ldaxr/cbnz on the
       owner word, so a zero thread_id stores an owner that reads back as
       "free" and IsLockedByCurrentThread's assert then fires. Seed a
       nonzero id; the SDK's ThreadManager ctor will overwrite the whole
       block (and the +0x1F8 pointer) when it registers the main thread. */
    *(uint32_t*)0x300005B0ull = 1;   /* ThreadType.thread_id */
    LOGF("[boot] TLR at 0x30000000 (tpidr=%llx) thread_id=1\n",
         (unsigned long long)ctx.tpidr);
    LOGF("[boot] peek sdk mutex 0xec4cd44 = %08x\n",
         *(unsigned int*)0xec4cd44ull);

    /* run init arrays: sdk first, then main (tag field = module ordinal) */
    for (int i = 0; g_init_arrays[i][1]; i++) {
        uint64_t fn = g_init_arrays[i][1];
        ctx.x[30] = A64_INIT_RET;
        ctx.pc = fn;
        LOGF("[init] call %llx\n", (unsigned long long)fn);
        int rc = run_until(A64_INIT_RET, "init_array", 200000000ull);
        if (rc != 0) {
            LOGF("[init] FAILED rc=%d pc=%llx\n", rc, (unsigned long long)ctx.pc);
            break;
        }
    }

    if (ctx.pc != A64_STOP && g_entry) {
        LOGF("[boot] calling entry nnMain at %llx\n", (unsigned long long)g_entry);
        ctx.x[30] = A64_STOP;
        ctx.pc = g_entry;
        run_until(A64_STOP, "nnMain", 5000000000ull);
    }

    LOGF("[done] pc=%llx svc_count=%llu undef=%llu\n",
         (unsigned long long)ctx.pc, (unsigned long long)ctx.svc_count,
         (unsigned long long)ctx.undef_count);
    LOGF("[svc histogram]\n");
    for (int i = 0; i < 0x400; i++)
        if (g_svc_hist[i])
            LOGF("  svc #%03x: %llu\n", i, (unsigned long long)g_svc_hist[i]);
    LOGF("[done] exit\n");
    fclose(g_log);
    return 0;
}
