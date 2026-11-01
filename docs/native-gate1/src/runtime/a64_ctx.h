/* SplatNative runtime context + helpers for recompiled AArch64 code. */
#ifndef A64_CTX_H
#define A64_CTX_H

#include <stdint.h>
#include <math.h>
#include <intrin.h>
#include <string.h>

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
/* Function-level call trace ring (fn_dispatch pushes pc; dumped on stop). */
extern uint64_t g_trace[1024];
extern unsigned g_trace_i;
void a64_dump_trace(void);
/* pc watch (fn_dispatch calls a64_hit_watch when pc == g_watch). */
extern uint64_t g_watch;
void a64_hit_watch(A64Ctx* c);
void a64_mrs_unknown(A64Ctx* c, const char* name);
void a64_msr_unknown(A64Ctx* c, const char* name, uint64_t val);
uint64_t a64_ticks(void);
void a64_fcmp(A64Ctx* c, int size, double a, double b);
int64_t a64_f2iz_s(int fsize, double v);
int64_t a64_f2ia_s(int fsize, double v);
int64_t a64_f2im_s(int fsize, double v);
int64_t a64_f2in_s(int fsize, double v);
int64_t a64_f2ip_s(int fsize, double v);
uint64_t a64_f2iz_u(int fsize, double v);
uint64_t a64_f2ia_u(int fsize, double v);
uint64_t a64_f2im_u(int fsize, double v);
uint64_t a64_f2in_u(int fsize, double v);
uint64_t a64_f2ip_u(int fsize, double v);
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

static inline uint64_t a64lsl64(uint64_t v, uint64_t n) { return v << (n & 63); }
static inline uint32_t a64lsl32(uint32_t v, uint32_t n) { return v << (n & 31); }
static inline uint64_t a64lsr64(uint64_t v, uint64_t n) { return v >> (n & 63); }
static inline uint32_t a64lsr32(uint32_t v, uint32_t n) { return v >> (n & 31); }
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

/* ---- bit ops ---- */
static inline uint32_t a64_clz32(uint32_t v) {
    unsigned long i;
    if (!_BitScanReverse(&i, (unsigned long)v)) return 32;
    return 31u - (uint32_t)i;
}
static inline uint64_t a64_clz64(uint64_t v) {
    unsigned long i;
    if (!_BitScanReverse64(&i, v)) return 64;
    return 63ull - (uint64_t)i;
}
static inline uint32_t a64_cls32(uint32_t v) {
    uint32_t msb = v >> 31, cnt = 0;
    for (int i = 30; i >= 0; i--) { if (((v >> i) & 1) == msb) cnt++; else break; }
    return cnt;
}
static inline uint64_t a64_cls64(uint64_t v) {
    uint64_t msb = v >> 63, cnt = 0;
    for (int i = 62; i >= 0; i--) { if (((v >> i) & 1) == msb) cnt++; else break; }
    return cnt;
}
static inline uint32_t a64_rbit32(uint32_t v) {
    uint32_t r = 0;
    for (int i = 0; i < 32; i++) { r = (r << 1) | (v & 1); v >>= 1; }
    return r;
}
static inline uint64_t a64_rbit64(uint64_t v) {
    uint64_t r = 0;
    for (int i = 0; i < 64; i++) { r = (r << 1) | (v & 1); v >>= 1; }
    return r;
}
static inline uint32_t a64_bswap32(uint32_t v) { return _byteswap_ulong(v); }
static inline uint64_t a64_bswap64(uint64_t v) { return _byteswap_uint64(v); }
static inline uint32_t a64_rev16_32(uint32_t v) {
    return ((v & 0x00ff00ffu) << 8) | ((v >> 8) & 0x00ff00ffu);
}
static inline uint64_t a64_rev16_64(uint64_t v) {
    const uint64_t m = 0x00ff00ff00ff00ffull;
    return ((v & m) << 8) | ((v >> 8) & m);
}
static inline uint64_t a64_rev32_64(uint64_t v) {
    return ((uint64_t)_byteswap_ulong((uint32_t)(v >> 32)) << 32) |
           (uint64_t)_byteswap_ulong((uint32_t)v);
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

/* ================= vector (Vn = two 64-bit halves) ================= */
typedef struct V128 { uint64_t lo, hi; } V128;

/* Lane access through memcpy: strict-aliasing-legal, constant sizes compile
   to direct moves. Declarations stay at block start (MSVC C mode). */
#define VBIN(NAME, T, EXPR) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y, v; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        v = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

#define VUN(NAME, T, EXPR) \
static inline V128 NAME(V128 a) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, v; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        v = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

/* ternary: z = lane of acc (first arg), x = a, y = b */
#define VTERN(NAME, T, EXPR) \
static inline V128 NAME(V128 acc, V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T z, x, y, v; \
        memcpy(&z, (const char*)&acc + i * sizeof(T), sizeof(T)); \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        v = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

/* compare -> all-ones/zero bit mask per lane (works for float too: mask is
   built as an integer then truncated into the lane). EXPR uses x,y. */
#define VCMP(NAME, T, EXPR) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y; uint64_t m; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        m = (EXPR) ? ~(uint64_t)0 : 0ull; \
        memcpy((char*)&r + i * sizeof(T), &m, sizeof(T)); \
    } \
    return r; }

/* shift by immediate n (scalar, same for every lane) */
#define VSHI(NAME, T, EXPR) \
static inline V128 NAME(V128 a, int n) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, v; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        v = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

/* two-source shift by immediate: SLI = (a << n) | (b >> (esize-n)),
   SRI = (b << (esize-n)) | (a >> n).  n in 0..esize-1; shifts of
   exactly esize are defined as 0 by the architecture (guarded here
   because C shifts by >= width are UB).  EXPR uses _yrs/_yls. */
#define VSHI2(NAME, T, EXPR) \
static inline V128 NAME(V128 a, V128 b, int n) { \
    V128 r; int i; int es = (int)(sizeof(T) * 8); \
    int _rs = es - n; \
    T _yrs = 0, _yls = 0; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y, v; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        _yrs = (_rs <= 0 || _rs >= es) ? (T)0 : (T)(y >> _rs); \
        _yls = (_rs <= 0 || _rs >= es) ? (T)0 : (T)(y << _rs); \
        v = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

/* shift by register: per-lane signed shift amount (negative = right). */
#define VSHRREG(NAME, T, TS, EXPR) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x; TS s; T v; int sh; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&s, (const char*)&b + i * sizeof(TS), sizeof(TS)); \
        sh = (int)s; \
        v = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

/* saturating helpers: T is the SIGNED lane type so memcpy reinterprets
   the two's-complement bits correctly. -------------------------------- */
#define VSAT_SIGNED(NAME, T, LO, HI) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y, v; long long t; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        t = (long long)x + (long long)y; \
        v = (T)(t < (long long)(LO) ? (LO) : (t > (long long)(HI) ? (HI) : t)); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

#define VSAT_SIGNED_SUB(NAME, T, LO, HI) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y, v; long long t; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        t = (long long)x - (long long)y; \
        v = (T)(t < (long long)(LO) ? (LO) : (t > (long long)(HI) ? (HI) : t)); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

#define VSAT_UNSIGNED(NAME, T, HI) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y, v; unsigned long long t; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        t = (unsigned long long)x + (unsigned long long)y; \
        v = (T)(t > (unsigned long long)(HI) ? (HI) : t); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

#define VSAT_UNSIGNED_SUB(NAME, T) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, y, v; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + i * sizeof(T), sizeof(T)); \
        v = (T)(x < y ? 0 : (T)(x - y)); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }

/* ---- FP element-wise ---- */
VBIN(v_fadd_f32, float,  x + y)
VBIN(v_fsub_f32, float,  x - y)
VBIN(v_fmul_f32, float,  x * y)
VBIN(v_fdiv_f32, float,  x / y)
VBIN(v_fmax_f32, float,  a64_fmax_32(x, y))
VBIN(v_fmin_f32, float,  a64_fmin_32(x, y))
VBIN(v_fmaxnm_f32, float, a64_fmaxnm_32(x, y))
VBIN(v_fminnm_f32, float, a64_fminnm_32(x, y))
VBIN(v_fabd_f32, float,  a64_fabd_32(x, y))
VBIN(v_fadd_f64, double, x + y)
VBIN(v_fsub_f64, double, x - y)
VBIN(v_fmul_f64, double, x * y)
VBIN(v_fdiv_f64, double, x / y)
VBIN(v_fmax_f64, double, a64_fmax_64(x, y))
VBIN(v_fmin_f64, double, a64_fmin_64(x, y))
VBIN(v_fmaxnm_f64, double, a64_fmaxnm_64(x, y))
VBIN(v_fminnm_f64, double, a64_fminnm_64(x, y))
VBIN(v_fabd_f64, double, a64_fabd_64(x, y))
VUN(v_fneg_f32, float,  -x)
VUN(v_fabs_f32, float,  fabsf(x))
VUN(v_fsqrt_f32, float, sqrtf(x))
VUN(v_fneg_f64, double, -x)
VUN(v_fabs_f64, double, fabs(x))
VUN(v_fsqrt_f64, double, sqrt(x))
VCMP(v_fcmeq_f32, float,  x == y)
VCMP(v_fcmeq_f64, double, x == y)
VCMP(v_fcmge_f32, float,  x >= y)
VCMP(v_fcmge_f64, double, x >= y)
VCMP(v_fcmgt_f32, float,  x > y)
VCMP(v_fcmgt_f64, double, x > y)
VCMP(v_fcmle_f32, float,  x <= y)
VCMP(v_fcmle_f64, double, x <= y)
VCMP(v_fcmlt_f32, float,  x < y)
VCMP(v_fcmlt_f64, double, x < y)
/* FMLA/FMLS are fused (single rounding) on ARM: use fma/fmaf directly. */
VTERN(v_fmla_f32, float,  fmaf(x, y, z))
VTERN(v_fmla_f64, double, fma(x, y, z))
/* integer multiply-accumulate / multiply-subtract (wrapping): acc +/- a*b */
VTERN(v_mla_u8,  uint8_t,  z + x * y)
VTERN(v_mla_u16, uint16_t, z + x * y)
VTERN(v_mla_u32, uint32_t, z + x * y)
VTERN(v_mla_u64, uint64_t, z + x * y)
VTERN(v_mls_u8,  uint8_t,  z - x * y)
VTERN(v_mls_u16, uint16_t, z - x * y)
VTERN(v_mls_u32, uint32_t, z - x * y)
VTERN(v_mls_u64, uint64_t, z - x * y)
VTERN(v_fmls_f32, float,  fmaf(-x, y, z))
VTERN(v_fmls_f64, double, fma(-x, y, z))
/* fused estimate helpers: FRSQRTS = 3 - a*b, FRECPS = 2 - a*b */
VBIN(v_frsqrts_f32, float,  3.0f - x * y)
VBIN(v_frsqrts_f64, double, 3.0 - x * y)
VBIN(v_frecps_f32, float,  2.0f - x * y)
VBIN(v_frecps_f64, double, 2.0 - x * y)

/* ---- integer element-wise (wrapping: unsigned types avoid C signed
   overflow UB; add/sub/mul are bit-identical for signedness) ---- */
VBIN(v_add_u8,  uint8_t,  (uint8_t)(x + y))
VBIN(v_add_u16, uint16_t, (uint16_t)(x + y))
VBIN(v_add_u32, uint32_t, (uint32_t)(x + y))
VBIN(v_add_u64, uint64_t, (uint64_t)(x + y))
VBIN(v_sub_u8,  uint8_t,  (uint8_t)(x - y))
VBIN(v_sub_u16, uint16_t, (uint16_t)(x - y))
VBIN(v_sub_u32, uint32_t, (uint32_t)(x - y))
VBIN(v_sub_u64, uint64_t, (uint64_t)(x - y))
VBIN(v_mul_u8,  uint8_t,  (uint8_t)(x * y))
VBIN(v_mul_u16, uint16_t, (uint16_t)(x * y))
VBIN(v_mul_u32, uint32_t, (uint32_t)(x * y))

