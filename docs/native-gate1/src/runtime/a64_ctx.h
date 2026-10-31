/* SplatNative runtime context + helpers for recompiled AArch64 code. */
#ifndef A64_CTX_H
#define A64_CTX_H

#include <stdint.h>
#include <math.h>

/* Stop pc: set by ExitProcess/Break, unmapped pc, or undef stops. */
#define A64_STOP      0xdead0000dead0000ull
/* Return sentinel for init_array invocations. */
#define A64_INIT_RET  0xdead0000dead0001ull

typedef struct A64Ctx {
    uint64_t x[32];      /* x[31] = SP */
    uint64_t pc;
    uint64_t tpidr, tpidrro, fpcr, fpsr;
    uint64_t d[32];      /* low 64 of Vn (D view) */
    uint64_t dhi[32];    /* high 64 of Vn (Q view) */
    uint8_t n, z, c, v;
    uint64_t svc_count;
    uint64_t undef_count;
} A64Ctx;

typedef void (*A64Fn)(A64Ctx*);
typedef struct FnEntry { uint64_t pc; A64Fn fn; } FnEntry;
typedef struct RecPatch { uint64_t off; uint64_t val; } RecPatch;
typedef struct HostPatch { uint64_t off; const char* name; } HostPatch;

extern const FnEntry g_fn_table[];
extern const unsigned g_fn_count;
extern const RecPatch g_patches[];
extern const unsigned g_patch_count;
extern const HostPatch g_host_patches[];
extern const unsigned g_host_patch_count;
extern const unsigned long long g_init_arrays[][2];
extern const unsigned long long g_entry;

void fn_dispatch(A64Ctx* c);
/* Returns 1 if pc was a host (runtime) function and ran it (see nso_loader.c). */
int host_dispatch(A64Ctx* c);

/* ---- logging / stop hooks (a64_helpers.c) ---- */
void a64_svc(A64Ctx* c, uint64_t imm);
void a64_break(A64Ctx* c, uint64_t pc, uint64_t word);
void a64_undef(A64Ctx* c, uint64_t pc, uint64_t word, const char* mn);
void a64_unmapped(A64Ctx* c);
void a64_mrs_unknown(A64Ctx* c, const char* name);
void a64_msr_unknown(A64Ctx* c, const char* name, uint64_t val);
uint64_t a64_ticks(void);
void a64_fcmp(A64Ctx* c, int size, double a, double b);
int64_t a64_f2i_z_s(int fsize, double v);
int64_t a64_f2i_a_s(int fsize, double v);
int64_t a64_f2i_m_s(int fsize, double v);
int64_t a64_f2i_n_s(int fsize, double v);
int64_t a64_f2i_p_s(int fsize, double v);
uint64_t a64_f2i_z_u(int fsize, double v);
uint64_t a64_f2i_a_u(int fsize, double v);
uint64_t a64_f2i_m_u(int fsize, double v);
uint64_t a64_f2i_n_u(int fsize, double v);
uint64_t a64_f2i_p_u(int fsize, double v);
double a64_i2f_s(int fbits, int ibits, uint64_t v);
double a64_i2f_u(int fbits, int ibits, uint64_t v);
uint32_t a64_crc32(const char* name, uint32_t a, uint32_t b);

/* ---- integer helpers ---- */
static inline int a64_cond(A64Ctx* c, int cond) {
    int r = 0;
    switch (cond & 15) {
    case 0:  r = c->z; break;                    /* EQ */
    case 1:  r = !c->z; break;                   /* NE */
    case 2:  r = c->c; break;                    /* CS/HS */
    case 3:  r = !c->c; break;                   /* CC/LO */
    case 4:  r = c->n; break;                    /* MI */
    case 5:  r = !c->n; break;                   /* PL */
    case 6:  r = c->v; break;                    /* VS */
    case 7:  r = !c->v; break;                   /* VC */
    case 8:  r = c->c && !c->z; break;           /* HI */
    case 9:  r = !c->c || c->z; break;           /* LS */
    case 10: r = c->n == c->v; break;            /* GE */
    case 11: r = c->n != c->v; break;            /* LT */
    case 12: r = c->n == c->v && !c->z; break;   /* GT */
    case 13: r = c->n != c->v || c->z; break;   /* LE */
    case 14: r = 1; break;                       /* AL */
    case 15: r = 1; break;                       /* NV (treat as AL) */
    }
    return r;
}

