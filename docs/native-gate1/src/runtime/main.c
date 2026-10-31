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

    LOGF("[boot] SplatNative first runnable unit - builder: Freebuff\n");
    LOGF("[boot] modules: sdk @ 0x0e6fe000, main @ 0x08506000\n");

    /* map modules at the absolute addresses from the Ryujinx boot log */
    if (load_nso("sdk", "E:\\S3_extract\\sdk", 0x0e6fe000) != 0) return 1;
    if (load_nso("main", "E:\\S3_extract\\main", 0x08506000) != 0) return 1;

    apply_patches();
    apply_host_patches();

    LOGF("[boot] fn table: %u functions, entry=%llx, patches=%u, host=%u\n",
         g_fn_count, (unsigned long long)g_entry, g_patch_count, g_host_patch_count);

    memset(&ctx, 0, sizeof(ctx));

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