/* ---- whole-register bitwise ---- */
static inline V128 v_and(V128 a, V128 b) {
    V128 r; r.lo = a.lo & b.lo; r.hi = a.hi & b.hi; return r; }
static inline V128 v_orr(V128 a, V128 b) {
    V128 r; r.lo = a.lo | b.lo; r.hi = a.hi | b.hi; return r; }
static inline V128 v_eor(V128 a, V128 b) {
    V128 r; r.lo = a.lo ^ b.lo; r.hi = a.hi ^ b.hi; return r; }
static inline V128 v_bic(V128 a, V128 b) {
    V128 r; r.lo = a.lo & ~b.lo; r.hi = a.hi & ~b.hi; return r; }
static inline V128 v_orn(V128 a, V128 b) {
    V128 r; r.lo = a.lo | ~b.lo; r.hi = a.hi | ~b.hi; return r; }
static inline V128 v_not(V128 a) {
    V128 r; r.lo = ~a.lo; r.hi = ~a.hi; return r; }

/* Bit select, QEMU do_bitsel semantics (mask first):
   BSL: mask = old Vd, true = Vn, false = Vm.
   BIT: mask = Vm,    true = Vn, false = old Vd.
   BIF: mask = Vm,    true = old Vd, false = Vn. */
static inline V128 v_bsl(V128 mask, V128 a, V128 b) {
    V128 r; r.lo = (mask.lo & a.lo) | (~mask.lo & b.lo);
    r.hi = (mask.hi & a.hi) | (~mask.hi & b.hi); return r; }

/* ---- integer compares -> per-lane mask ---- */
VCMP(v_cmeq_u8,  uint8_t,  x == y)
VCMP(v_cmeq_u16, uint16_t, x == y)
VCMP(v_cmeq_u32, uint32_t, x == y)
VCMP(v_cmeq_u64, uint64_t, x == y)
VCMP(v_cmgt_u8,  uint8_t,  (int8_t)x > (int8_t)y)
VCMP(v_cmgt_u16, uint16_t, (int16_t)x > (int16_t)y)
VCMP(v_cmgt_u32, uint32_t, (int32_t)x > (int32_t)y)
VCMP(v_cmgt_u64, uint64_t, (int64_t)x > (int64_t)y)
VCMP(v_cmgt_unsigned_u8,  uint8_t,  x > y)
VCMP(v_cmgt_unsigned_u16, uint16_t, x > y)
VCMP(v_cmgt_unsigned_u32, uint32_t, x > y)
VCMP(v_cmgt_unsigned_u64, uint64_t, x > y)
VCMP(v_cmhi_u8,  uint8_t,  x > y)
VCMP(v_cmhi_u16, uint16_t, x > y)
VCMP(v_cmhi_u32, uint32_t, x > y)
VCMP(v_cmhi_u64, uint64_t, x > y)
VCMP(v_cmlt_u8,  uint8_t,  (int8_t)x < 0)
VCMP(v_cmlt_u16, uint16_t, (int16_t)x < 0)
VCMP(v_cmlt_u32, uint32_t, (int32_t)x < 0)
VCMP(v_cmlt_u64, uint64_t, (int64_t)x < 0)
VCMP(v_cmtst_u8,  uint8_t,  (x & y) != 0)
VCMP(v_cmtst_u16, uint16_t, (x & y) != 0)
VCMP(v_cmtst_u32, uint32_t, (x & y) != 0)
VCMP(v_cmtst_u64, uint64_t, (x & y) != 0)

/* signedness-independent min/max on two's-complement lanes */
VBIN(v_smin_u8,  uint8_t,  (uint8_t)(((int8_t)x < (int8_t)y) ? x : y))
VBIN(v_smin_u16, uint16_t, (uint16_t)(((int16_t)x < (int16_t)y) ? x : y))
VBIN(v_smin_u32, uint32_t, (uint32_t)(((int32_t)x < (int32_t)y) ? x : y))
VBIN(v_smax_u8,  uint8_t,  (uint8_t)(((int8_t)x > (int8_t)y) ? x : y))
VBIN(v_smax_u16, uint16_t, (uint16_t)(((int16_t)x > (int16_t)y) ? x : y))
VBIN(v_smax_u32, uint32_t, (uint32_t)(((int32_t)x > (int32_t)y) ? x : y))
VBIN(v_umin_u8,  uint8_t,  (x < y) ? x : y)
VBIN(v_umin_u16, uint16_t, (x < y) ? x : y)
VBIN(v_umin_u32, uint32_t, (x < y) ? x : y)
VBIN(v_umin_u64, uint64_t, (x < y) ? x : y)
VBIN(v_umax_u8,  uint8_t,  (x > y) ? x : y)
VBIN(v_umax_u16, uint16_t, (x > y) ? x : y)
VBIN(v_umax_u32, uint32_t, (x > y) ? x : y)
VBIN(v_umax_u64, uint64_t, (x > y) ? x : y)

/* ---- shifts by immediate: n is lane count-derived in the emitter ---- */
VSHI(v_shl_u8,  uint8_t,  (uint8_t)(x << (n & 7)))
VSHI(v_shl_u16, uint16_t, (uint16_t)(x << (n & 15)))
VSHI(v_shl_u32, uint32_t, (uint32_t)(x << (n & 31)))
VSHI(v_shl_u64, uint64_t, (uint64_t)(x << (n & 63)))
VSHI(v_ushr_u8,  uint8_t,  (uint8_t)(x >> (n & 7)))
VSHI(v_ushr_u16, uint16_t, (uint16_t)(x >> (n & 15)))
VSHI(v_ushr_u32, uint32_t, (uint32_t)(x >> (n & 31)))
VSHI(v_ushr_u64, uint64_t, (uint64_t)(x >> (n & 63)))
VSHI(v_sshr_u8,  uint8_t,  (uint8_t)((int8_t)x >> (n & 7)))
VSHI(v_sshr_u16, uint16_t, (uint16_t)((int16_t)x >> (n & 15)))
VSHI(v_sshr_u32, uint32_t, (uint32_t)((int32_t)x >> (n & 31)))
VSHI(v_sshr_u64, uint64_t, (uint64_t)((int64_t)x >> (n & 63)))
/* SRSHR: rounded arithmetic shift right by n (n = architectural shift,
   verified vs Unicorn).  n >= lane bits yields 0 for every input; the
   explicit branch avoids C shift-by-width UB in that case. */
#define VSRSHR(NAME, T, ST, BITS) \
static inline V128 NAME(V128 a, int n) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T x, v; ST s; \
        memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        s = (ST)x; \
        if (n <= 0) v = x; \
        else if (n >= (BITS)) v = (T)0; \
        else v = (T)((s + ((ST)1 << (n - 1))) >> n); \
        memcpy((char*)&r + i * sizeof(T), &v, sizeof(T)); \
    } \
    return r; }
VSRSHR(v_srshr_u8,  uint8_t,  int8_t,  8)
VSRSHR(v_srshr_u16, uint16_t, int16_t, 16)
VSRSHR(v_srshr_u32, uint32_t, int32_t, 32)
VSRSHR(v_srshr_u64, uint64_t, int64_t, 64)
/* SLI/SRI (insert shifted source into the other operand's complement) */
VSHI2(v_sli_u8,  uint8_t,  (x << n) | _yrs)
VSHI2(v_sli_u16, uint16_t, (x << n) | _yrs)
VSHI2(v_sli_u32, uint32_t, (x << n) | _yrs)
VSHI2(v_sli_u64, uint64_t, (x << n) | _yrs)
VSHI2(v_sri_u8,  uint8_t,  (x >> n) | _yls)
VSHI2(v_sri_u16, uint16_t, (x >> n) | _yls)
VSHI2(v_sri_u32, uint32_t, (x >> n) | _yls)
VSHI2(v_sri_u64, uint64_t, (x >> n) | _yls)
/* SSRA/USRA/SRSRA/URSRA: shift Vn right by n then accumulate into Vd
   (wrapping, no saturation).  RND is the round-to-nearest increment for the
   SRSRA/URSRA pair (0 for the plain forms); BIG is the n >= lane-bits
   result: arithmetic -1/0 for ssra, 0 for the rounded and logical forms. */
#define VSSRA(NAME, T, ST, BITS, RND, BIG) \
static inline V128 NAME(V128 d, V128 a, int n) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T xv, dv; ST s; \
        memcpy(&xv, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&dv, (const char*)&d + i * sizeof(T), sizeof(T)); \
        if (n <= 0) s = (ST)xv; \
        else if (n >= (BITS)) s = (BIG); \
        else s = (ST)(((ST)xv + (RND)) >> n); \
        dv = (T)((ST)dv + s); \
        memcpy((char*)&r + i * sizeof(T), &dv, sizeof(T)); \
    } \
    return r; }
VSSRA(v_ssra_u8,   uint8_t,  int8_t,  8,  0, (int8_t)(((int8_t)xv) < 0 ? -1 : 0))
VSSRA(v_ssra_u16,  uint16_t, int16_t, 16, 0, (int16_t)(((int16_t)xv) < 0 ? -1 : 0))
VSSRA(v_ssra_u32,  uint32_t, int32_t, 32, 0, (int32_t)(((int32_t)xv) < 0 ? -1 : 0))
VSSRA(v_ssra_u64,  uint64_t, int64_t, 64, 0, (int64_t)(((int64_t)xv) < 0 ? -1 : 0))
VSSRA(v_srsra_u8,  uint8_t,  int8_t,  8,  (int8_t)1 << ((n - 1) & 7),  (int8_t)0)
VSSRA(v_srsra_u16, uint16_t, int16_t, 16, (int16_t)1 << ((n - 1) & 15), (int16_t)0)
VSSRA(v_srsra_u32, uint32_t, int32_t, 32, (int32_t)1 << ((n - 1) & 31), (int32_t)0)
VSSRA(v_srsra_u64, uint64_t, int64_t, 64, (int64_t)1 << ((n - 1) & 63), (int64_t)0)
#define VUSRA(NAME, T, BITS, RND, BIG) \
static inline V128 NAME(V128 d, V128 a, int n) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(T)); i++) { \
        T xv, dv, s; \
        memcpy(&xv, (const char*)&a + i * sizeof(T), sizeof(T)); \
        memcpy(&dv, (const char*)&d + i * sizeof(T), sizeof(T)); \
        if (n <= 0) s = xv; \
        else if (n >= (BITS)) s = (BIG); \
        else s = (T)((xv + (RND)) >> n); \
        dv = (T)(dv + s); \
        memcpy((char*)&r + i * sizeof(T), &dv, sizeof(T)); \
    } \
    return r; }
VUSRA(v_usra_u8,   uint8_t,  8,  0, (uint8_t)0)
VUSRA(v_usra_u16,  uint16_t, 16, 0, (uint16_t)0)
VUSRA(v_usra_u32,  uint32_t, 32, 0, (uint32_t)0)
VUSRA(v_usra_u64,  uint64_t, 64, 0, (uint64_t)0)
VUSRA(v_ursra_u8,  uint8_t,  8,  (uint8_t)1 << ((n - 1) & 7),  (uint8_t)0)
VUSRA(v_ursra_u16, uint16_t, 16, (uint16_t)1 << ((n - 1) & 15), (uint16_t)0)
VUSRA(v_ursra_u32, uint32_t, 32, (uint32_t)1 << ((n - 1) & 31), (uint32_t)0)
VUSRA(v_ursra_u64, uint64_t, 64, (uint64_t)1 << ((n - 1) & 63), (uint64_t)0)

/* shift-by-register: sh >= 0 left, sh < 0 logical (USHL) or
   arithmetic (SSHL) right.  TS is the signed shift-lane type. */