static inline uint64_t a64_lsl64(uint64_t v, uint64_t n) { return v << (n & 63); }
static inline uint32_t a64_lsl32(uint32_t v, uint32_t n) { return v << (n & 31); }
static inline uint64_t a64_lsr64(uint64_t v, uint64_t n) { return v >> (n & 63); }
static inline uint32_t a64_lsr32(uint32_t v, uint32_t n) { return v >> (n & 31); }
static inline uint64_t a64asr64(uint64_t v, uint64_t n) {
    return (uint64_t)((int64_t)v >> (n & 63));
}
static inline uint32_t a64asr32(uint32_t v, uint32_t n) {
    return (uint32_t)((int32_t)v >> (n & 31));
}
static inline uint64_t a64ror64(uint64_t v, uint64_t n) {
    n &= 63; if (!n) return v;
    return (v >> n) | (v << (64 - n));
}
static inline uint32_t a64ror32(uint32_t v, uint32_t n) {
    n &= 31; if (!n) return v;
    return (v >> n) | (v << (32 - n));
}
static inline uint64_t a64_udiv64(uint64_t a, uint64_t b) { return b ? a / b : 0; }
static inline uint32_t a64_udiv32(uint32_t a, uint32_t b) { return b ? a / b : 0; }
static inline uint64_t a64_sdiv64(uint64_t a, uint64_t b) {
    if (!b) return 0;
    int64_t x = (int64_t)a, y = (int64_t)b;
    if (x == INT64_MIN && y == -1) return (uint64_t)x;
    return (uint64_t)(x / y);
}
static inline uint32_t a64_sdiv32(uint32_t a, uint32_t b) {
    if (!b) return 0;
    int32_t x = (int32_t)a, y = (int32_t)b;
    if (x == INT32_MIN && y == -1) return (uint32_t)x;
    return (uint32_t)(x / y);
}
static inline uint64_t a64_smulh(uint64_t a, uint64_t b) {
    /* 64x64 signed high 64 */
    uint64_t aLo = a & 0xffffffffull, aHi = a >> 32;
    uint64_t bLo = b & 0xffffffffull, bHi = b >> 32;
    uint64_t p0 = aLo * bLo;
    uint64_t p1 = aLo * bHi;
    uint64_t p2 = aHi * bLo;
    uint64_t p3 = aHi * bHi;
    uint64_t mid = (p0 >> 32) + (p1 & 0xffffffffull) + (p2 & 0xffffffffull);
    uint64_t hi = p3 + (p1 >> 32) + (p2 >> 32) + (mid >> 32);
    /* correct for sign */
    if ((int64_t)a < 0) hi -= b;
    if ((int64_t)b < 0) hi -= a;
    return hi;
}
static inline uint64_t a64_umulh(uint64_t a, uint64_t b) {
    uint64_t aLo = a & 0xffffffffull, aHi = a >> 32;
    uint64_t bLo = b & 0xffffffffull, bHi = b >> 32;
    uint64_t p0 = aLo * bLo;
    uint64_t p1 = aLo * bHi;
    uint64_t p2 = aHi * bLo;
    uint64_t p3 = aHi * bHi;
    uint64_t mid = (p0 >> 32) + (p1 & 0xffffffffull) + (p2 & 0xffffffffull);
    return p3 + (p1 >> 32) + (p2 >> 32) + (mid >> 32);
}

/* ---- memory (guest image is mapped at its absolute load address) ---- */
static inline uint64_t ld8(A64Ctx* c, uint64_t a) {
    (void)c; return *(const uint8_t*)(uintptr_t)a;
}
static inline uint64_t ld16(A64Ctx* c, uint64_t a) {
    (void)c; return *(const uint16_t*)(uintptr_t)a;
}
static inline uint64_t ld32(A64Ctx* c, uint64_t a) {
    (void)c; return *(const uint32_t*)(uintptr_t)a;
}
static inline uint64_t ld64(A64Ctx* c, uint64_t a) {
    (void)c; return *(const uint64_t*)(uintptr_t)a;
}
static inline void st8(A64Ctx* c, uint64_t a, uint64_t v) {
    (void)c; *(uint8_t*)(uintptr_t)a = (uint8_t)v;
}
static inline void st16(A64Ctx* c, uint64_t a, uint64_t v) {
    (void)c; *(uint16_t*)(uintptr_t)a = (uint16_t)v;
}
static inline void st32(A64Ctx* c, uint64_t a, uint64_t v) {
    (void)c; *(uint32_t*)(uintptr_t)a = (uint32_t)v;
}
static inline void st64(A64Ctx* c, uint64_t a, uint64_t v) {
    (void)c; *(uint64_t*)(uintptr_t)a = v;
}

