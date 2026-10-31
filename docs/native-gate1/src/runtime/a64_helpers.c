/* SplatNative runtime logging + helper implementations. */
#include "a64_ctx.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

FILE* g_log = NULL;
uint64_t g_svc_hist[0x400];

static void lg(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    if (g_log) {
        va_list aq;
        va_copy(aq, ap);
        vfprintf(g_log, fmt, aq);
        va_end(aq);
        fflush(g_log);
    }
    vfprintf(stderr, fmt, ap);
    va_end(ap);
}

void a64_svc(A64Ctx* c, uint64_t imm) {
    c->svc_count++;
    if (imm < 0x400) g_svc_hist[imm]++;
    static uint64_t shown;
    if (shown < 64 || (c->svc_count & 0xfff) == 0) {
        shown++;
        lg("[svc] #%llx at pc=%llx (total %llu)\n",
           (unsigned long long)imm, (unsigned long long)c->pc,
           (unsigned long long)c->svc_count);
    }
}

void a64_break(A64Ctx* c, uint64_t pc, uint64_t word) {
    lg("[brk] pc=%llx word=%llx -> STOP\n",
       (unsigned long long)pc, (unsigned long long)word);
    c->undef_count++;
    c->pc = A64_STOP;
}

void a64_undef(A64Ctx* c, uint64_t pc, uint64_t word, const char* mn) {
    lg("[undef] pc=%llx word=%llx mn=%s -> STOP\n",
       (unsigned long long)pc, (unsigned long long)word, mn ? mn : "?");
    c->undef_count++;
    c->pc = A64_STOP;
}

void a64_unmapped(A64Ctx* c) {
    lg("[unmapped] pc=%llx not in fn table -> STOP (svc_count=%llu)\n",
       (unsigned long long)c->pc, (unsigned long long)c->svc_count);
    c->undef_count++;
    c->pc = A64_STOP;
}

void a64_mrs_unknown(A64Ctx* c, const char* name) {
    (void)c;
    static uint64_t n;
    if (n++ < 32) lg("[mrs] unknown sysreg %s\n", name);
}

void a64_msr_unknown(A64Ctx* c, const char* name, uint64_t val) {
    (void)c;
    static uint64_t n;
    if (n++ < 32) lg("[msr] unknown sysreg %s val=%llx\n",
                     name, (unsigned long long)val);
}

uint64_t a64_ticks(void) {
    static LARGE_INTEGER freq, start;
    static int init;
    LARGE_INTEGER now;
    if (!init) {
        QueryPerformanceFrequency(&freq);
        QueryPerformanceCounter(&start);
        init = 1;
    }
    QueryPerformanceCounter(&now);
    /* CNTVCT frequency is 19.2 MHz */
    return (uint64_t)((now.QuadPart - start.QuadPart) * 19200000ull / freq.QuadPart);
}

void a64_fcmp(A64Ctx* c, int size, double a, double b) {
    (void)size;
    if (isnan(a) || isnan(b)) { c->n = 0; c->z = 0; c->c = 1; c->v = 1; return; }
    if (a == b) { c->n = 0; c->z = 1; c->c = 1; c->v = 0; return; }
    if (a > b)  { c->n = 0; c->z = 0; c->c = 1; c->v = 0; return; }
    c->n = 0; c->z = 0; c->c = 0; c->v = 0;
}

/* rounding modes: 0=trunc 1=away 2=floor 3=nearest-even 4=ceil */
static double rnd(int mode, double v) {
    switch (mode) {
    case 0: return trunc(v);
    case 1: return round(v);
    case 2: return floor(v);
    case 3: return rint(v);
    default: return ceil(v);
    }
}

static int64_t f2i_s(double v, int mode) {
    if (isnan(v)) return 0;
    double r = rnd(mode, v);
    if (r >= 9223372036854775808.0) return INT64_MAX;
    if (r <= -9223372036854775808.0) return INT64_MIN;
    return (int64_t)r;
}

static uint64_t f2i_u(double v, int mode) {
    if (isnan(v)) return 0;
    double r = rnd(mode, v);
    if (r <= 0) return 0;
    if (r >= 18446744073709551616.0) return UINT64_MAX;
    return (uint64_t)r;
}

int64_t a64_f2i_z_s(int fsize, double v) { (void)fsize; return f2i_s(v, 0); }
int64_t a64_f2i_a_s(int fsize, double v) { (void)fsize; return f2i_s(v, 1); }
int64_t a64_f2i_m_s(int fsize, double v) { (void)fsize; return f2i_s(v, 2); }
int64_t a64_f2i_n_s(int fsize, double v) { (void)fsize; return f2i_s(v, 3); }
int64_t a64_f2i_p_s(int fsize, double v) { (void)fsize; return f2i_s(v, 4); }
uint64_t a64_f2i_z_u(int fsize, double v) { (void)fsize; return f2i_u(v, 0); }
uint64_t a64_f2i_a_u(int fsize, double v) { (void)fsize; return f2i_u(v, 1); }
uint64_t a64_f2i_m_u(int fsize, double v) { (void)fsize; return f2i_u(v, 2); }
uint64_t a64_f2i_n_u(int fsize, double v) { (void)fsize; return f2i_u(v, 3); }
uint64_t a64_f2i_p_u(int fsize, double v) { (void)fsize; return f2i_u(v, 4); }

double a64_i2f_s(int fbits, int ibits, uint64_t v) {
    (void)fbits;
    if (ibits == 32) return (double)(int32_t)(uint32_t)v;
    return (double)(int64_t)v;
}

double a64_i2f_u(int fbits, int ibits, uint64_t v) {
    (void)fbits;
    if (ibits == 32) return (double)(uint32_t)v;
    return (double)v;
}

uint32_t a64_crc32(const char* name, uint32_t a, uint32_t b) {
    (void)a; (void)b;
    static uint64_t seen;
    if (seen++ < 4) lg("[crc32] %s unimplemented -> 0\n", name);
    return 0;
}