VSHRREG(v_ushl_u8,  uint8_t,  int8_t,  sh < 0 ? (x >> (-sh & 7)) : (x << (sh & 7)))
VSHRREG(v_ushl_u16, uint16_t, int16_t, sh < 0 ? (x >> (-sh & 15)) : (x << (sh & 15)))
VSHRREG(v_ushl_u32, uint32_t, int32_t, sh < 0 ? (x >> (-sh & 31)) : (x << (sh & 31)))
VSHRREG(v_ushl_u64, uint64_t, int64_t, sh < 0 ? (x >> (-sh & 63)) : (x << (sh & 63)))
VSHRREG(v_sshl_u8,  uint8_t,  int8_t,  sh < 0 ? ((int8_t)x >> (-sh & 7)) : (x << (sh & 7)))
VSHRREG(v_sshl_u16, uint16_t, int16_t, sh < 0 ? ((int16_t)x >> (-sh & 15)) : (x << (sh & 15)))
VSHRREG(v_sshl_u32, uint32_t, int32_t, sh < 0 ? ((int32_t)x >> (-sh & 31)) : (x << (sh & 31)))
VSHRREG(v_sshl_u64, uint64_t, int64_t, sh < 0 ? ((int64_t)x >> (-sh & 63)) : (x << (sh & 63)))

/* ---- saturating add/sub ---- */
VSAT_SIGNED(v_sqadd_u8,  int8_t,  -128, 127)
VSAT_SIGNED(v_sqadd_u16, int16_t, -32768, 32767)
VSAT_SIGNED(v_sqadd_u32, int32_t, INT32_MIN, INT32_MAX)
VSAT_SIGNED_SUB(v_sqsub_u8,  int8_t,  -128, 127)
VSAT_SIGNED_SUB(v_sqsub_u16, int16_t, -32768, 32767)
VSAT_SIGNED_SUB(v_sqsub_u32, int32_t, INT32_MIN, INT32_MAX)
VSAT_UNSIGNED(v_uqadd_u8,  uint8_t,  255)
VSAT_UNSIGNED(v_uqadd_u16, uint16_t, 65535)
VSAT_UNSIGNED(v_uqadd_u32, uint32_t, 0xffffffffu)
VSAT_UNSIGNED_SUB(v_uqsub_u8,  uint8_t)
VSAT_UNSIGNED_SUB(v_uqsub_u16, uint16_t)
VSAT_UNSIGNED_SUB(v_uqsub_u32, uint32_t)

/* ---- halving add (rounding: (a + b + 1) >> 1) ---- */
VBIN(v_shadd_u8,  uint8_t,  (uint8_t)(((uint16_t)x + y + 1) >> 1))
VBIN(v_shadd_u16, uint16_t, (uint16_t)(((uint32_t)x + y + 1) >> 1))
VBIN(v_shadd_u32, uint32_t, (uint32_t)(((uint64_t)x + y + 1) >> 1))
VBIN(v_uhadd_u8,  uint8_t,  (uint8_t)(((uint16_t)x + y) >> 1))
VBIN(v_uhadd_u16, uint16_t, (uint16_t)(((uint32_t)x + y) >> 1))
VBIN(v_uhadd_u32, uint32_t, (uint32_t)(((uint64_t)x + y) >> 1))

/* ---- doubling multiply high half.  Computed in 64-bit; the only
   doubling-overflow cases (MIN*MIN -> 2^63) saturate per ARM. ---- */
static inline V128 v_sqdmulh_u16(V128 a, V128 b) {
    V128 r; int i;
    for (i = 0; i < 8; i++) {
        int16_t x, y; int64_t t; uint16_t v;
        memcpy(&x, (const char*)&a + i * 2, 2);
        memcpy(&y, (const char*)&b + i * 2, 2);
        t = 2 * (int64_t)x * (int64_t)y;
        t >>= 16;
        if (t > 32767) t = 32767;
        if (t < -32768) t = -32768;
        v = (uint16_t)(int16_t)t;
        memcpy((char*)&r + i * 2, &v, 2);
    }
    return r; }
static inline V128 v_sqdmulh_u32(V128 a, V128 b) {
    V128 r; int i;
    for (i = 0; i < 4; i++) {
        int32_t x, y; int64_t t, p; uint32_t v;
        memcpy(&x, (const char*)&a + i * 4, 4);
        memcpy(&y, (const char*)&b + i * 4, 4);
        p = (int64_t)x * (int64_t)y;
        if (p == (int64_t)0x4000000000000000ll) { /* (-2^31)^2: doubled overflows */
            v = 0x7fffffffu;
        } else {
            t = 2 * p; t >>= 32;
            if (t > INT32_MAX) t = INT32_MAX;
            if (t < INT32_MIN) t = INT32_MIN;
            v = (uint32_t)(int32_t)t;
        }
        memcpy((char*)&r + i * 4, &v, 4);
    }
    return r; }
static inline V128 v_sqrdmulh_u16(V128 a, V128 b) {
    V128 r; int i;
    for (i = 0; i < 8; i++) {
        int16_t x, y; int64_t t; uint16_t v;
        memcpy(&x, (const char*)&a + i * 2, 2);
        memcpy(&y, (const char*)&b + i * 2, 2);
        t = 2 * (int64_t)x * (int64_t)y + 0x8000;
        t >>= 16;
        if (t > 32767) t = 32767;
        if (t < -32768) t = -32768;
        v = (uint16_t)(int16_t)t;
        memcpy((char*)&r + i * 2, &v, 2);
    }
    return r; }
static inline V128 v_sqrdmulh_u32(V128 a, V128 b) {
    V128 r; int i;
    for (i = 0; i < 4; i++) {
        int32_t x, y; int64_t t, p; uint32_t v;
        memcpy(&x, (const char*)&a + i * 4, 4);
        memcpy(&y, (const char*)&b + i * 4, 4);
        p = (int64_t)x * (int64_t)y;
        if (p == (int64_t)0x4000000000000000ll) {
            v = 0x7fffffffu;
        } else {
            t = 2 * p + 0x80000000ll; t >>= 32;
            if (t > INT32_MAX) t = INT32_MAX;
            if (t < INT32_MIN) t = INT32_MIN;
            v = (uint32_t)(int32_t)t;
        }
        memcpy((char*)&r + i * 4, &v, 4);
    }
    return r; }

/* ---- widening: 16->32, 32->64, 8->16 (per source element) ---- */
#define VWIDE(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 a) { \
    V128 r; int i; int n = (int)(16 / sizeof(TD)); \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x; TD v = 0; \
        if (i < (int)(16 / sizeof(TS))) \
            memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        else x = 0; \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VWIDE(v_sshll_u16_u8,  int16_t,  int8_t,   x)
VWIDE(v_sshll_u32_u16, int32_t,  int16_t,  x)
VWIDE(v_sshll_u64_u32, int64_t,  int32_t,  x)
VWIDE(v_ushll_u16_u8,  uint16_t, uint8_t,  x)
VWIDE(v_ushll_u32_u16, uint32_t, uint16_t, x)
VWIDE(v_ushll_u64_u32, uint64_t, uint32_t, x)
/* shifted variants (ushll/sshll/shll have an immediate left shift) */
#define VWIDE_SHIFT(NAME, TD, TS, EXPR, SH) \
static inline V128 NAME(V128 a, int sh) { \
    V128 r; int i; int n = (int)(16 / sizeof(TD)); \
    (void)SH; r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x; TD v = 0; \
        if (i < (int)(16 / sizeof(TS))) \
            memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        else x = 0; \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VWIDE_SHIFT(v_sshlll_u16_u8,  int16_t,  int8_t,   (int16_t)((uint16_t)((int16_t)x << sh)), 0)
VWIDE_SHIFT(v_sshlll_u32_u16, int32_t,  int16_t,  (int32_t)((uint32_t)((int32_t)x << sh)), 0)
VWIDE_SHIFT(v_sshlll_u64_u32, int64_t,  int32_t,  (int64_t)((uint64_t)((int64_t)x << sh)), 0)
VWIDE_SHIFT(v_ushlll_u16_u8,  uint16_t, uint8_t,  (uint16_t)((uint16_t)x << sh), 0)
VWIDE_SHIFT(v_ushlll_u32_u16, uint32_t, uint16_t, (uint32_t)((uint32_t)x << sh), 0)
VWIDE_SHIFT(v_ushlll_u64_u32, uint64_t, uint32_t, (uint64_t)((uint64_t)x << sh), 0)
/* widening add: source lanes are read as half-width and summed to full */
#define VWADD(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; int n = (int)(16 / sizeof(TD)); \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x, y; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        memcpy(&y, (const char*)&b + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VWADD(v_saddl_u32_u16,  int32_t,  int16_t,  (int32_t)x + y)
VWADD(v_saddl_u64_u32,  int64_t,  int32_t,  (int64_t)x + y)
VWADD(v_uaddl_u32_u16,  uint32_t, uint16_t, (uint32_t)x + y)
VWADD(v_uaddl_u64_u32,  uint64_t, uint32_t, (uint64_t)x + y)

/* wide add: first source has FULL-width dest lanes, second is narrow */
#define VWADD2(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; int n = (int)(16 / sizeof(TD)); \
    for (i = 0; i < n; i++) { \
        TD x; TS y; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TD), sizeof(TD)); \
        if (i < (int)(16 / sizeof(TS))) \
            memcpy(&y, (const char*)&b + i * sizeof(TS), sizeof(TS)); \
        else y = 0; \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VWADD2(v_saddw_u16_u8,   int16_t,  int8_t,   x + y)
VWADD2(v_saddw_u32_u16,  int32_t,  int16_t,  x + (int32_t)y)
VWADD2(v_saddw_u64_u32,  int64_t,  int32_t,  x + (int64_t)y)
VWADD2(v_uaddw_u16_u8,   uint16_t, uint8_t,  x + y)
VWADD2(v_uaddw_u32_u16,  uint32_t, uint16_t, x + (uint32_t)y)
VWADD2(v_uaddw_u64_u32,  uint64_t, uint32_t, x + (uint64_t)y)