/* ---- float bit conversions ---- */
static inline float u32f(uint32_t b) { union { uint32_t u; float f; } x; x.u = b; return x.f; }
static inline double u64f(uint64_t b) { union { uint64_t u; double f; } x; x.u = b; return x.f; }
static inline uint32_t f32b(float f) { union { float f; uint32_t u; } x; x.f = f; return x.u; }
static inline uint64_t f64b(double f) { union { double f; uint64_t u; } x; x.f = f; return x.u; }

#define negf(x) (-(x))
#define negd(x) (-(x))
#define fabsd(x) fabs(x)
#define sqrtd(x) sqrt(x)

/* FP binary helpers emitted by the recompiler */
static inline float a64_fmax_32(float a, float b) { return (a > b) ? a : b; }
static inline double a64_fmax_64(double a, double b) { return (a > b) ? a : b; }
static inline float a64_fmin_32(float a, float b) { return (a < b) ? a : b; }
static inline double a64_fmin_64(double a, double b) { return (a < b) ? a : b; }
static inline float a64_fmaxnm_32(float a, float b) {
    if (a != a) return b; if (b != b) return a; return (a > b) ? a : b;
}
static inline double a64_fmaxnm_64(double a, double b) {
    if (a != a) return b; if (b != b) return a; return (a > b) ? a : b;
}
static inline float a64_fminnm_32(float a, float b) {
    if (a != a) return b; if (b != b) return a; return (a < b) ? a : b;
}
static inline double a64_fminnm_64(double a, double b) {
    if (a != a) return b; if (b != b) return a; return (a < b) ? a : b;
}
static inline float a64_fabd_32(float a, float b) { float d = a - b; return d < 0 ? -d : d; }
static inline double a64_fabd_64(double a, double b) { double d = a - b; return d < 0 ? -d : d; }

static inline float a64_fmadd_32(float a, float b, float c) { return fmaf(a, b, c); }
static inline double a64_fmadd_64(double a, double b, double c) { return fma(a, b, c); }
static inline float a64_fmsub_32(float a, float b, float c) { return fmaf(a, b, -c); }
static inline double a64_fmsub_64(double a, double b, double c) { return fma(a, b, -c); }
static inline float a64_fnmadd_32(float a, float b, float c) { return -fmaf(a, b, c); }
static inline double a64_fnmadd_64(double a, double b, double c) { return -fma(a, b, c); }
static inline float a64_fnmsub_32(float a, float b, float c) { return -fmaf(a, b, -c); }
static inline double a64_fnmsub_64(double a, double b, double c) { return -fma(a, b, -c); }

/* FRINT variants: n=nearest-even, p=ceil, m=floor, z=trunc, a=away, x/i=impl-defined
   (we use nearest-even, the FPCR default). */
static inline float a64_frintn_32(float v) { return rintf(v); }
static inline double a64_frintn_64(double v) { return rint(v); }
static inline float a64_frintp_32(float v) { return ceilf(v); }
static inline double a64_frintp_64(double v) { return ceil(v); }
static inline float a64_frintm_32(float v) { return floorf(v); }
static inline double a64_frintm_64(double v) { return floor(v); }
static inline float a64_frintz_32(float v) { return truncf(v); }
static inline double a64_frintz_64(double v) { return trunc(v); }
static inline float a64_frinta_32(float v) { return roundf(v); }   /* ties away */
static inline double a64_frinta_64(double v) { return round(v); }
static inline float a64_frintx_32(float v) { return rintf(v); }
static inline double a64_frintx_64(double v) { return rint(v); }
static inline float a64_frinti_32(float v) { return rintf(v); }
static inline double a64_frinti_64(double v) { return rint(v); }

#endif /* A64_CTX_H */