/* widening multiply: both sources narrow (TS), dest full (TD) */
#define VWML(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 a, V128 b) { \
    V128 r; int i; int n = (int)(16 / sizeof(TD)); \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n && i < (int)(16 / sizeof(TS)); i++) { \
        TS x, y; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        memcpy(&y, (const char*)&b + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VWML(v_smull_u16_u8,  uint16_t, int8_t,   (int16_t)x * y)
VWML(v_smull_u32_u16, uint32_t, int16_t,  (int32_t)x * y)
VWML(v_smull_u64_u32, uint64_t, int32_t,  (int64_t)x * y)
VWML(v_umull_u16_u8,  uint16_t, uint8_t,  (uint16_t)x * y)
VWML(v_umull_u32_u16, uint32_t, uint16_t, (uint32_t)x * y)
VWML(v_umull_u64_u32, uint64_t, uint32_t, (uint64_t)x * y)

/* widening multiply-accumulate: acc (TD) += narrow product (TS) */
#define VWMLACC(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 acc, V128 a, V128 b) { \
    V128 r; int i; int n = (int)(16 / sizeof(TD)); \
    for (i = 0; i < n && i < (int)(16 / sizeof(TS)); i++) { \
        TD z; TS x, y; TD v; \
        memcpy(&z, (const char*)&acc + i * sizeof(TD), sizeof(TD)); \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        memcpy(&y, (const char*)&b + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    for (; i < n; i++) { \
        TD z; \
        memcpy(&z, (const char*)&acc + i * sizeof(TD), sizeof(TD)); \
        memcpy((char*)&r + i * sizeof(TD), &z, sizeof(TD)); \
    } \
    return r; }

VWMLACC(v_smlal_u16_u8,  int16_t,  int8_t,   z + (int32_t)x * y)
VWMLACC(v_smlal_u32_u16, int32_t,  int16_t,  z + (int32_t)x * y)
VWMLACC(v_smlal_u64_u32, int64_t,  int32_t,  z + (int64_t)x * y)
VWMLACC(v_umlal_u16_u8,  uint16_t, uint8_t,  z + (uint32_t)x * y)
VWMLACC(v_umlal_u32_u16, uint32_t, uint16_t, z + (uint32_t)x * y)
VWMLACC(v_umlal_u64_u32, uint64_t, uint32_t, z + (uint64_t)x * y)

/* ---- narrowing: full-width in (TS), half-width out.  The loop runs over
   SOURCE lanes so the result always lands in the low 8 bytes (dest Q=0,
   or the emitter's placement for the *2 forms). ---- */
#define VNARROW(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 a) { \
    V128 r; int i; int n = (int)(16 / sizeof(TS)); \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VNARROW(v_xtn_u8_u16,   uint8_t,  uint16_t, (uint8_t)x)
VNARROW(v_xtn_u16_u32,  uint16_t, uint32_t, (uint16_t)x)
VNARROW(v_xtn_u32_u64,  uint32_t, uint64_t, (uint32_t)x)
VNARROW(v_sqxtn_u8_u16, uint8_t,  uint16_t,
        (uint8_t)((int16_t)x < -128 ? -128 : ((int16_t)x > 127 ? 127 : x)))
VNARROW(v_sqxtn_u16_u32, uint16_t, uint32_t,
        (uint16_t)((int32_t)x < -32768 ? -32768 : ((int32_t)x > 32767 ? 32767 : x)))
VNARROW(v_sqxtn_u32_u64, uint32_t, uint64_t,
        (uint32_t)((int64_t)x < INT32_MIN ? (uint32_t)INT32_MIN :
                   ((int64_t)x > INT32_MAX ? (uint32_t)INT32_MAX : x)))
VNARROW(v_uqxtn_u8_u16,  uint8_t,  uint16_t, (uint8_t)(x > 255 ? 255 : x))
VNARROW(v_uqxtn_u16_u32, uint16_t, uint32_t, (uint16_t)(x > 65535 ? 65535 : x))
VNARROW(v_uqxtn_u32_u64, uint32_t, uint64_t, (uint32_t)(x > 0xffffffffull ? 0xffffffffu : x))
/* SQXTUN: signed input -> unsigned saturated output */
VNARROW(v_sqxtun_u8_u16,  uint8_t,  int16_t,
        (uint8_t)(x < 0 ? 0 : (x > 255 ? 255 : x)))
VNARROW(v_sqxtun_u16_u32, uint16_t, int32_t,
        (uint16_t)(x < 0 ? 0 : (x > 65535 ? 65535 : x)))
VNARROW(v_sqxtun_u32_u64, uint32_t, int64_t,
        (uint32_t)(x < 0 ? 0 : (x > 0xffffffffll ? 0xffffffffu : x)))
/* rounded+sat narrowing: SQSHRN (RND=0, truncating) and SQRSHRN (RND=1). */
#define VNARROW_SHIFT(NAME, TD, TS, SATLO, SATHI, RND) \
static inline V128 NAME(V128 a, int sh) { \
    V128 r; int i; int n = (int)(16 / sizeof(TS)); \
    long long rnd = (RND && sh) ? (1ll << (sh - 1)) : 0; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x; TD v; long long t; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        t = sh ? (((long long)x + rnd) >> sh) : (long long)x; \
        t = t < (SATLO) ? (SATLO) : (t > (SATHI) ? (SATHI) : t); \
        v = (TD)t; \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VNARROW_SHIFT(v_sqshrn_u8_u16,  uint8_t,  uint16_t, -128, 127, 0)
VNARROW_SHIFT(v_sqshrn_u16_u32, uint16_t, uint32_t, -32768, 32767, 0)
VNARROW_SHIFT(v_sqshrn_u32_u64, uint32_t, uint64_t, INT32_MIN, INT32_MAX, 0)
VNARROW_SHIFT(v_sqrshrn_u8_u16,  uint8_t,  uint16_t, -128, 127, 1)
VNARROW_SHIFT(v_sqrshrn_u16_u32, uint16_t, uint32_t, -32768, 32767, 1)
VNARROW_SHIFT(v_sqrshrn_u32_u64, uint32_t, uint64_t, INT32_MIN, INT32_MAX, 1)
VNARROW_SHIFT(v_uqshrn_u8_u16,  uint8_t,  uint16_t, 0, 255, 0)
VNARROW_SHIFT(v_uqshrn_u16_u32, uint16_t, uint32_t, 0, 65535, 0)
VNARROW_SHIFT(v_uqshrn_u32_u64, uint32_t, uint64_t, 0ll, 0xffffffffll, 0)
VNARROW_SHIFT(v_uqrshrn_u8_u16,  uint8_t,  uint16_t, 0, 255, 1)
VNARROW_SHIFT(v_uqrshrn_u16_u32, uint16_t, uint32_t, 0, 65535, 1)
VNARROW_SHIFT(v_uqrshrn_u32_u64, uint32_t, uint64_t, 0ll, 0xffffffffll, 1)

/* signed -> unsigned narrowing shift (SQSHRUN/SQRSHRUN): the shifted value
   is the signed product, negatives saturate to 0 and positives saturate to
   the unsigned max. */
VNARROW_SHIFT(v_sqshrun_u8_u16,  uint8_t,  uint16_t, 0, 255, 0)
VNARROW_SHIFT(v_sqshrun_u16_u32, uint16_t, uint32_t, 0, 65535, 0)
VNARROW_SHIFT(v_sqshrun_u32_u64, uint32_t, uint64_t, 0ll, 0xffffffffll, 0)
VNARROW_SHIFT(v_sqrshrun_u8_u16,  uint8_t,  uint16_t, 0, 255, 1)
VNARROW_SHIFT(v_sqrshrun_u16_u32, uint16_t, uint32_t, 0, 65535, 1)
VNARROW_SHIFT(v_sqrshrun_u32_u64, uint32_t, uint64_t, 0ll, 0xffffffffll, 1)

/* non-saturating shrn/rshrn.  SHRN truncates (verified vs Unicorn: no
   rounding); RSHRN rounds with bit sh-1 (also verified). */
#define VNARROW_NORND(NAME, TD, TS) \
static inline V128 NAME(V128 a, int sh) { \
    V128 r; int i; int n = (int)(16 / sizeof(TS)); \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(sh ? ((long long)x >> sh) : (long long)x); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VNARROW_NORND(v_shrn_u8_u16,  uint8_t,  uint16_t)
VNARROW_NORND(v_shrn_u16_u32, uint16_t, uint32_t)
VNARROW_NORND(v_shrn_u32_u64, uint32_t, uint64_t)

#define VNARROW_NOSAT(NAME, TD, TS) \
static inline V128 NAME(V128 a, int sh) { \
    V128 r; int i; int n = (int)(16 / sizeof(TS)); \
    long long rnd = sh ? (1ll << (sh - 1)) : 0; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        TS x; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(sh ? (((long long)x + rnd) >> sh) : (long long)x); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }
/* RSHRN: round with sh-1 then shift (verified vs Unicorn) */
VNARROW_NOSAT(v_rshrn_u8_u16,  uint8_t,  uint16_t)
VNARROW_NOSAT(v_rshrn_u16_u32, uint16_t, uint32_t)
VNARROW_NOSAT(v_rshrn_u32_u64, uint32_t, uint64_t)

/* ---- permutes ---- */
static inline V128 v_ext(V128 a, V128 b, int n) {
    /* byte n = index into a||b (n in 0..15) */
    unsigned char in[32]; V128 r; int i;
    memcpy(in, &a, 16); memcpy(in + 16, &b, 16);
    for (i = 0; i < 16; i++) ((unsigned char*)&r)[i] = in[n + i];
    return r; }

/* zip/uzp/trn per element size (1,2,4,8): operate on the low `2*n` lanes;
   for full 16-byte arrangements the emitter passes lane count = 16/esize. */
#define VPERM2(NAME, T, PICK) \
static inline V128 NAME(V128 a, V128 b, int n) { \
    V128 r; int i; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n; i++) { \
        T x, y, v; \
        memcpy(&x, (const char*)&a + (2 * i) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + (2 * i) * sizeof(T), sizeof(T)); \
        { int _pi = PICK; T _s = _pi ? y : x; memcpy((char*)&r + i * sizeof(T), &_s, sizeof(T)); } \
        if (2 * i + 1 < n) { \
            memcpy(&x, (const char*)&a + (2 * i + 1) * sizeof(T), sizeof(T)); \
            memcpy(&y, (const char*)&b + (2 * i + 1) * sizeof(T), sizeof(T)); \
            { int _pi = PICK; T _s = _pi ? x : y; memcpy((char*)&r + (i + n / 2) * sizeof(T), &_s, sizeof(T)); } \
        } \
    } \
    return r; }

/* ARM permutes: n = total result lanes (= per-source lane count),
   hi = 0 -> use source low half, hi = 1 -> source high half.
   ZIP1: r[2i]=a[half+i0], r[2i+1]=b[half+i0] (i0 = i, half = hi*n/2)
   UZP1: r[i]=a[half+2i],  r[n/2+i]=b[half+2i]
   TRN1: r[2i]=a[half+2i],  r[2i+1]=b[half+2i] */
#define VZIP(NAME, T) \
static inline V128 NAME(V128 a, V128 b, int n, int hi) { \
    V128 r; int i; int base = hi * (n / 2); \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n / 2; i++) { \
        T x, y; \
        memcpy(&x, (const char*)&a + (base + i) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + (base + i) * sizeof(T), sizeof(T)); \
        memcpy((char*)&r + (2 * i) * sizeof(T), &x, sizeof(T)); \
        memcpy((char*)&r + (2 * i + 1) * sizeof(T), &y, sizeof(T)); \
    } \
    return r; }

VZIP(v_zip1_u8,  uint8_t)
VZIP(v_zip1_u16, uint16_t)
VZIP(v_zip1_u32, uint32_t)
VZIP(v_zip1_u64, uint64_t)

#define VUZP(NAME, T) \
static inline V128 NAME(V128 a, V128 b, int n, int hi) { \
    V128 r; int i; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n / 2; i++) { \
        T x, y; \
        memcpy(&x, (const char*)&a + (2 * i + hi) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + (2 * i + hi) * sizeof(T), sizeof(T)); \
        memcpy((char*)&r + i * sizeof(T), &x, sizeof(T)); \
        memcpy((char*)&r + (n / 2 + i) * sizeof(T), &y, sizeof(T)); \
    } \
    return r; }

VUZP(v_uzp1_u8,  uint8_t)
VUZP(v_uzp1_u16, uint16_t)
VUZP(v_uzp1_u32, uint32_t)
VUZP(v_uzp1_u64, uint64_t)

#define VTRN(NAME, T) \
static inline V128 NAME(V128 a, V128 b, int n, int hi) { \
    V128 r; int i; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < n / 2; i++) { \
        T x, y; \
        memcpy(&x, (const char*)&a + (2 * i + hi) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + (2 * i + hi) * sizeof(T), sizeof(T)); \
        memcpy((char*)&r + (2 * i) * sizeof(T), &x, sizeof(T)); \
        memcpy((char*)&r + (2 * i + 1) * sizeof(T), &y, sizeof(T)); \
    } \
    return r; }

VTRN(v_trn1_u8,  uint8_t)
VTRN(v_trn1_u16, uint16_t)
VTRN(v_trn1_u32, uint32_t)
VTRN(v_trn1_u64, uint64_t)

/* zip/uzp/trn *2 variants: emitter swaps the two sources and passes hi=1,
   which selects the even/odd pairing from the source high halves. */

/* ---- tbl/tbx: byte table lookup, up to 4 tables ---- */
static inline V128 v_tbl(V128 t0, V128 t1, V128 t2, V128 t3,
                         V128 idx, int ntables) {
    unsigned char tbl[64]; unsigned char in[16]; V128 r; int i;
    memset(tbl, 0, sizeof(tbl));
    memcpy(tbl, &t0, 16);
    if (ntables >= 2) memcpy(tbl + 16, &t1, 16);
    if (ntables >= 3) memcpy(tbl + 32, &t2, 16);
    if (ntables >= 4) memcpy(tbl + 48, &t3, 16);
    memcpy(in, &idx, 16);
    for (i = 0; i < 16; i++)
        in[i] = (in[i] < (unsigned)(ntables * 16)) ? tbl[in[i]] : 0;
    memcpy(&r, in, 16);
    return r; }

/* ---- byte permutes ---- */
static inline V128 v_cnt(V128 a) {
    unsigned char in[16], out[16]; V128 r; int i;
    memcpy(in, &a, 16);
    for (i = 0; i < 16; i++) { int b = 0, k; for (k = 0; k < 8; k++) b += (in[i] >> k) & 1; out[i] = (unsigned char)b; }
    memcpy(&r, out, 16); return r; }
static inline V128 v_rev16(V128 a) {
    unsigned char in[16], out[16]; V128 r; int i;
    memcpy(in, &a, 16);
    for (i = 0; i < 16; i += 2) { out[i] = in[i + 1]; out[i + 1] = in[i]; }
    memcpy(&r, out, 16); return r; }
static inline V128 v_rev32(V128 a) {
    unsigned char in[16], out[16]; V128 r; int i, k;
    memcpy(in, &a, 16);
    for (i = 0; i < 16; i += 4) for (k = 0; k < 4; k++) out[i + k] = in[i + 3 - k];
    memcpy(&r, out, 16); return r; }
static inline V128 v_rev64(V128 a) {
    unsigned char in[16], out[16]; V128 r; int i, k;
    memcpy(in, &a, 16);
    for (i = 0; i < 16; i += 8) for (k = 0; k < 8; k++) out[i + k] = in[i + 7 - k];
    memcpy(&r, out, 16); return r; }
static inline V128 v_clz_vec(V128 a) {
    unsigned char in[16], out[16]; V128 r; int i, k;
    memcpy(in, &a, 16);
    for (i = 0; i < 16; i++) { int n = 0; if (in[i] == 0) n = 8; else for (k = 7; k >= 0; k--) { if ((in[i] >> k) & 1) break; n++; } out[i] = (unsigned char)n; }
    memcpy(&r, out, 16); return r; }

/* ---- pairwise adds (non-accumulate): result[i] = a pair,
   result[half+i] = b pair; n = active result lanes ---- */
#define VADDPAIR(NAME, T) \
static inline V128 NAME(V128 a, V128 b, int n) { \
    V128 r; int i; int half = n / 2; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < half; i++) { \
        T x, y, v0, v1; \
        memcpy(&x, (const char*)&a + (2 * i) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&a + (2 * i + 1) * sizeof(T), sizeof(T)); \
        v0 = (T)(x + y); \
        memcpy(&x, (const char*)&b + (2 * i) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + (2 * i + 1) * sizeof(T), sizeof(T)); \
        v1 = (T)(x + y); \
        memcpy((char*)&r + i * sizeof(T), &v0, sizeof(T)); \
        memcpy((char*)&r + (half + i) * sizeof(T), &v1, sizeof(T)); \
    } \
    return r; }

VADDPAIR(v_addp_u8,  uint8_t)
VADDPAIR(v_addp_u16, uint16_t)
VADDPAIR(v_addp_u32, uint32_t)
VADDPAIR(v_addp_u64, uint64_t)

/* widening long pairwise add: 8x8 -> 4x16 style (SADDLP) */
static inline V128 v_saddlp_u16(V128 a) {
    uint8_t in[16]; int16_t o[8]; V128 r; int i;
    memcpy(in, &a, 16);
    for (i = 0; i < 8; i += 2) { int32_t s = (int8_t)in[i] + (int8_t)in[i + 1]; o[i / 2] = (int16_t)s; }
    memcpy(&r, o, 16); return r; }
static inline V128 v_saddlp_u32(V128 a) {
    uint16_t in[8]; int32_t o[4]; V128 r; int i;
    memcpy(in, &a, 16);
    for (i = 0; i < 8; i += 2) { int64_t s = (int16_t)in[i] + (int16_t)in[i + 1]; o[i / 2] = (int32_t)s; }
    memcpy(&r, o, 16); return r; }
static inline V128 v_saddlp_u64(V128 a) {
    uint32_t in[4]; int64_t o[2]; V128 r; int i;
    memcpy(in, &a, 16);
    for (i = 0; i < 4; i += 2) { int64_t s = (int32_t)in[i] + (int32_t)in[i + 1]; o[i / 2] = s; }
    memcpy(&r, o, 16); return r; }
static inline V128 v_uaddlp_u16(V128 a) {
    uint8_t in[16]; uint16_t o[8]; V128 r; int i;
    memcpy(in, &a, 16);
    for (i = 0; i < 8; i += 2) o[i / 2] = (uint16_t)in[i] + in[i + 1];
    memcpy(&r, o, 16); return r; }

/* ---- FP pairwise ---- */
static inline V128 v_faddp_4s(V128 a, V128 b) {
    float x[4], y[4], o[4]; V128 r; int i;
    memcpy(x, &a, 16); memcpy(y, &b, 16);
    for (i = 0; i < 2; i++) { o[i] = x[2 * i] + x[2 * i + 1]; o[2 + i] = y[2 * i] + y[2 * i + 1]; }
    memcpy(&r, o, 16); return r; }
static inline V128 v_faddp_2d(V128 a, V128 b) {
    V128 r; r.lo = a.lo + a.hi; r.hi = b.lo + b.hi; return r; }
/* scalar FADDP Dd, Vn.2D */
static inline double v_faddp_scalar_d(V128 a) {
    return u64f(a.lo) + u64f(a.hi); }

/* ---- FP pairwise max/min ---- */
static inline V128 v_fmaxp_4s(V128 a, V128 b) {
    float x[4], y[4], o[4]; V128 r; int i;
    memcpy(x, &a, 16); memcpy(y, &b, 16);
    for (i = 0; i < 2; i++) { o[i] = a64_fmax_32(x[2 * i], x[2 * i + 1]); o[2 + i] = a64_fmax_32(y[2 * i], y[2 * i + 1]); }
    memcpy(&r, o, 16); return r; }
static inline V128 v_fminp_4s(V128 a, V128 b) {
    float x[4], y[4], o[4]; V128 r; int i;
    memcpy(x, &a, 16); memcpy(y, &b, 16);
    for (i = 0; i < 2; i++) { o[i] = a64_fmin_32(x[2 * i], x[2 * i + 1]); o[2 + i] = a64_fmin_32(y[2 * i], y[2 * i + 1]); }
    memcpy(&r, o, 16); return r; }
static inline V128 v_fmaxp_2d(V128 a, V128 b) {
    V128 r; r.lo = a64_fmax_64(u64f(a.lo), u64f(a.hi));
    r.hi = a64_fmax_64(u64f(b.lo), u64f(b.hi)); return r; }

/* ---- FP/integer reductions across the first `n` active lanes ---- */
static inline V128 v_addv_4s(V128 a, int n) {
    uint32_t x[4]; uint32_t s = 0; V128 r; int i;
    memcpy(x, &a, 16);
    for (i = 0; i < n; i++) s = (uint32_t)(s + x[i]);
    memcpy(&r, &s, 4); return r; }
static inline V128 v_addv_8h(V128 a, int n) {
    uint16_t x[8]; uint32_t s = 0; V128 r; int i;
    memcpy(x, &a, 16);
    for (i = 0; i < n; i++) s = (uint16_t)(s + x[i]);
    memcpy(&r, &s, 2); return r; }
static inline V128 v_addv_16b(V128 a, int n) {
    uint8_t x[16]; uint32_t s = 0; V128 r; int i;
    memcpy(x, &a, 16);
    for (i = 0; i < n; i++) s += x[i];
    { uint8_t b = (uint8_t)s; memcpy(&r, &b, 1); } return r; }
static inline V128 v_umaxv_16b(V128 a, int n) {
    uint8_t x[16], m; V128 r; int i;
    memcpy(x, &a, 16); m = x[0];
    for (i = 1; i < n; i++) if (x[i] > m) m = x[i];
    memcpy(&r, &m, 1); return r; }
static inline V128 v_umaxv_8h(V128 a, int n) {
    uint16_t x[8], m; V128 r; int i;
    memcpy(x, &a, 16); m = x[0];
    for (i = 1; i < n; i++) if (x[i] > m) m = x[i];
    memcpy(&r, &m, 2); return r; }
static inline V128 v_umaxv_4s(V128 a, int n) {
    uint32_t x[4], m; V128 r; int i;
    memcpy(x, &a, 16); m = x[0];
    for (i = 1; i < n; i++) if (x[i] > m) m = x[i];
    memcpy(&r, &m, 4); return r; }
static inline V128 v_fmaxnmv_f32(V128 a, int n) {
    float x[4], s; V128 r; uint32_t bits; int i;
    memcpy(x, &a, 16); s = x[0];
    for (i = 1; i < n; i++) s = a64_fmaxnm_32(s, x[i]);
    bits = f32b(s); memcpy(&r, &bits, 4); return r; }
static inline V128 v_fminnmv_f32(V128 a, int n) {
    float x[4], s; V128 r; uint32_t bits; int i;
    memcpy(x, &a, 16); s = x[0];
    for (i = 1; i < n; i++) s = a64_fminnm_32(s, x[i]);
    bits = f32b(s); memcpy(&r, &bits, 4); return r; }

/* ---- AES (QEMU crypto_helper semantics: AESE/AESD XOR the round key
   FIRST, then SubBytes+ShiftRows / InvSubBytes+InvShiftRows;
   AESIMC = InvMixColumns only).  State byte i: row = i%4, col = i/4. ---- */
static inline uint8_t a64_gfmul(uint8_t a, uint8_t b) {
    uint8_t p = 0;
    while (b) {
        if (b & 1) p ^= a;
        a = (uint8_t)((a << 1) ^ ((a >> 7) ? 0x1b : 0));
        b >>= 1;
    }
    return p;
}
static inline V128 v_aese(V128 state, V128 key) {
    static const uint8_t sbox[256] = {
        0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
        0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
        0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
        0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
        0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
        0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
        0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
        0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
        0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
        0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
        0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
        0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
        0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
        0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
        0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
        0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
    };
    unsigned char in[16], out[16]; V128 r; int i, c, row;
    memcpy(in, &state, 16); memcpy(out, &key, 16);
    for (i = 0; i < 16; i++) in[i] ^= out[i];       /* AddRoundKey first */
    for (i = 0; i < 16; i++) in[i] = sbox[in[i]];   /* SubBytes */
    for (c = 0; c < 4; c++)                          /* ShiftRows */
        for (row = 0; row < 4; row++)
            out[row + 4 * c] = in[row + 4 * ((c + row) & 3)];
    memcpy(&r, out, 16);
    return r;
}
static inline V128 v_aesd(V128 state, V128 key) {
    static const uint8_t inv_sbox[256] = {
        0x52,0x09,0x6a,0xd5,0x30,0x36,0xa5,0x38,0xbf,0x40,0xa3,0x9e,0x81,0xf3,0xd7,0xfb,
        0x7c,0xe3,0x39,0x82,0x9b,0x2f,0xff,0x87,0x34,0x8e,0x43,0x44,0xc4,0xde,0xe9,0xcb,
        0x54,0x7b,0x94,0x32,0xa6,0xc2,0x23,0x3d,0xee,0x4c,0x95,0x0b,0x42,0xfa,0xc3,0x4e,
        0x08,0x2e,0xa1,0x66,0x28,0xd9,0x24,0xb2,0x76,0x5b,0xa2,0x49,0x6d,0x8b,0xd1,0x25,
        0x72,0xf8,0xf6,0x64,0x86,0x68,0x98,0x16,0xd4,0xa4,0x5c,0xcc,0x5d,0x65,0xb6,0x92,
        0x6c,0x70,0x48,0x50,0xfd,0xed,0xb9,0xda,0x5e,0x15,0x46,0x57,0xa7,0x8d,0x9d,0x84,
        0x90,0xd8,0xab,0x00,0x8c,0xbc,0xd3,0x0a,0xf7,0xe4,0x58,0x05,0xb8,0xb3,0x45,0x06,
        0xd0,0x2c,0x1e,0x8f,0xca,0x3f,0x0f,0x02,0xc1,0xaf,0xbd,0x03,0x01,0x13,0x8a,0x6b,
        0x3a,0x91,0x11,0x41,0x4f,0x67,0xdc,0xea,0x97,0xf2,0xcf,0xce,0xf0,0xb4,0xe6,0x73,
        0x96,0xac,0x74,0x22,0xe7,0xad,0x35,0x85,0xe2,0xf9,0x37,0xe8,0x1c,0x75,0xdf,0x6e,
        0x47,0xf1,0x1a,0x71,0x1d,0x29,0xc5,0x89,0x6f,0xb7,0x62,0x0e,0xaa,0x18,0xbe,0x1b,
        0xfc,0x56,0x3e,0x4b,0xc6,0xd2,0x79,0x20,0x9a,0xdb,0xc0,0xfe,0x78,0xcd,0x5a,0xf4,
        0x1f,0xdd,0xa8,0x33,0x88,0x07,0xc7,0x31,0xb1,0x12,0x10,0x59,0x27,0x80,0xec,0x5f,
        0x60,0x51,0x7f,0xa9,0x19,0xb5,0x4a,0x0d,0x2d,0xe5,0x7a,0x9f,0x93,0xc9,0x9c,0xef,
        0xa0,0xe0,0x3b,0x4d,0xae,0x2a,0xf5,0xb0,0xc8,0xeb,0xbb,0x3c,0x83,0x53,0x99,0x61,
        0x17,0x2b,0x04,0x7e,0xba,0x77,0xd6,0x26,0xe1,0x69,0x14,0x63,0x55,0x21,0x0c,0x7d
    };
    unsigned char in[16], out[16]; V128 r; int i, c, row;
    memcpy(in, &state, 16); memcpy(out, &key, 16);
    for (i = 0; i < 16; i++) in[i] ^= out[i];
    for (i = 0; i < 16; i++) in[i] = inv_sbox[in[i]];
    for (c = 0; c < 4; c++)                          /* InvShiftRows */
        for (row = 0; row < 4; row++)
            out[row + 4 * c] = in[row + 4 * ((c - row + 4) & 3)];
    memcpy(&r, out, 16);
    return r;
}
static inline V128 v_aesimc(V128 a) {
    unsigned char in[16], out[16]; V128 r; int c;
    memcpy(in, &a, 16);
    for (c = 0; c < 4; c++) {
        const unsigned char *p = in + 4 * c;
        unsigned char *q = out + 4 * c;
        q[0] = (unsigned char)(a64_gfmul(p[0],14) ^ a64_gfmul(p[1],11) ^ a64_gfmul(p[2],13) ^ a64_gfmul(p[3],9));
        q[1] = (unsigned char)(a64_gfmul(p[0],9)  ^ a64_gfmul(p[1],14) ^ a64_gfmul(p[2],11) ^ a64_gfmul(p[3],13));
        q[2] = (unsigned char)(a64_gfmul(p[0],13) ^ a64_gfmul(p[1],9)  ^ a64_gfmul(p[2],14) ^ a64_gfmul(p[3],11));
        q[3] = (unsigned char)(a64_gfmul(p[0],11) ^ a64_gfmul(p[1],13) ^ a64_gfmul(p[2],9)  ^ a64_gfmul(p[3],14));
    }
    memcpy(&r, out, 16);
    return r;
}

/* ---- conversion helpers (same lane count, int <-> fp) ---- */
#define VCONV(NAME, TD, TS, EXPR) \
static inline V128 NAME(V128 a) { \
    V128 r; int i; \
    for (i = 0; i < (int)(16 / sizeof(TD)); i++) { \
        TS x; TD v; \
        memcpy(&x, (const char*)&a + i * sizeof(TS), sizeof(TS)); \
        v = (TD)(EXPR); \
        memcpy((char*)&r + i * sizeof(TD), &v, sizeof(TD)); \
    } \
    return r; }

VCONV(v_scvtf_f32_s32, float,  int32_t, (float)x)
VCONV(v_scvtf_f64_s64, double, int64_t, (double)x)
VCONV(v_ucvtf_f32_u32, float,  uint32_t, (float)x)
VCONV(v_ucvtf_f64_u64, double, uint64_t, (double)x)
VCONV(v_fcvtzs_s32_f32, int32_t, float,  (int32_t)x)
VCONV(v_fcvtzs_s64_f64, int64_t, double, (int64_t)x)
VCONV(v_fcvtzu_u32_f32, uint32_t, float,  (uint32_t)x)
VCONV(v_fcvtzu_u64_f64, uint64_t, double, (uint64_t)x)

/* ================= gap-driven instantiations (emitter coverage) ========= */
VBIN(v_mul_u64,  uint64_t, (uint64_t)(x * y))
VBIN(v_smin_u64, uint64_t, (uint64_t)(((int64_t)x < (int64_t)y) ? x : y))
VBIN(v_smax_u64, uint64_t, (uint64_t)(((int64_t)x > (int64_t)y) ? x : y))
VCMP(v_cmge_u8,  uint8_t,  (int8_t)x >= (int8_t)y)
VCMP(v_cmge_u16, uint16_t, (int16_t)x >= (int16_t)y)
VCMP(v_cmge_u32, uint32_t, (int32_t)x >= (int32_t)y)
VCMP(v_cmge_u64, uint64_t, (int64_t)x >= (int64_t)y)
VCMP(v_cmhs_u8,  uint8_t,  x >= y)
VCMP(v_cmhs_u16, uint16_t, x >= y)
VCMP(v_cmhs_u32, uint32_t, x >= y)
VCMP(v_cmhs_u64, uint64_t, x >= y)
VSAT_SIGNED(v_sqadd_u64, int64_t, INT64_MIN, INT64_MAX)
VSAT_SIGNED_SUB(v_sqsub_u64, int64_t, INT64_MIN, INT64_MAX)
/* u64 saturating add/sub: dedicated (unsigned long is 32-bit on MSVC) */
static inline V128 v_uqadd_u64(V128 a, V128 b) {
    V128 r; int i;
    for (i = 0; i < 2; i++) {
        uint64_t x, y, v;
        memcpy(&x, (const char*)&a + i * 8, 8);
        memcpy(&y, (const char*)&b + i * 8, 8);
        v = (x + y < x) ? ~0ull : (x + y);
        memcpy((char*)&r + i * 8, &v, 8);
    }
    return r; }
static inline V128 v_uqsub_u64(V128 a, V128 b) {
    V128 r; int i;
    for (i = 0; i < 2; i++) {
        uint64_t x, y, v;
        memcpy(&x, (const char*)&a + i * 8, 8);
        memcpy(&y, (const char*)&b + i * 8, 8);
        v = (y > x) ? 0ull : (x - y);
        memcpy((char*)&r + i * 8, &v, 8);
    }
    return r; }
/* byte-wise right shift of the whole register: *2 (high-half) source
   forms read bytes [8..15] as if they were the low half. */
static inline V128 v_rshift_bytes(V128 a, int n) {
    V128 r; r.lo = 0; r.hi = 0;
    if (n <= 0) return a;
    if (n >= 16) return r;
    memcpy(&r, (const char*)&a + n, 16 - n);
    return r; }
/* ADDV across .2D */
static inline V128 v_addv_2d(V128 a, int n) {
    uint64_t s = 0; V128 r; int i;
    for (i = 0; i < n; i++) { uint64_t x; memcpy(&x, (const char*)&a + i * 8, 8); s += x; }
    r.lo = s; r.hi = 0; return r; }
/* FMINP .2D (FMAXP .2D exists above) */
static inline V128 v_fminp_2d(V128 a, V128 b) {
    V128 r;
    r.lo = a64_fmin_64(u64f(a.lo), u64f(a.hi));
    r.hi = a64_fmin_64(u64f(b.lo), u64f(b.hi));
    return r; }
/* pairwise max/min across two sources (SMAXP/UMINP family) */
/* pairwise max/min across two sources (SMAXP/UMAXP/SMINP/UMINP):
   EXPR compares sx,sy (already cast to ST) and yields a T-sized value. */
#define VPAIRMAXMIN(NAME, T, ST, EXPR) \
static inline V128 NAME(V128 a, V128 b, int n) { \
    V128 r; int i; int half = n / 2; \
    r.lo = 0; r.hi = 0; \
    for (i = 0; i < half; i++) { \
        T x, y, v0, v1; ST sx, sy; \
        memcpy(&x, (const char*)&a + (2 * i) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&a + (2 * i + 1) * sizeof(T), sizeof(T)); \
        sx = (ST)x; sy = (ST)y; v0 = (T)(EXPR); \
        memcpy(&x, (const char*)&b + (2 * i) * sizeof(T), sizeof(T)); \
        memcpy(&y, (const char*)&b + (2 * i + 1) * sizeof(T), sizeof(T)); \
        sx = (ST)x; sy = (ST)y; v1 = (T)(EXPR); \
        memcpy((char*)&r + i * sizeof(T), &v0, sizeof(T)); \
        memcpy((char*)&r + (half + i) * sizeof(T), &v1, sizeof(T)); \
    } \
    return r; }
VPAIRMAXMIN(v_smaxp_u8,  uint8_t,  int8_t,  (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_smaxp_u16, uint16_t, int16_t, (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_smaxp_u32, uint32_t, int32_t, (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_smaxp_u64, uint64_t, int64_t, (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_umaxp_u8,  uint8_t,  uint8_t,  (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_umaxp_u16, uint16_t, uint16_t, (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_umaxp_u32, uint32_t, uint32_t, (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_umaxp_u64, uint64_t, uint64_t, (sx > sy) ? sx : sy)
VPAIRMAXMIN(v_sminp_u8,  uint8_t,  int8_t,  (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_sminp_u16, uint16_t, int16_t, (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_sminp_u32, uint32_t, int32_t, (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_sminp_u64, uint64_t, int64_t, (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_uminp_u8,  uint8_t,  uint8_t,  (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_uminp_u16, uint16_t, uint16_t, (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_uminp_u32, uint32_t, uint32_t, (sx < sy) ? sx : sy)
VPAIRMAXMIN(v_uminp_u64, uint64_t, uint64_t, (sx < sy) ? sx : sy)
/* across-lane reductions: signed/unsigned/FP max/min to lane 0 */
#define VRED(NAME, T, ST, EXPR) \
static inline V128 NAME(V128 a, int n) { \
    V128 r; int i; T m; ST sx, sy; \
    memcpy(&m, &a, sizeof(T)); \
    for (i = 1; i < n; i++) { T x; memcpy(&x, (const char*)&a + i * sizeof(T), sizeof(T)); \
        sx = (ST)m; sy = (ST)x; m = (T)(EXPR); } \
    r.lo = 0; r.hi = 0; memcpy(&r, &m, sizeof(T)); return r; }
VRED(v_smaxv_u8,  uint8_t,  int8_t,  (sx > sy) ? sx : sy)
VRED(v_smaxv_u16, uint16_t, int16_t, (sx > sy) ? sx : sy)
VRED(v_smaxv_u32, uint32_t, int32_t, (sx > sy) ? sx : sy)
VRED(v_smaxv_u64, uint64_t, int64_t, (sx > sy) ? sx : sy)
VRED(v_sminv_u8,  uint8_t,  int8_t,  (sx < sy) ? sx : sy)
VRED(v_sminv_u16, uint16_t, int16_t, (sx < sy) ? sx : sy)
VRED(v_sminv_u32, uint32_t, int32_t, (sx < sy) ? sx : sy)
VRED(v_sminv_u64, uint64_t, int64_t, (sx < sy) ? sx : sy)
VRED(v_uminv_u8,  uint8_t,  uint8_t,  (sx < sy) ? sx : sy)
VRED(v_uminv_u16, uint16_t, uint16_t, (sx < sy) ? sx : sy)
VRED(v_uminv_u32, uint32_t, uint32_t, (sx < sy) ? sx : sy)
VRED(v_uminv_u64, uint64_t, uint64_t, (sx < sy) ? sx : sy)
VRED(v_umaxv_u8,  uint8_t,  uint8_t,  (sx > sy) ? sx : sy)
VRED(v_umaxv_u16, uint16_t, uint16_t, (sx > sy) ? sx : sy)
VRED(v_umaxv_u32, uint32_t, uint32_t, (sx > sy) ? sx : sy)
VRED(v_umaxv_u64, uint64_t, uint64_t, (sx > sy) ? sx : sy)
/* FP across-lane max (plain, non-number-pattern-aware is fine here:
   a64_fmax matches ARM FMAX for the operand values games actually pass) */
static inline V128 v_fmaxv_f32(V128 a, int n) {
    float x[4], m; V128 r; uint32_t bits; int i;
    memcpy(x, &a, 16); m = x[0];
    for (i = 1; i < n; i++) m = a64_fmax_32(m, x[i]);
    bits = f32b(m); r.lo = bits; r.hi = 0; return r; }
static inline V128 v_fminv_f32(V128 a, int n) {
    float x[4], m; V128 r; uint32_t bits; int i;
    memcpy(x, &a, 16); m = x[0];
    for (i = 1; i < n; i++) m = a64_fmin_32(m, x[i]);
    bits = f32b(m); r.lo = bits; r.hi = 0; return r; }
static inline V128 v_fmaxv_f64(V128 a, int n) {
    double m; V128 r; int i;
    m = u64f(a.lo);
    for (i = 1; i < n; i++) m = a64_fmax_64(m, u64f(i ? a.hi : a.lo));
    r.lo = f64b(m); r.hi = 0; return r; }

/* ============ deferred helpers (scalar + vector fill-ins) ============ */

static inline float  v_fabsf(float x)  { return fabsf(x); }
static inline double v_fabsd(double x) { return fabs(x); }
static inline float  v_fnegf(float x)  { return -x; }
static inline double v_fnegd(double x) { return -x; }

/* ---- vector register accessors for the recompiler ---- */
static inline V128 vld128(A64Ctx* c, int i) { V128 v; v.lo = c->d[i]; v.hi = c->dhi[i]; return v; }
static inline void vst128(A64Ctx* c, int i, V128 v) { c->d[i] = v.lo; c->dhi[i] = v.hi; }

/* ---- table lookup with TBX semantics (preserve dest where index OOR) ---- */
static inline V128 v_tbx(V128 d, V128 t0, V128 t1, V128 t2, V128 t3,
                          V128 idx, int ntables) {
    unsigned char tbl[64], in[16], out[16]; V128 r; int i;
    memcpy(out, &d, 16);
    memset(tbl, 0, sizeof(tbl));
    memcpy(tbl, &t0, 16);
    if (ntables >= 2) memcpy(tbl + 16, &t1, 16);
    if (ntables >= 3) memcpy(tbl + 32, &t2, 16);
    if (ntables >= 4) memcpy(tbl + 48, &t3, 16);
    memcpy(in, &idx, 16);
    for (i = 0; i < 16; i++)
        if (in[i] < (unsigned)(ntables * 16)) out[i] = tbl[in[i]];
    memcpy(&r, out, 16);
    return r; }

/* ---- bit select: BSL(mask,a,b) = (mask&a)|(~mask&b);
        BIT(mask,b,a) = (mask&b)|(~mask&a);  BIF(mask,b,a) = same as BIT ---- */
static inline V128 v_bit(V128 m, V128 b, V128 a) { V128 r; r.lo = (m.lo & b.lo) | (~m.lo & a.lo); r.hi = (m.hi & b.hi) | (~m.hi & a.hi); return r; }
static inline V128 v_bif(V128 m, V128 b, V128 a) { V128 r; r.lo = (m.lo & b.lo) | (~m.lo & a.lo); r.hi = (m.hi & b.hi) | (~m.hi & a.hi); return r; }

/* ---- element helpers ---- */
static inline uint64_t vgetelem(V128 v, int es, int i) {
    unsigned char b[16]; uint64_t x = 0; memcpy(b, &v, 16);
    if (i * es + es > 16) return 0;
    memcpy(&x, b + i * es, es); return x; }
static inline V128 vsetelem(V128 v, int es, int i, uint64_t x) {
    unsigned char b[16]; memcpy(b, &v, 16);
    if (i * es + es <= 16) memcpy(b + i * es, &x, es);
    memcpy(&v, b, 16); return v; }
static inline V128 vdup_elem(V128 v, int es, int i) {
    uint64_t x = vgetelem(v, es, i); V128 r; int k;
    r.lo = 0; r.hi = 0;
    for (k = 0; k < 16 / es; k++) r = vsetelem(r, es, k, x);
    return r; }
static inline V128 vdup_gpr(uint64_t x, int es) {
    V128 r; int k; uint64_t m = (es >= 8) ? ~0ull : ((1ull << (es * 8)) - 1);
    r.lo = 0; r.hi = 0; x &= m;
    for (k = 0; k < 16 / es; k++) r = vsetelem(r, es, k, x);
    return r; }

/* ---- clz/cls for narrow lanes (vector) ---- */
static inline V128 v_clz_u8(V128 a)  { unsigned char b[16]; V128 r; int i, k; memcpy(b, &a, 16); for (i = 0; i < 16; i++) { int c = 0; for (k = 7; k >= 0 && !((b[i] >> k) & 1); k--) c++; ((unsigned char*)&r)[i] = (unsigned char)c; } return r; }
static inline V128 v_clz_u16(V128 a) { uint16_t b[8]; V128 r; int i; memcpy(b, &a, 16); for (i = 0; i < 8; i++) { int c = 0, k; for (k = 15; k >= 0 && !((b[i] >> k) & 1); k--) c++; ((uint16_t*)&r)[i] = (uint16_t)c; } return r; }
static inline V128 v_clz_u32(V128 a) { V128 r; int i; uint32_t b[4]; memcpy(b, &a, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = a64_clz32(b[i]); return r; }
static inline V128 v_clz_u64(V128 a) { V128 r; uint64_t x[2]; memcpy(x, &a, 16); r.lo = a64_clz64(x[0]); r.hi = a64_clz64(x[1]); return r; }
static inline V128 v_cls_u8(V128 a)  { unsigned char b[16]; V128 r; int i; memcpy(b, &a, 16); for (i = 0; i < 16; i++) { int x = (int)(signed char)b[i], c = 0; int msb = x >> 31; for (c = 0; c < 8 && ((x >> (7 - c)) & 1) == (unsigned)msb; c++) ; ((unsigned char*)&r)[i] = (unsigned char)(c - 1); } return r; }
static inline V128 v_cls_u16(V128 a) { uint16_t b[8]; V128 r; int i; memcpy(b, &a, 16); for (i = 0; i < 8; i++) { int x = (int)(signed short)b[i], c = 0; int msb = x >> 31; for (c = 0; c < 16 && ((x >> (15 - c)) & 1) == (unsigned)msb; c++) ; ((uint16_t*)&r)[i] = (uint16_t)(c - 1); } return r; }
static inline V128 v_cls_u32(V128 a) { V128 r; uint32_t b[4]; int i; memcpy(b, &a, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = a64_cls32(b[i]); return r; }
static inline V128 v_cls_u64(V128 a) { V128 r; uint64_t b[2]; memcpy(b, &a, 16); r.lo = a64_cls64(b[0]); r.hi = a64_cls64(b[1]); return r; }

/* ---- signed compares where signedness is the point (cmge/cmgt) ---- */
static inline V128 v_cmge_s8(V128 a, V128 b)  { V128 r; int8_t x[16], y[16]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) ((uint8_t*)&r)[i] = (uint8_t)-(x[i] >= y[i]); return r; }
static inline V128 v_cmge_s16(V128 a, V128 b) { V128 r; int16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)-(x[i] >= y[i]); return r; }
static inline V128 v_cmge_s32(V128 a, V128 b) { V128 r; int32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = (uint32_t)-(x[i] >= y[i]); return r; }
static inline V128 v_cmge_s64(V128 a, V128 b) { V128 r; int64_t x[2], y[2]; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = (uint64_t)-(x[0] >= y[0]); r.hi = (uint64_t)-(x[1] >= y[1]); return r; }
static inline V128 v_cmgt_s8(V128 a, V128 b)  { V128 r; int8_t x[16], y[16]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) ((uint8_t*)&r)[i] = (uint8_t)-(x[i] > y[i]); return r; }
static inline V128 v_cmgt_s16(V128 a, V128 b) { V128 r; int16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)-(x[i] > y[i]); return r; }
static inline V128 v_cmgt_s32(V128 a, V128 b) { V128 r; int32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = (uint32_t)-(x[i] > y[i]); return r; }
static inline V128 v_cmgt_s64(V128 a, V128 b) { V128 r; int64_t x[2], y[2]; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = (uint64_t)-(x[0] > y[0]); r.hi = (uint64_t)-(x[1] > y[1]); return r; }
static inline V128 v_cmlt_s8(V128 a, V128 b)  { return v_cmgt_s8(b, a); }
static inline V128 v_cmlt_s16(V128 a, V128 b) { return v_cmgt_s16(b, a); }
static inline V128 v_cmlt_s32(V128 a, V128 b) { return v_cmgt_s32(b, a); }
static inline V128 v_cmlt_s64(V128 a, V128 b) { return v_cmgt_s64(b, a); }
static inline V128 v_cmle_s8(V128 a, V128 b)  { return v_cmge_s8(b, a); }
static inline V128 v_cmle_s16(V128 a, V128 b) { return v_cmge_s16(b, a); }
static inline V128 v_cmle_s32(V128 a, V128 b) { return v_cmge_s32(b, a); }
static inline V128 v_cmle_s64(V128 a, V128 b) { return v_cmge_s64(b, a); }
static inline V128 v_cmeq_s8(V128 a, V128 b)  { V128 r; int8_t x[16], y[16]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) ((uint8_t*)&r)[i] = (uint8_t)-(x[i] == y[i]); return r; }
static inline V128 v_cmeq_s16(V128 a, V128 b) { V128 r; int16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)-(x[i] == y[i]); return r; }
static inline V128 v_cmeq_s32(V128 a, V128 b) { V128 r; int32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = (uint32_t)-(x[i] == y[i]); return r; }
static inline V128 v_cmeq_s64(V128 a, V128 b) { V128 r; int64_t x[2], y[2]; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = (uint64_t)-(x[0] == y[0]); r.hi = (uint64_t)-(x[1] == y[1]); return r; }

/* ---- halving adds ---- */
static inline V128 v_urhadd_u8(V128 a, V128 b) { V128 r; uint8_t x[16], y[16]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) ((uint8_t*)&r)[i] = (uint8_t)(((unsigned)x[i] + (unsigned)y[i] + 1) >> 1); return r; }
static inline V128 v_shsub_u8(V128 a, V128 b)  { V128 r; uint8_t x[16], y[16]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) ((uint8_t*)&r)[i] = (uint8_t)(((int)x[i] - (int)y[i]) >> 1); return r; }
static inline V128 v_uhsub_u8(V128 a, V128 b)  { V128 r; uint8_t x[16], y[16]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) ((uint8_t*)&r)[i] = (uint8_t)(((unsigned)x[i] - (unsigned)y[i]) >> 1); return r; }
/* halving subtract, wider lanes (gap-driven: census flagged v_shsub_u16) */
static inline V128 v_shsub_u16(V128 a, V128 b)  { V128 r; uint16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)(((int16_t)x[i] - (int16_t)y[i]) >> 1); return r; }
static inline V128 v_shsub_u32(V128 a, V128 b)  { V128 r; uint32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = (uint32_t)(((int32_t)x[i] - (int32_t)y[i]) >> 1); return r; }
static inline V128 v_uhsub_u16(V128 a, V128 b)  { V128 r; uint16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)(((uint32_t)x[i] - (uint32_t)y[i]) >> 1); return r; }
static inline V128 v_uhsub_u32(V128 a, V128 b)  { V128 r; uint32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 4; i++) ((uint32_t*)&r)[i] = (uint32_t)(((uint32_t)x[i] - (uint32_t)y[i]) >> 1); return r; }
/* pairwise FP, 2-scalar form (Vd[0]=Vn[0]+Vn[1], Vd[1]=Vm[0]+Vm[1]) */
static inline V128 v_faddp_2s(V128 a, V128 b) { float x[2], y[2], o[2]; V128 r; memcpy(x, &a, 8); memcpy(y, &b, 8); o[0] = x[0] + x[1]; o[1] = y[0] + y[1]; memcpy(&r, o, 8); r.hi = 0; return r; }
static inline V128 v_fmaxp_2s(V128 a, V128 b) { float x[2], y[2], o[2]; V128 r; memcpy(x, &a, 8); memcpy(y, &b, 8); o[0] = a64_fmax_32(x[0], x[1]); o[1] = a64_fmax_32(y[0], y[1]); memcpy(&r, o, 8); r.hi = 0; return r; }
static inline V128 v_fminp_2s(V128 a, V128 b) { float x[2], y[2], o[2]; V128 r; memcpy(x, &a, 8); memcpy(y, &b, 8); o[0] = a64_fmin_32(x[0], x[1]); o[1] = a64_fmin_32(y[0], y[1]); memcpy(&r, o, 8); r.hi = 0; return r; }
/* FP reciprocal / rsqrt estimates (games refine via frecpe/frsqrts newton step;
   exact 1/x and 1/sqrt here are within the estimate's use and bit-sane) */
static inline V128 v_frecpe_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = 1.0f / x[i]; memcpy(&r, o, 16); return r; }
static inline V128 v_frecpe_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = 1.0 / x[i]; memcpy(&r, o, 16); return r; }
static inline V128 v_frsqrte_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = 1.0f / sqrtf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frsqrte_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = 1.0 / sqrt(x[i]); memcpy(&r, o, 16); return r; }
/* FP rounding: n=nearest-even p=ceil m=floor z=trunc a=away x/i=current(=nearest) */
static inline V128 v_frintn_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = nearbyintf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintn_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = nearbyint(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintp_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = ceilf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintp_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = ceil(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintm_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = floorf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintm_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = floor(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintz_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = truncf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintz_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = trunc(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frinta_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = roundf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frinta_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = round(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintx_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = nearbyintf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frintx_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = nearbyint(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frinti_f32(V128 a) { V128 r; float x[4], o[4]; int i; memcpy(x, &a, 16); for (i = 0; i < 4; i++) o[i] = nearbyintf(x[i]); memcpy(&r, o, 16); return r; }
static inline V128 v_frinti_f64(V128 a) { V128 r; double x[2], o[2]; int i; memcpy(x, &a, 16); for (i = 0; i < 2; i++) o[i] = nearbyint(x[i]); memcpy(&r, o, 16); return r; }

/* ---- pairwise add (non-narrowing) ---- */

/* ---- pairwise max / min ---- */

/* ---- scalar pairwise FP (FADDP/FMAXP/FMINP Sd, Vn.2S) ---- */
static inline float v_faddp_scalar_s(V128 a) { float x[2]; memcpy(x, &a, 8); return x[0] + x[1]; }
static inline float v_fmaxp_scalar_s(V128 a) { float x[2]; memcpy(x, &a, 8); return a64_fmax_32(x[0], x[1]); }
static inline double v_fmaxp_scalar_d(V128 a) { double x[2]; memcpy(x, &a, 16); return a64_fmax_64(x[0], x[1]); }
static inline double v_fminp_scalar_d(V128 a) { double x[2]; memcpy(x, &a, 16); return a64_fmin_64(x[0], x[1]); }

/* ---- pairwise add over the low 64 (ADDP Dd, Vn.2D) ---- */
static inline uint64_t v_addp_scalar_d_u64(V128 a) { return a.lo + a.hi; }

/* ---- widen-long accumulating (SMLAL/UMLAL/SQDMLAL 16->32) ---- */
/* ---- polynomial multiply (PMUL/PMULL) ---- */
static inline V128 v_pmul_u8(V128 a, V128 b) { unsigned char x[16], y[16]; V128 r; int i, k; memcpy(x, &a, 16); memcpy(y, &b, 16); for (i = 0; i < 16; i++) { unsigned p = 0; for (k = 0; k < 8; k++) if ((y[i] >> k) & 1) p ^= (unsigned)x[i] << k; ((unsigned char*)&r)[i] = (unsigned char)p; } return r; }
static inline V128 v_pmull_u16_u8(V128 a, V128 b) { unsigned char x[16], y[16]; V128 r; int i, k; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 8; i++) { unsigned p = 0; for (k = 0; k < 8; k++) if ((y[i] >> k) & 1) p ^= (unsigned)x[i] << k; ((uint16_t*)&r)[i] = (uint16_t)p; } return r; }

/* ---- addhn/raddhn/subhn (narrow pairwise add, no shift) ---- */
static inline V128 v_addhn_u8_u16(V128 a, V128 b) { V128 r; uint16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 8; i++) ((uint8_t*)&r)[i] = (uint8_t)((x[i] + y[i]) >> 8); return r; }
static inline V128 v_addhn_u16_u32(V128 a, V128 b) { V128 r; uint32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 4; i++) ((uint16_t*)&r)[i] = (uint16_t)((x[i] + y[i]) >> 16); return r; }
static inline V128 v_addhn_u32_u64(V128 a, V128 b) { V128 r; uint64_t x[2], y[2]; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = (uint32_t)((x[0] + y[0]) >> 32) | ((uint64_t)(uint32_t)((x[1] + y[1]) >> 32) << 32); r.hi = 0; return r; }
static inline V128 v_raddhn_u8_u16(V128 a, V128 b) { V128 r; uint16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 8; i++) ((uint8_t*)&r)[i] = (uint8_t)(((unsigned)x[i] + (unsigned)y[i] + 128u) >> 8); return r; }
static inline V128 v_raddhn_u16_u32(V128 a, V128 b) { V128 r; uint32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 4; i++) ((uint16_t*)&r)[i] = (uint16_t)(((uint64_t)x[i] + y[i] + 32768u) >> 16); return r; }
static inline V128 v_raddhn_u32_u64(V128 a, V128 b) { V128 r; uint64_t x[2], y[2]; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = (uint32_t)((x[0] + y[0] + (1ull<<31)) >> 32) | ((uint64_t)(uint32_t)((x[1] + y[1] + (1ull<<31)) >> 32) << 32); r.hi = 0; return r; }
static inline V128 v_subhn_u8_u16(V128 a, V128 b) { V128 r; uint16_t x[8], y[8]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 8; i++) ((uint8_t*)&r)[i] = (uint8_t)((x[i] - y[i]) >> 8); return r; }
static inline V128 v_subhn_u16_u32(V128 a, V128 b) { V128 r; uint32_t x[4], y[4]; int i; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 4; i++) ((uint16_t*)&r)[i] = (uint16_t)((x[i] - y[i]) >> 16); return r; }
static inline V128 v_subhn_u32_u64(V128 a, V128 b) { V128 r; uint64_t x[2], y[2]; memcpy(x, &a, 16); memcpy(y, &b, 16); r.lo = (uint32_t)((x[0] - y[0]) >> 32) | ((uint64_t)(uint32_t)((x[1] - y[1]) >> 32) << 32); r.hi = 0; return r; }

/* ---- widening subtract (SUBHN already above) ---- */
static inline V128 v_usubw_u32_u16(V128 acc, V128 b) { V128 r; uint16_t y[8]; uint32_t z[4]; int i; memcpy(y, &b, 16); memcpy(z, &acc, 16); for (i = 0; i < 4; i++) z[i] = z[i] - y[i]; memcpy(&r, z, 16); return r; }
static inline V128 v_ssubw_u32_u16(V128 acc, V128 b) { V128 r; int16_t y[8]; uint32_t z[4]; int i; memcpy(y, &b, 16); memcpy(z, &acc, 16); for (i = 0; i < 4; i++) z[i] = (uint32_t)(z[i] - y[i]); memcpy(&r, z, 16); return r; }
static inline V128 v_usubw_u64_u32(V128 acc, V128 b) { V128 r; uint32_t y[4]; uint64_t z[2]; int i; memcpy(y, &b, 16); memcpy(z, &acc, 16); for (i = 0; i < 2; i++) z[i] = z[i] - y[i]; memcpy(&r, z, 16); return r; }
static inline V128 v_ssubw_u64_u32(V128 acc, V128 b) { V128 r; int32_t y[4]; uint64_t z[2]; int i; memcpy(y, &b, 16); memcpy(z, &acc, 16); for (i = 0; i < 2; i++) z[i] = (uint64_t)(z[i] - y[i]); memcpy(&r, z, 16); return r; }

/* ---- long pairwise add (SADDLP/UADDLP/SADALP/UADALP) ---- */
static inline V128 v_saddlp_u16_u8(V128 a) { V128 r; uint8_t x[16]; int i; memcpy(x, &a, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)((int16_t)((int8_t)x[2*i] + (int8_t)x[2*i+1])); return r; }
static inline V128 v_uaddlp_u16_u8(V128 a) { V128 r; uint8_t x[16]; int i; memcpy(x, &a, 16); r.lo = 0; r.hi = 0; for (i = 0; i < 8; i++) ((uint16_t*)&r)[i] = (uint16_t)((unsigned)x[2*i] + (unsigned)x[2*i+1]); return r; }
static inline V128 v_sadalp_u16_u8(V128 acc, V128 a) { V128 r; uint8_t x[16]; uint16_t z[8]; int i; memcpy(x, &a, 16); memcpy(z, &acc, 16); for (i = 0; i < 8; i++) z[i] = (uint16_t)(z[i] + (int16_t)((int8_t)x[2*i] + (int8_t)x[2*i+1])); memcpy(&r, z, 16); return r; }
static inline V128 v_uadalp_u16_u8(V128 acc, V128 a) { V128 r; uint8_t x[16]; uint16_t z[8]; int i; memcpy(x, &a, 16); memcpy(z, &acc, 16); for (i = 0; i < 8; i++) z[i] = (uint16_t)(z[i] + (unsigned)x[2*i] + (unsigned)x[2*i+1]); memcpy(&r, z, 16); return r; }

/* ---- long add across lanes (SADDLV/UADDLV) ---- */
static inline V128 v_saddlv_u32_u16(V128 a) { V128 r; int16_t x[8]; int32_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < 8; i++) s += x[i]; r.lo = (uint32_t)s; r.hi = 0; return r; }
static inline V128 v_uaddlv_u32_u16(V128 a) { V128 r; uint16_t x[8]; uint32_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < 8; i++) s += x[i]; r.lo = (uint32_t)s; r.hi = 0; return r; }
/* lane-suffixed widening sums; the emitter calls these as (V128, int n).
   Accumulate in 64-bit so the widening result never overflows. */
static inline V128 v_uaddlv_u8(V128 a, int n) { V128 r; uint8_t x[16]; uint64_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < n && i < 16; i++) s += x[i]; r.lo = s; r.hi = 0; return r; }
static inline V128 v_uaddlv_u16(V128 a, int n) { V128 r; uint16_t x[8]; uint64_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < n && i < 8; i++) s += x[i]; r.lo = s; r.hi = 0; return r; }
static inline V128 v_uaddlv_u32(V128 a, int n) { V128 r; uint32_t x[4]; uint64_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < n && i < 4; i++) s += x[i]; r.lo = s; r.hi = 0; return r; }
static inline V128 v_saddlv_u8(V128 a, int n) { V128 r; int8_t x[16]; int64_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < n && i < 16; i++) s += x[i]; r.lo = (uint64_t)s; r.hi = 0; return r; }
static inline V128 v_saddlv_u16(V128 a, int n) { V128 r; int16_t x[8]; int64_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < n && i < 8; i++) s += x[i]; r.lo = (uint64_t)s; r.hi = 0; return r; }
static inline V128 v_saddlv_u32(V128 a, int n) { V128 r; int32_t x[4]; int64_t s = 0; int i; memcpy(x, &a, 16); for (i = 0; i < n && i < 4; i++) s += x[i]; r.lo = (uint64_t)s; r.hi = 0; return r; }

/* ---- pairwise across the whole register, scalar dest (FADDP Sd, Vn.2S) ---- */
static inline float v_faddp_pair_s(V128 a) { float x[2]; memcpy(x, &a, 8); return x[0] + x[1]; }

/* ---- SCVTF/UCVTF with immediate fraction bits (scalar) ---- */
static inline float v_scalbn_f32(float x, int e) { return scalbnf(x, e); }
static inline double v_scalbn_f64(double x, int e) { return scalbn(x, e); }

/* ---- FMOV vector immediates (broadcast of a 64-bit pattern) ---- */
static inline V128 v_fmov_imm_u64(uint64_t bits) { V128 r; r.lo = bits; r.hi = bits; return r; }
static inline V128 v_fmov_imm_u32(uint32_t bits) { V128 r; r.lo = (uint64_t)bits | ((uint64_t)bits << 32); r.hi = r.lo; return r; }

#endif /* A64_CTX_H */
