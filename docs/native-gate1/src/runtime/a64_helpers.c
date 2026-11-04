/* SplatNative runtime logging + helper implementations. */
#include "a64_ctx.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

static void lg(const char* fmt, ...);
static void dump_stack32(A64Ctx* c);

/* ------------------------------------------------------------------ *
 * Minimal service-object layer (kernel side of the sf/hipc client).   *
 *                                                                     *
 * svcConnectToNamedPort (imm 0x1f) must hand back a real handle: the  *
 * SDK wrapper stores the returned x1 into the caller's Handle*, and   *
 * that value is later passed to svcSendSyncRequest (imm 0x21) as the  *
 * session handle. Leaving it unset made the guest store the low 32    *
 * bits of the port name string as a "handle" (0x0f023058 = &"sm:"),    *
 * which is what the earlier log lines decoded as names.               *
 * ------------------------------------------------------------------ */
typedef struct {
    uint64_t handle;
    char name[40];
} SvcPort;

static SvcPort g_ports[64];
static unsigned g_port_n;
static uint64_t g_next_handle = 0x1000;

static void cstr40(char* dst, uint64_t p) {
    int i;
    dst[0] = 0;
    if (!p || IsBadReadPtr((const void*)(uintptr_t)p, 1)) return;
    for (i = 0; i < 39; i++) {
        unsigned char ch;
        if (IsBadReadPtr((const void*)(uintptr_t)(p + i), 1)) break;
        ch = *(const unsigned char*)(uintptr_t)(p + i);
        if (!ch || ch < 0x20 || ch > 0x7e) break;
        dst[i] = (char)ch;
    }
    dst[i] = 0;
}

static SvcPort* port_new(const char* name) {
    SvcPort* p;
    if (g_port_n >= sizeof(g_ports) / sizeof(g_ports[0])) return NULL;
    p = &g_ports[g_port_n++];
    p->handle = g_next_handle++;
    strncpy(p->name, name, sizeof(p->name) - 1);
    p->name[sizeof(p->name) - 1] = 0;
    return p;
}

static SvcPort* port_find(uint32_t h) {
    unsigned i;
    for (i = 0; i < g_port_n; i++)
        if ((uint32_t)g_ports[i].handle == h) return &g_ports[i];
    return NULL;
}

FILE* g_log = NULL;
uint64_t g_svc_hist[0x400];
uint64_t g_trace[262144];
unsigned g_trace_i;

void a64_hit_watch(A64Ctx* c) {
    lg("[watch] hit pc=%llx x30=%llx\n",
       (unsigned long long)c->pc, (unsigned long long)c->x[30]);
    lg("[watch] x0=%llx x1=%llx x2=%llx x3=%llx\n",
       (unsigned long long)c->x[0], (unsigned long long)c->x[1],
       (unsigned long long)c->x[2], (unsigned long long)c->x[3]);
    lg("[watch] x4=%llx x5=%llx x6=%llx x7=%llx\n",
       (unsigned long long)c->x[4], (unsigned long long)c->x[5],
       (unsigned long long)c->x[6], (unsigned long long)c->x[7]);
    if (!IsBadReadPtr((const void*)(uintptr_t)(c->tpidrro + 0x1f8), 8)) {
        unsigned long long tt =
            *(const unsigned long long*)(uintptr_t)(c->tpidrro + 0x1f8);
        if (!IsBadReadPtr((const void*)(uintptr_t)tt, 0x1b4))
            lg("[watch]   ThreadType=%llx tid=%08x\n", tt,
               *(const unsigned int*)(uintptr_t)(tt + 0x1b0));
    }
    for (int k = 0; k < 4; k++) {
        unsigned long long p = c->x[k];
        if (p < 0x08500000ull || p >= 0x40000000ull) continue;
        char buf[192];
        size_t n = 0;
        while (n < sizeof(buf) - 1 &&
               !IsBadReadPtr((const void*)(uintptr_t)(p + n), 1)) {
            char ch = ((const char*)p)[n];
            if (ch == 0) break;
            if (ch < 0x20 || ch > 0x7e) { n = 0; break; }
            buf[n++] = ch;
        }
        if (n) { buf[n] = 0; lg("[watch]   x%d str: %s\n", k, buf); }
        else if (p >= 0x08500000ull && p < 0x40000000ull &&
                 !IsBadReadPtr((const void*)(uintptr_t)p, 8))
            lg("[watch]   x%d u32=%08x u64=%llx\n", k,
               *(const unsigned int*)p, *(const unsigned long long*)p);
    }
}

static unsigned char g_cov_seen[0x20000];
void a64_cov_hit(A64Ctx* c) {
    extern uint64_t g_cov_lo;
    uint64_t rel = c->pc - g_cov_lo;
    if (rel >= 0x20000ull) return;
    if (g_cov_seen[rel]) return;
    g_cov_seen[rel] = 1;
    lg("[cov] %llx\n", (unsigned long long)c->pc);
}

void a64_mark_hit(A64Ctx* c) {
    lg("[mark] pc=%llx x0=%llx x1=%llx x2=%llx x3=%llx x30=%llx sp=%llx\n",
       (unsigned long long)c->pc, (unsigned long long)c->x[0],
       (unsigned long long)c->x[1], (unsigned long long)c->x[2],
       (unsigned long long)c->x[3], (unsigned long long)c->x[30],
       (unsigned long long)c->x[29]);
    a64_dump_trace();
    lg("[mark] stop at probe pc\n[done] exit\n");
    fflush(g_log);
    ExitProcess(0);
}

void a64_dump_bigtrace(unsigned n) {
    extern uint64_t g_bigtrace[65536];
    extern unsigned g_bigtrace_i;
    unsigned total = g_bigtrace_i;
    unsigned k;
    if (n > 65536u) n = 65536u;
    if (n > total) n = total;
    lg("[big] last %u dispatches\n", n);
    for (k = 0; k < n; k++)
        lg("[big]   %llx\n",
           (unsigned long long)g_bigtrace[(total - n + k) & 0xffffu]);
}

void a64_dump_trace(void) {
    unsigned total = g_trace_i;
    const char* all = getenv("SN_TRACE_ALL");
    /* fn_dispatch writes into a 1024-entry ring (index & 1023), so read the
       ring back with the same mask: the old dump walked the 262144-entry
       buffer from the top and printed stale zeros. */
    unsigned cap = all ? 262144u : 1024u;
    unsigned n = total < cap ? total : cap;
    lg("[trace] last %u fn entries (oldest first):\n", n);
    for (unsigned k = 0; k < n; k++) {
        unsigned idx = all ? ((total - n + k) & 262143u)
                           : ((total - n + k) & 1023u);
        lg("[trace]   %llx\n", (unsigned long long)g_trace[idx]);
    }
}

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

static void dump_fp_chain(A64Ctx* c) {
    uint64_t fp = c->x[29];
    lg("[fp] chain from x29=%llx x30=%llx\n",
       (unsigned long long)fp, (unsigned long long)c->x[30]);
    for (int i = 0; i < 24; i++) {
        if (fp < 0x20000000ull || fp >= 0x21000000ull) break;
        if (IsBadReadPtr((const void*)(uintptr_t)fp, 16)) break;
        uint64_t next = *(uint64_t*)(uintptr_t)fp;
        uint64_t lr = *(uint64_t*)(uintptr_t)(fp + 8);
        lg("[fp]   fp=%llx lr=%llx\n", (unsigned long long)fp,
           (unsigned long long)lr);
        if (next <= fp) break;
        fp = next;
    }
}


/* ---- guest-call helper (HLE -> recompiled guest code) -------------------
   The fs/sf client decodes the hipc reply header with its own decoder at sdk
   0xe799a5c and then looks for the reply packet at
   buf + (decoded count)*4, 16-byte aligned.  Echoing the request in place is
   only correct when that lands on the request packet, which is not true for
   every message family (the copy-handle requests place it further up the
   buffer).  Instead of guessing offsets, run the guest's own decoder on the
   reply buffer from inside the svc handler and answer exactly where the client
   will look.  Guest memory is mapped 1:1, so a host static buffer works as the
   decoder's scratch state struct. */
static A64Ctx g_call_save;
static uint32_t g_next_objid = 0x100;
static uint8_t g_hipc_state[0x40];

static uint64_t gh_call(A64Ctx* c, uint64_t pc, uint64_t a0, uint64_t a1) {
    const uint64_t sentinel = 0xdeadbeefdead0001ull;
    uint64_t r;
    int guard = 0;
    g_call_save = *c;
    c->x[0] = a0;
    c->x[1] = a1;
    c->x[2] = 0;
    c->x[30] = sentinel;
    c->pc = pc;
    while (c->pc != sentinel && c->pc != A64_STOP && guard++ < 4000000)
        fn_dispatch(c);
    r = c->x[0];
    *c = g_call_save;
    return r;
}

static uint64_t gh_call3(A64Ctx* c, uint64_t pc, uint64_t a0, uint64_t a1, uint64_t a2) {
    const uint64_t sentinel = 0xdeadbeefdead0001ull;
    uint64_t r;
    int guard = 0;
    g_call_save = *c;
    c->x[0] = a0;
    c->x[1] = a1;
    c->x[2] = a2;
    c->x[30] = sentinel;
    c->pc = pc;
    while (c->pc != sentinel && c->pc != A64_STOP && guard++ < 4000000)
        fn_dispatch(c);
    r = c->x[0];
    *c = g_call_save;
    return r;
}


/* Fill the reply packet wherever the client will look for it.  The client
   re-decodes the echoed hipc header with sdk 0xe799a5c and then reads a
   descriptor count out of that state struct; different sf code paths read
   different count fields (+0x21 / +0x29), so place the reply packet at the
   offset each of them implies.  In-place replies (offset == request packet)
   are already written by the caller. */
static void a64_dbg_parse_hipc(A64Ctx* c, uint64_t buf, const char* tag, const char* nm) {
    static uint8_t scratch[0x20];
    uint8_t info[0x40];
    uint64_t r;
    memset(info, 0, sizeof(info));
    memset(scratch, 0, sizeof(scratch));
    /* ParseHeader(accessor this, HipcMessageHeaderInfo* out, void* msg) */
    r = gh_call3(c, 0xe8513f8ull, (uint64_t)(uintptr_t)scratch,
                 (uint64_t)(uintptr_t)info, buf);
    lg("[hipc]%s %-8s %-9s parse=%llx type=%02x tag=%02x bytes=%04x stat=%02x sb=%02x rb=%02x xb=%02x\n",
       tag, nm, "", (unsigned long long)r, info[0xb], info[0xa],
       (unsigned)(info[8] | (info[9] << 8)), info[3], info[4], info[5], info[6]);
}

static void gh_hipc_fill_reply(A64Ctx* c, uint64_t buf, int reqpkt, uint32_t objid) {
    uint8_t pkt[0x40];
    int cand[2];
    int i, n = 0;
    if (reqpkt < 0) reqpkt = 0;
    if (IsBadReadPtr((const void*)(uintptr_t)(buf + (uint64_t)reqpkt), sizeof(pkt)))
        return;
    memcpy(pkt, (const void*)(uintptr_t)(buf + (uint64_t)reqpkt), sizeof(pkt));
    pkt[0x10] = 0x53; pkt[0x11] = 0x46; pkt[0x12] = 0x43; pkt[0x13] = 0x4f;
    pkt[0x14] = 1; pkt[0x15] = 0; pkt[0x16] = 0; pkt[0x17] = 0;
    memset(pkt + 0x20, 0, 8);
    /* A CMIF domain reply carries the returned object id at packet+0x20;
       0 means 'no object'. Callers that open a sub-session store that
       id in their object wrapper, so hand out a real one instead of a
       NULL the guest then dereferences. */
    if (objid) *(uint32_t*)(pkt + 0x20) = objid;
    memset(g_hipc_state, 0, sizeof(g_hipc_state));
    gh_call(c, 0xe799a5cull, (uint64_t)(uintptr_t)g_hipc_state, buf);
    cand[n++] = (int)(((((uint32_t)g_hipc_state[0x21]) << 2) + 0xfu) & ~0xfu);
    cand[n++] = (int)(((((uint32_t)g_hipc_state[0x29]) << 2) + 0xfu) & ~0xfu);
    for (i = 0; i < n; i++) {
        int rp = cand[i];
        if (rp <= 0 || rp == reqpkt || rp > 0x3c0) continue;
        if (IsBadWritePtr((void*)(uintptr_t)(buf + (uint64_t)rp), sizeof(pkt))) {
            lg("[svc]   reply slot tls+0x%x not writable\n", rp);
            continue;
        }
        /* not duplicated: extra SFCO headers corrupt out-params */
        lg("[svc]   reply packet also at tls+0x%x (state %02x/%02x)\n",
           rp, (unsigned)g_hipc_state[0x21], (unsigned)g_hipc_state[0x29]);
    }
}

/* Where does the client decode the reply magic?  nn::sf re-parses the echoed
   hipc header with sdk 0xe799a5c, which stores a packed HeaderInfo at
   struct+0x1a; its top byte (struct+0x21) is the word count the client scales
   by 4 and rounds up, and it then reads the CMIF magic at +0x10.  Compute that
   offset from the header we actually sealed instead of guessing, so messages
   with session handles and plain ones both land where the client looks. */
static int gh_hipc_reply_base(A64Ctx* c, uint64_t buf) {
    uint32_t n;
    int base;
    memset(g_hipc_state, 0, sizeof(g_hipc_state));
    gh_call(c, 0xe799a5cull, (uint64_t)(uintptr_t)g_hipc_state, buf);
    n = (uint32_t)g_hipc_state[0x21];
    base = (int)(((n << 2) + 0xfu) & ~0xfu);
    if (base < 0 || base > 0x3c0) base = 0;
    return base;
}

/* Replicates the client-side reply check exactly: nn::sf's cmif reader
   (sdk 0xe7c44c4 / 0xe7992a0) re-parses the buffer with 0xe799a5c, takes the
   message pointer at state+0x00 and the word count at state+0x21, then reads
   the reply magic from align16(ptr + count*4) + 0x10 and compares it with
   0x4f434653, returning 0x1a80a when it does not match.  Log and return that
   address as an offset from the message buffer, so replies land where the
   client actually looks instead of where we assume it looks. */
static int gh_hipc_magic_off(A64Ctx* c, uint64_t buf) {
    uint64_t ptr, a;
    uint32_t n, magic;
    int off;
    memset(g_hipc_state, 0, sizeof(g_hipc_state));
    gh_call(c, 0xe799a5cull, (uint64_t)(uintptr_t)g_hipc_state, buf);
    ptr = *(uint64_t*)(const void*)g_hipc_state;
    n = (uint32_t)g_hipc_state[0x21];
    a = (ptr + (uint64_t)n * 4ull + 0xfull) & ~0xfull;
    magic = IsBadReadPtr((const void*)(uintptr_t)(a + 0x10), 4)
            ? 0xdeadbeefu : *(uint32_t*)(const void*)(uintptr_t)(a + 0x10);
    off = (int)(a - buf);
    lg("[hipc] magic lookup: ptr=%llx n=%u magic_at=%llx (tls+%x) magic=%08x%s\n",
       (unsigned long long)ptr, (unsigned)n, (unsigned long long)(a + 0x10), off,
       magic, magic == 0x4f434653u ? " OK" : "");
    return off;
}

/* Offset from the message buffer where the client expects the reply packet. */
static int gh_hipc_reply_off(A64Ctx* c, uint64_t buf) {
    uint32_t cnt;
    memset(g_hipc_state, 0, sizeof(g_hipc_state));
    gh_call(c, 0xe799a5cull, (uint64_t)(uintptr_t)g_hipc_state, buf);
    cnt = (uint32_t)g_hipc_state[0x21];
    return (int)(((cnt << 2) + 0xfull) & ~0xfull);
}

/* Place the reply packet ('SFCO', version 1, Result 0, request echoed) where
   the client's decoder will look for it. */
static void gh_hipc_place_reply(A64Ctx* c, uint64_t buf, int reqpkt) {
    uint8_t pkt[0x40];
    int rep = gh_hipc_reply_off(c, buf);
    if (reqpkt < 0) reqpkt = 0;
    if (rep < 0 || rep > 0x3c0) rep = reqpkt;
    if (IsBadReadPtr((const void*)(uintptr_t)(buf + (uint64_t)reqpkt), sizeof(pkt)) ||
        IsBadWritePtr((void*)(uintptr_t)(buf + (uint64_t)rep), sizeof(pkt))) {
        lg("[svc]   reply placement failed (req=%x rep=%x)\n", reqpkt, rep);
        return;
    }
    memcpy(pkt, (const void*)(uintptr_t)(buf + (uint64_t)reqpkt), sizeof(pkt));
    pkt[0x10] = 0x53; pkt[0x11] = 0x46; pkt[0x12] = 0x43; pkt[0x13] = 0x4f;  /* SFCO */
    pkt[0x14] = 1; pkt[0x15] = 0; pkt[0x16] = 0; pkt[0x17] = 0;
    memset(pkt + 0x20, 0, 8);
    memcpy((void*)(uintptr_t)(buf + (uint64_t)rep), pkt, sizeof(pkt));
    lg("[svc]   reply packet: req tls+0x%x -> rep tls+0x%x ('SFCO' at tls+0x%x)\n", reqpkt, rep, rep + 0x10);
}

static void dump_tls(const char* why, uint64_t tls, int words) {
    char line[1100];
    size_t n = 0;
    int i;
    if (IsBadReadPtr((const void*)(uintptr_t)tls, (size_t)words * 4)) return;
    for (i = 0; i < words && n + 12 < sizeof(line); i++)
        n += (size_t)snprintf(line + n, sizeof(line) - n, " %08x",
                              *(const uint32_t*)(uintptr_t)(tls + (uint64_t)i * 4));
    lg("[svc]   tls %s:%s\n", why, line);
}

/* Ad-hoc probe used while tracing a single call site: tag plus the registers
   and stack slots the surrounding code is about to branch on. */
#ifdef A64_ADDR_GUARD
void a64_bad_addr_dbg(A64Ctx* c, uint64_t a, int wr) {
    lg("[badaddr] %s addr=%016llx pc=%016llx x30=%016llx sp=%016llx\n",
       wr ? "st" : "ld", (unsigned long long)a, (unsigned long long)c->pc,
       (unsigned long long)c->x[30], (unsigned long long)c->x[31]);
    ExitProcess(77);
}
#endif

void a64_log6(A64Ctx* c, int tag, uint64_t a, uint64_t b, uint64_t d,
              uint64_t e, uint64_t f) {
    lg("[probe6] tag=%x pc=%llx a=%llx b=%llx d=%llx e=%llx f=%llx\n", tag,
       (unsigned long long)c->pc, (unsigned long long)a, (unsigned long long)b,
       (unsigned long long)d, (unsigned long long)e, (unsigned long long)f);
}

uint64_t g_st_watch1, g_st_watch2;
void a64_st_watch_hit(A64Ctx* c, uint64_t a, uint64_t v, int size) {
    lg("[stwatch] %llx <- %llx (%d B) pc=%llx x30=%llx\n",
       (unsigned long long)a, (unsigned long long)v, size,
       (unsigned long long)c->pc, (unsigned long long)c->x[30]);
}

void a64_dump_mem(const char* tag, uint64_t addr, int words) {
    char buf[16 * 10 + 1];
    int i, n = 0;
    if (words > 16) words = 16;
    if (IsBadReadPtr((const void*)(uintptr_t)addr, (size_t)words * 4)) {
        lg("[dump] %s @%llx unreadable\n", tag, (unsigned long long)addr);
        return;
    }
    for (i = 0; i < words; i++)
        n += snprintf(buf + n, sizeof(buf) - (size_t)n, "%08x ",
                      *(uint32_t*)(uintptr_t)(addr + (uint64_t)i * 4));
    lg("[dump] %s @%llx: %s\n", tag, (unsigned long long)addr, buf);
}

void a64_log_globals(const char* why) {
    uint64_t v[7];
    int i;
    static const uint64_t addr[7] = {0xf2883a0ull, 0xf2883a4ull, 0xf2883a8ull,
                                     0xf2883b0ull, 0xf2883b8ull, 0xf2883c0ull,
                                     0xf2883c8ull};
    for (i = 0; i < 7; i++)
        v[i] = IsBadReadPtr((const void*)(uintptr_t)addr[i], 8)
                   ? 0xdeadbeefull
                   : *(uint64_t*)(uintptr_t)addr[i];
    lg("[globals] %s 3a0=%016llx 3a4=%016llx 3a8=%016llx 3b0=%016llx "
       "3b8=%016llx 3c0=%016llx 3c8=%016llx\n", why,
       (unsigned long long)v[0], (unsigned long long)v[1],
       (unsigned long long)v[2], (unsigned long long)v[3],
       (unsigned long long)v[4], (unsigned long long)v[5],
       (unsigned long long)v[6]);
}

/* fs allocator hook installed at 0xf27c898, plus the fs heap size the sdk keeps
   next to it; logged where nn::fs::detail::Allocate decides to throw/return. */
void a64_log_fsalloc(A64Ctx* c) {
    uint64_t fn = *(uint64_t*)0xf27c898ull;
    uint64_t a0 = 0, a8 = 0;
    if (!IsBadReadPtr((const void*)0xf27c8a0ull, 16)) {
        a0 = *(uint64_t*)0xf27c8a0ull;
        a8 = *(uint64_t*)0xf27c8a8ull;
    }
    lg("[fsalloc] allocator=%llx size=%llx ret=%llx c8a0=%llx c8a8=%llx "
       "malloc_slot=%llx\n",
       (unsigned long long)fn, (unsigned long long)c->x[19],
       (unsigned long long)c->x[0], (unsigned long long)a0,
       (unsigned long long)a8, (unsigned long long)*(uint64_t*)0xf26bcc8ull);
}

void a64_log_probe(A64Ctx* c, int tag) {
    uint64_t sp = c->x[31];
    uint64_t s8 = 0, s10 = 0;
    if (!IsBadReadPtr((const void*)(uintptr_t)(sp + 8), 16)) {
        s8 = *(uint64_t*)(uintptr_t)(sp + 8);
        s10 = *(uint64_t*)(uintptr_t)(sp + 16);
    }
    lg("[probe] tag=%x pc=%llx x0=%llx x1=%llx x8=%llx x19=%llx x20=%llx "
       "sp8=%llx sp10=%llx\n",
       tag, (unsigned long long)c->pc, (unsigned long long)c->x[0],
       (unsigned long long)c->x[1], (unsigned long long)c->x[8],
       (unsigned long long)c->x[19], (unsigned long long)c->x[20],
       (unsigned long long)s8, (unsigned long long)s10);
    if (tag == 0xe7c2b24) a64_log_globals("at fs proxy query");
}

void a64_log_sm(A64Ctx* c, int tag) {
    static unsigned n_;
    lg("[sm] #%u tag=%d x0=%llx x1=%llx x8=%llx x20=%llx\n",
       ++n_, tag, (unsigned long long)c->x[0], (unsigned long long)c->x[1],
       (unsigned long long)c->x[8], (unsigned long long)c->x[20]);
}

void a64_log_arb(A64Ctx* c) {
    static unsigned n_;
    unsigned k = ++n_;
    uint64_t t0 = *(uint64_t*)(uintptr_t)0x30000000ull;
    uint64_t t8 = *(uint64_t*)(uintptr_t)0x30000008ull;
    lg("[arb] #%u x0=%llx x8=%llx tlr0=%llx tlr8=%llx tpidrro=%llx\n",
       k, (unsigned long long)c->x[0], (unsigned long long)c->x[8],
       (unsigned long long)t0, (unsigned long long)t8,
       (unsigned long long)c->tpidrro);
}

void a64_log_throw(A64Ctx* c) {
    static unsigned n_;
    unsigned k = ++n_;
    if (k <= 1) {
        dump_fp_chain(c);
        if (!IsBadReadPtr((const void*)(uintptr_t)c->x[29], 0x100)) {
            uint64_t* q = (uint64_t*)(uintptr_t)c->x[29];
            int i;
            for (i = 0; i < 32; i++)
                lg("[stk] %d %llx\n", i, (unsigned long long)q[i]);
        }
        char buf[700];
        size_t bn = 0;
        int i;
        for (i = 49; i >= 1; i--) {
            uint64_t pc = g_trace[(g_trace_i - (unsigned)i) & 262143u];
            bn += (size_t)_snprintf(buf + bn, sizeof(buf) - bn, " %llx",
                                    (unsigned long long)pc);
            if (bn > sizeof(buf) - 24) break;
        }
        {
            uint64_t g = 0, o = 0;
            if (!IsBadReadPtr((const void*)(uintptr_t)0xf267dc8ull, 8))
                g = *(uint64_t*)(uintptr_t)0xf267dc8ull;
            if (g && !IsBadReadPtr((const void*)(uintptr_t)(g + 0x98), 8))
                o = *(uint64_t*)(uintptr_t)(g + 0x98);
            lg("[abortobj] g=%llx g98=%llx slot_4ac70=%llx slot_460c0=%llx slot_4a7d0=%llx\n",
               (unsigned long long)g, (unsigned long long)o,
               (unsigned long long)*(uint64_t*)(uintptr_t)0xf26ef78ull,
               (unsigned long long)*(uint64_t*)(uintptr_t)0xf26c9a0ull,
               (unsigned long long)*(uint64_t*)(uintptr_t)0xf26ed78ull);
            {
                uint64_t w[8]; int q;
                for (q = 0; q < 8; q++)
                    w[q] = IsBadReadPtr((const void*)(uintptr_t)(0xf267dc8ull + (uint64_t)q * 8), 8)
                               ? 0 : *(uint64_t*)(uintptr_t)(0xf267dc8ull + (uint64_t)q * 8);
                a64_str_dbg("gname0", *(uint64_t*)(uintptr_t)0xf2339b0ull);
            a64_str_dbg("gname1", 0xf2339b0ull);
            {
                uint64_t c0 = 0, c1 = 0, c2 = 0, c3 = 0;
                uint64_t b = 0xf290018ull + 0x90ull;
                if (!IsBadReadPtr((const void*)(uintptr_t)b, 32)) {
                    c0 = *(uint64_t*)(uintptr_t)b;
                    c1 = *(uint64_t*)(uintptr_t)(b + 8);
                    c2 = *(uint64_t*)(uintptr_t)(b + 16);
                    c3 = *(uint64_t*)(uintptr_t)(b + 24);
                }
                lg("[abortcont] %llx %llx %llx %llx\n",
                   (unsigned long long)c0, (unsigned long long)c1,
                   (unsigned long long)c2, (unsigned long long)c3);
            }
            lg("[abortglob] %llx %llx %llx %llx %llx %llx %llx %llx\n",
                   (unsigned long long)w[0], (unsigned long long)w[1],
                   (unsigned long long)w[2], (unsigned long long)w[3],
                   (unsigned long long)w[4], (unsigned long long)w[5],
                   (unsigned long long)w[6], (unsigned long long)w[7]);
            }
        }
        lg("[abortchain]\n%s\n", buf);
    }
    uint64_t p = c->x[0];
    lg("[throw] #%u x0=%llx x1=%llx x2=%llx x3=%llx x4=%llx x30=%llx x29=%llx\n",
       k, (unsigned long long)c->x[0], (unsigned long long)c->x[1],
       (unsigned long long)c->x[2], (unsigned long long)c->x[3],
       (unsigned long long)c->x[4], (unsigned long long)c->x[30],
       (unsigned long long)c->x[29]);
    if (p >= 0x08500000ull && p < 0x100000000ull &&
        !IsBadReadPtr((const void*)(uintptr_t)p, 32))
        lg("[throw]   [x0]=%llx %llx %llx %llx\n",
           (unsigned long long)*(uint64_t*)(uintptr_t)p,
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 8),
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 16),
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 24));
}

void a64_log_range(A64Ctx* c) {
    static unsigned hits;
    unsigned n_ = ++hits;
    uint64_t p0 = c->x[0], p1 = c->x[1];
    lg("[range] hit %u @e845148 x8=%llx x9=%llx x11=%llx x0=%llx x1=%llx\n",
       n_, (unsigned long long)c->x[8], (unsigned long long)c->x[9],
       (unsigned long long)c->x[11], (unsigned long long)p0,
       (unsigned long long)p1);
    if (p0 >= 0x08500000ull && p0 < 0x100000000ull &&
        !IsBadReadPtr((const void*)(uintptr_t)p0, 8))
        lg("[range]   [x0]=%llx\n", (unsigned long long)*(uint64_t*)(uintptr_t)p0);
    if (p1 >= 0x08500000ull && p1 < 0x100000000ull &&
        !IsBadReadPtr((const void*)(uintptr_t)p1, 8))
        lg("[range]   [x1]=%llx\n", (unsigned long long)*(uint64_t*)(uintptr_t)p1);
    {
        uint64_t pb = *(uint64_t*)(uintptr_t)0xf267de8ull;
        uint64_t pe = *(uint64_t*)(uintptr_t)0xf267de0ull;
        uint64_t vb = 0, ve = 0;
        if (pb >= 0x08500000ull && pb < 0x100000000ull &&
            !IsBadReadPtr((const void*)(uintptr_t)pb, 8))
            vb = *(uint64_t*)(uintptr_t)pb;
        if (pe >= 0x08500000ull && pe < 0x100000000ull &&
            !IsBadReadPtr((const void*)(uintptr_t)pe, 8))
            ve = *(uint64_t*)(uintptr_t)pe;
        lg("[range]   base*=%llx end*=%llx -> [%llx, %llx]\n",
           (unsigned long long)pb, (unsigned long long)pe,
           (unsigned long long)vb, (unsigned long long)ve);
    }

}

static unsigned g_arb_log = 0;

/* Per-service counter for the verbose IPC dumps: a service that the SDK
   retries in a loop otherwise fills the log (22M lines in 4 minutes). */
typedef struct { char name[24]; unsigned n; } IpcDumpCount;
static IpcDumpCount g_ipc_dumps[32];

static unsigned g_ipc_dump_count(const char* nm) {
    int i, free_i = -1;
    for (i = 0; i < 32; i++) {
        if (g_ipc_dumps[i].name[0] == 0) { if (free_i < 0) free_i = i; continue; }
        if (strncmp(g_ipc_dumps[i].name, nm, sizeof(g_ipc_dumps[i].name) - 1) == 0)
            return g_ipc_dumps[i].n++;
    }
    if (free_i >= 0) {
        strncpy(g_ipc_dumps[free_i].name, nm, sizeof(g_ipc_dumps[free_i].name) - 1);
        g_ipc_dumps[free_i].n = 1;
    }
    return 0;
}



/* ---- romfs content model -------------------------------------------------
   `rom:` is served from the local extracted tree (SN_ROMFS, default
   E:\\Splatoon Games). The mount-cache answer the guest asks for is derived
   from that real tree - entry counts and byte totals measured once - instead
   of a made-up constant. No game bytes are read here, only directory entries.
   ------------------------------------------------------------------------- */
static const char* g_romfs_root;
static uint64_t g_romfs_files, g_romfs_dirs, g_romfs_bytes, g_romfs_meta;

static void gh_romfs_walk(const char* dir, int depth) {
    char pat[1024];
    WIN32_FIND_DATAA fd;
    HANDLE h;
    if (depth > 24) return;
    snprintf(pat, sizeof pat, "%s\\*", dir);
    h = FindFirstFileA(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        char sub[1024];
        if (!strcmp(fd.cFileName, ".") || !strcmp(fd.cFileName, "..")) continue;
        snprintf(sub, sizeof sub, "%s\\%s", dir, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            g_romfs_dirs++;
            gh_romfs_walk(sub, depth + 1);
        } else {
            g_romfs_files++;
            g_romfs_bytes += ((uint64_t)fd.nFileSizeHigh << 32) | fd.nFileSizeLow;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

static uint64_t gh_romfs_meta_size(void) {
    static int done = 0;
    if (done) return g_romfs_meta;
    done = 1;
    if (!g_romfs_root) {
        const char* e = getenv("SN_ROMFS");
        g_romfs_root = (e && *e) ? e : "E:\\Splatoon Games";
    }
    gh_romfs_walk(g_romfs_root, 0);
    /* romfs metadata = one directory entry (0x18) per dir, one file entry
       (0x20) per file, plus the 0x50 header, 8-byte aligned. */
    g_romfs_meta = (g_romfs_files * 0x20ull + g_romfs_dirs * 0x18ull + 0x50ull + 7ull) & ~7ull;
    if (g_romfs_meta < 0x10000ull) g_romfs_meta = 0x10000ull;
    lg("[romfs] tree '%s': files=%llu dirs=%llu bytes=%llu meta=%llu\n",
       g_romfs_root, (unsigned long long)g_romfs_files,
       (unsigned long long)g_romfs_dirs, (unsigned long long)g_romfs_bytes,
       (unsigned long long)g_romfs_meta);
    return g_romfs_meta;
}

void a64_svc(A64Ctx* c, uint64_t imm) {
    c->svc_count++;
    if (imm < 0x400) g_svc_hist[imm]++;
    static uint64_t shown;
    if (shown < 64 || (c->svc_count & 0xfff) == 0) {
        shown++;
        lg("[svc] #%llx at pc=%llx lr=%llx (total %llu)\n",
           (unsigned long long)imm, (unsigned long long)c->pc,
           (unsigned long long)c->x[30],
           (unsigned long long)c->svc_count);
    }
    if (imm == 0x06) {   /* QueryMemory(MemoryInfo* out=x0, u32* page=x1, u64 addr=x2) */
        uint64_t out = c->x[0], page = c->x[1], addr = c->x[2];
        uint64_t base = 0, size = 0;
        uint32_t type = 0, attr = 0, perm = 0;
        if (addr >= 0x08500000ull && addr < 0x08506000ull) {
            base = 0x08500000ull; size = 0x6000ull; type = 3; perm = 3;
        } else if (addr >= 0x08506000ull && addr < 0x0eb7e000ull) {
            base = 0x08506000ull; size = 0x61f8000ull; type = 3; perm = 3;
        } else if (addr >= 0x0e6fe000ull && addr < 0x0f34d000ull) {
            base = 0x0e6fe000ull; size = 0xc55000ull; type = 3; perm = 3;
        } else if (addr >= 0x20000000ull && addr < 0x21000000ull) {
            base = 0x20000000ull; size = 0x1000000ull; type = 9; perm = 3;
        } else if (addr >= 0x40000000ull && addr < 0x60000000ull) {
            base = addr & ~0xFFFFull; size = 0x10000ull; type = 5; perm = 3;
        } else if (addr >= 0x60000000ull && addr < 0x70000000ull) {
            /* Thread/stack-guard region: reserved VA, no mapping until the SDK
               maps a stack alias.  AddressSpaceAllocatorBase's "is this range
               free" predicate (0xe847884) rejects any range whose QueryMemory
               type is not 0, so reporting it mapped made every stack alias
               allocation fail and nn::os abort in MapAliasStack. */
            base = 0x60000000ull; size = 0x10000000ull; type = 0;
        } else if (addr >= 0x90000000ull && addr < 0xd0000000ull) {
            base = 0x90000000ull; size = 0x40000000ull; type = 5; perm = 3;
        } else {
            base = addr & ~0xFFFFull; size = 0x10000ull; type = 0;
        }
        if (out && !IsBadWritePtr((void*)(uintptr_t)out, 0x20)) {
            *(uint64_t*)(uintptr_t)out = base;
            *(uint64_t*)(uintptr_t)(out + 8) = size;
            *(uint32_t*)(uintptr_t)(out + 0x10) = type;
            *(uint32_t*)(uintptr_t)(out + 0x14) = attr;
            *(uint32_t*)(uintptr_t)(out + 0x18) = perm;
            *(uint32_t*)(uintptr_t)(out + 0x1c) = 0;
        }
        if (page && !IsBadWritePtr((void*)(uintptr_t)page, 4))
            *(uint32_t*)(uintptr_t)page = 0;
        c->x[0] = 0;
        c->x[1] = 0;
        lg("[svc] QueryMemory(%llx) -> base=%llx size=%llx type=%u\n",
           (unsigned long long)addr, (unsigned long long)base,
           (unsigned long long)size, (unsigned)type);
        return;
    }
    if (imm == 0x29) {   /* GetInfo(u64* out, InfoType id, Handle, u64) */
        uint64_t out = c->x[0], id = c->x[1], val = 0;
        switch (id) {
        case 0: val = 0xfull; break;         /* AllowedCpuIdBitmask */
        case 1: val = 0x20000000ull; break;  /* alias region address */
        case 2: val = 0x01000000ull; break;  /* alias region size    */
        case 3: val = 0x90000000ull; break;  /* heap region address  */
        case 4: val = 0x40000000ull; break;  /* heap region size     */
        case 0xe: val = 0x60000000ull; break;/* StackRegionAddress   */
        case 0xf: val = 0x10000000ull; break;/* StackRegionSize      */
        case 5: val = 0x20000000ull; break;  /* total memory size    */
        case 6: val = 0x10000000ull; break;  /* used memory size     */
        default: val = 0; break;
        }
        if (out && out < 0x100000000ull &&
            !IsBadReadPtr((const void*)(uintptr_t)out, 8))
            *(uint64_t*)(uintptr_t)out = val;
        c->x[0] = 0;
        c->x[1] = val;
        lg("[host] GetInfo(id=%llu out=%llx) -> %llx\n",
           (unsigned long long)id, (unsigned long long)out,
           (unsigned long long)val);
        return;
    }
    if (imm == 0x1f) {   /* ConnectToNamedPort(Handle* out=x0, const char* name=x1) */
        char nm[40];
        SvcPort* p;
        cstr40(nm, c->x[1]);
        p = port_new(nm);
        lg("[svc] ConnectToNamedPort('%s') out=%llx -> h=%llx\n", nm,
           (unsigned long long)c->x[0],
           (unsigned long long)(p ? p->handle : 0));
        c->x[0] = 0;                       /* Result success */
        c->x[1] = p ? p->handle : 0;       /* wrapper stores w1 into *out */
        return;
    }
    if (imm == 0x21) {   /* SendSyncRequest(Handle h=x0); message lives at TPIDRRO */
        uint64_t tls = c->tpidrro;
        uint32_t h = (uint32_t)c->x[0];
        SvcPort* p = port_find(h);
        char nm[40];
        uint32_t w0 = 0, w4 = 0;
        nm[0] = 0;
        if (p) strncpy(nm, p->name, sizeof(nm) - 1), nm[sizeof(nm) - 1] = 0;
        if (!IsBadReadPtr((const void*)(uintptr_t)tls, 0x40)) {
            w0 = *(uint32_t*)(uintptr_t)tls;
            w4 = *(uint32_t*)(uintptr_t)(tls + 4);
        }
        {
            static int once = 0;
            if (!once) {
                const uint64_t slots[8] = { 0xf26d8c0ull, 0xf26d8c8ull,
                                            0xf26d248ull, 0xf26d530ull,
                                            0xf26d8b8ull, 0xf26cd50ull,
                                            0xf26d948ull, 0xf26d8d0ull };
                uint64_t v[8];
                int i;
                once = 1;
                for (i = 0; i < 8; i++)
                    v[i] = IsBadReadPtr((const void*)(uintptr_t)slots[i], 8) ? 0ull
                          : *(uint64_t*)(uintptr_t)slots[i];
                if (!IsBadWritePtr((void*)(uintptr_t)0xe799ee0ull, 4)) {
                    *(uint32_t*)(uintptr_t)0xe799ee0ull = 0xD4000FC1u;  /* svc #0x7e */
                    lg("[ipc] abort probe armed at e799ee0@NL@");
                } else {
                    lg("[ipc] abort probe NOT writable at e799ee0@NL@");
                }
                lg("[ipc] thunk slots %llx %llx %llx %llx %llx %llx %llx %llx\n",
                   (unsigned long long)v[0], (unsigned long long)v[1],
                   (unsigned long long)v[2], (unsigned long long)v[3],
                   (unsigned long long)v[4], (unsigned long long)v[5],
                   (unsigned long long)v[6], (unsigned long long)v[7]);
                {
                    const uint64_t s3[5] = { 0xf26f208ull, 0xf26cd58ull,
                                             0xf26f0d8ull, 0xf26f0e0ull,
                                             0xf26c708ull };
                    uint64_t w3[5];
                    int z;
                    for (z = 0; z < 5; z++)
                        w3[z] = IsBadReadPtr((const void*)(uintptr_t)s3[z], 8) ? 0ull
                                : *(uint64_t*)(uintptr_t)s3[z];
                    lg("[ipc] extra slots %llx %llx %llx %llx %llx\n",
                       (unsigned long long)w3[0], (unsigned long long)w3[1],
                       (unsigned long long)w3[2], (unsigned long long)w3[3],
                       (unsigned long long)w3[4]);
                }
            }
        }
        if (strncmp(nm, "fsp-srv", 7) == 0 || strncmp(nm, "sm:", 3) == 0) {
            unsigned k = g_ipc_dump_count(strcat(strcpy((char[16]){0}, nm), "!st"));
            if (k < 6 && !IsBadReadPtr((const void*)(uintptr_t)0xf288400ull, 1))
                lg("[fsstate] %s once=%02x appinit=%08x lib=%02x dfc=%llx live=%llx\n", nm,
                   (unsigned)*(uint8_t*)(uintptr_t)0xf288400ull,
                   (unsigned)*(uint32_t*)(uintptr_t)0xf2883b0ull,
                   (unsigned)*(uint8_t*)(uintptr_t)0xf288980ull,
                   (unsigned long long)*(uint64_t*)(uintptr_t)0xf2883b8ull,
                   (unsigned long long)*(uint64_t*)(uintptr_t)0xf2883c0ull);
        }
        {
            unsigned n = g_ipc_dump_count(nm);
            if (n < 12)
                lg("[svc] SendSyncRequest(h=%x '%s') x1=%llx x2=%llx hdr=%08x/%08x\n",
                   h, nm, (unsigned long long)c->x[1], (unsigned long long)c->x[2],
                   w0, w4);
            if (n < 12) {
                dump_tls("req", tls, 80);
                a64_dbg_parse_hipc(c, tls, "req", nm);
                dump_fp_chain(c);
            }
        }
        /* Two message families arrive here and they read their reply from
           different places, so classify from the message itself:
             - cmif command to a service: the request packet starts with
               'SFCI' (0x49434653); the client re-parses the buffer (sdk
               0xe799a5c/0xe7c4348 + 0xe799b10) and requires the reply packet
               at that same offset to start with 'SFCO' (0x4f434653).
             - raw service-manager request (no packet): the sdk helper at
               0xe8570dc reads the Result at tls+8 (handshake) or, for the
               GetService helper at 0xe857208, the new handle at tls+0xc and
               the Result at tls+0x10. */
        {
            int off = -1, o;
            for (o = 8; o + 0x18 <= 0x80; o += 4)
                if (*(uint32_t*)(uintptr_t)(tls + (uint64_t)o) == 0x49434653u) {
                    off = o;
                    break;
                }
            if (off >= 0) {
                /* A CMIF domain reply returns an object id at packet+0x20
                   (message+0x10); 0 means 'no object'. The applet entry point
                   hands that id to its caller, which stores it in a global and
                   dereferences it, so give it a real one. */
                /* Every service that opens sub-sessions needs a real domain object id;
   returning 0 leaves the client holding a null object, which is what
   nn::fs::detail::GetFileSystemProxyServiceObject asserts on. */
                /* CMIF control commands arrive as ordinary commands here:
                   fsp-srv cmd 3 = QueryPointerBufferSize (u16 out),
                   cmd 0 = ConvertSessionToDomain (returns the object id). */
                uint32_t cmd = *(uint32_t*)(uintptr_t)(tls + (uint64_t)off + 8);
                {
                    uint32_t w1 = *(uint32_t*)(uintptr_t)(tls + 4);
                    uint32_t tag16 = *(uint32_t*)(uintptr_t)tls & 0xffffu;
                    uint32_t req_nw = w1 & 0x3ffu;
                    uint32_t hdr_w2 = *(uint32_t*)(uintptr_t)(tls + 8);
                    uint32_t hdr_w3 = *(uint32_t*)(uintptr_t)(tls + 12);
                    int is_desc = (w1 & 0x80000000u) != 0;
                    int is_fsp = strcmp(nm, "fsp-srv") == 0;
                    uint32_t oid = (strcmp(nm, "appletOE") == 0 ||
                                    (is_fsp && cmd == 0u)) ? g_next_objid++ : 0;
                    uint32_t nw;
                    int base;
                    /* A descriptor-carrying request keeps its CMIF packet after
                       the descriptor list; a reply that carries no buffers must
                       drop that list, so clear the whole data area first. */
                    if (is_desc && !IsBadWritePtr((void*)(uintptr_t)(tls + 0x10), 0xf0))
                        memset((void*)(uintptr_t)(tls + 0x10), 0, 0xf0);
                    /* Plain messages: the generic sf sender (sdk 0xe850e10)
                       reads the reply magic at a fixed msg+0x10, and a 2-word
                       reply header is what nn::sf expects there.
                       Descriptor-carrying messages: the fs proxy derives the
                       offset from the header it re-parses, so declare the
                       request's own word count and ask the parser where it
                       will look. */
                    nw = is_desc ? (req_nw ? req_nw : (4u + (oid ? 1u : 0u)))
                                 : (2u + (oid ? 1u : 0u));
                    /* Seal the echoed hipc header as a reply before asking the
                       client-side parser where the CMIF magic belongs: that
                       offset is derived from this very header. */
                    *(uint32_t*)(uintptr_t)tls = tag16;
                    *(uint32_t*)(uintptr_t)(tls + 4) = (nw & 0x3ffu) | 0x400u;
                    if ((hdr_w2 & 0xffu) != 0) {   /* clone reply: session handle slots */
                        *(uint32_t*)(uintptr_t)(tls + 8) = hdr_w2 & 0xffu;
                        *(uint32_t*)(uintptr_t)(tls + 12) = hdr_w3;
                    }
                    /* Where the client reads the reply magic (observed, three
                       message shapes on this boot):
                         plain cmif packet at msg+0x10  -> magic at 0x10
                         domain packet at msg+0x20      -> magic at 0x20
                         packet at msg+0x30 + descriptors -> magic at 0x20
                       i.e. the magic sits where the request packet was, minus
                       one 16-byte block when the reply drops the descriptor
                       list. The parser-derived offset (gh_hipc_reply_base) is
                       logged alongside and agrees for the descriptor case. */
                    {   int parsed = gh_hipc_reply_base(c, tls);
                        int want = gh_hipc_magic_off(c, tls);
                        /* Put the reply magic where the client's reader looks
                           for it; fall back to the request-relative rule when
                           that address is outside the message buffer. */
                        /* The verified request-relative rule stays the
                           placement rule; the client-side lookup above is
                           logged for diagnosis only (it disagrees for plain
                           messages, where the client reads at msg+0x10). */
                        base = (int)off - 0x10 - (is_desc ? 0x10 : 0);
                        if (base < 0) base = 0;
                        lg("[svc]   base off=%x desc=%d -> base=%x parsed=%x\n", off, is_desc, base, parsed);
                    }

                    if (base < 0) base = 0;
                    lg("[svc]   reply base from req off: off=%x desc=%d base=%x parsed=%x\n",
                       off, is_desc, base, gh_hipc_reply_base(c, tls));
                    if (!IsBadWritePtr((void*)(uintptr_t)(tls + (uint64_t)base), 0x40)) {
                        uint8_t* rp = (uint8_t*)(uintptr_t)(tls + (uint64_t)base);
                        rp[0x10] = 0x53; rp[0x11] = 0x46; rp[0x12] = 0x43; rp[0x13] = 0x4f;
                        rp[0x14] = 1; rp[0x15] = 0; rp[0x16] = 0; rp[0x17] = 0;
                        *(uint32_t*)(rp + 0x18) = 0;
                        *(uint32_t*)(rp + 0x1c) = 0;
                        if (oid) *(uint32_t*)(rp + 0x20) = oid;
                        if (is_fsp && cmd == 3u) *(uint16_t*)(rp + 0x20) = 0x1000u;
                        /* fsp-srv cmd 0xc8: the mount-cache size the guest
                           asks for before it mounts rom:. Answered from the
                           real tree, not a constant (see gh_romfs_meta_size). */
                        if (is_fsp && cmd == 0xc8u) {
                            uint64_t mc = gh_romfs_meta_size();
                            *(uint64_t*)(rp + 0x20) = mc;
                /* Seal this as a reply the way the accepted fsp-srv replies
                   are sealed (2 data words, message type 1), so the client's
                   invoke sees a reply header and not a request header. */
                *(uint32_t*)(uintptr_t)(tls + 4) = 2u | 0x400u;
                            lg("[romfs] fsp-srv cmd 0xc8 -> mount cache size %llu\n",
                               (unsigned long long)mc);
                        }
                        if ((strncmp(nm, "applet", 6) == 0 || strcmp(nm, "am") == 0 ||
                             strcmp(nm, "apm") == 0) && oid &&
                            !IsBadWritePtr((void*)(uintptr_t)(rp + 0x20), 0x40)) {
                            memset((void*)(uintptr_t)(rp + 0x20), 0, 0x40);
                            *(uint32_t*)(rp + 0x20) = oid;
                        }
                    }
                    lg("[svc]   cmif reply on '%s': req pkt tls+0x%x desc=%d nw=%x base=tls+0x%x oid=%x\n",
                       nm, off, is_desc, nw, base, oid);
                    a64_dbg_parse_hipc(c, tls, "rep2", nm);
                }
                /* A real server seals its reply with its own header: the tag
                   is echoed, buffer counts are the reply's (none here), and
                   word1 declares the CMIF reply length with message type 1.
                   Only do this for messages that carry no buffers/handles,
                   where the shape is unambiguous. */
                dump_tls("rep", tls, 60);
                a64_dbg_parse_hipc(c, tls, "rep", nm);
                c->x[0] = 0;
                return;
            }
        }
        if (h && p && w4 == 2) {
            char svc[40];
            cstr40(svc, tls + 8);
            if (svc[0]) {
                SvcPort* np = port_new(svc);
                uint32_t nh = np ? (uint32_t)np->handle : 0;
                if (!IsBadWritePtr((void*)(uintptr_t)(tls + 0xc), 12)) {
                    *(uint32_t*)(uintptr_t)(tls + 0xc) = nh;
                    *(uint32_t*)(uintptr_t)(tls + 0x10) = 0;
                }
                c->x[0] = 0;
                lg("[svc] GetService('%s') via '%s' -> h=%x\n", svc, nm, nh);
                dump_tls("rep", tls, 16);
                return;
            }
        }
        /* Messages with no 'SFCI' packet: the reply buffer is the request
           buffer, and the kernel leaves it in place for the server to edit.
           Zeroing the payload (the old behaviour) destroyed the trailing
           words of an sf domain exchange -- nn::fs::QueryMountRomCacheSize
           then re-parsed a wiped buffer and produced the client's own
           0x1a80a reply-magic error. Echo the request and only write the
           Result the SDK reads at tls+8. */
        {   int want = gh_hipc_magic_off(c, tls);
            /* fsp-srv query with no CMIF packet: the client's cmif reader
               still expects a reply packet whose magic sits at
               align16(ptr + desc_count*4) + 0x10, and its payload follows at
               packet+0x20.  That query is QueryMountRomCacheSize, so answer it
               with the size measured from the real romfs tree. */
            if (strcmp(nm, "fsp-srv") == 0 && want >= 0 && want <= 0x3c0 &&
                !IsBadWritePtr((void*)(uintptr_t)tls, 0x60)) {
                /* fsp-srv query with no CMIF packet on the wire: this is the
                   storage/size query under nn::fs::QueryMountRomCacheSize.
                   Answer it at the offset the client-side lookup reports,
                   from the real romfs tree. */
                uint64_t mc = gh_romfs_meta_size();
                *(uint32_t*)(uintptr_t)(tls + (uint64_t)want + 0x10) = 0x4f434653u;
                *(uint32_t*)(uintptr_t)(tls + (uint64_t)want + 0x14) = 1;
                *(uint32_t*)(uintptr_t)(tls + (uint64_t)want + 0x18) = 0;
                *(uint32_t*)(uintptr_t)(tls + (uint64_t)want + 0x1c) = 0;
                *(uint64_t*)(uintptr_t)(tls + (uint64_t)want + 0x20) = mc;
                *(uint32_t*)(uintptr_t)(tls + 4) = 2u | 0x400u;
                lg("[romfs] fsp-srv size query: SFCO at tls+%x sizes at tls+%x -> %llu\n",
                   want + 0x10, want + 0x20, (unsigned long long)mc);
                dump_tls("rep", tls, 16);
                (void)gh_hipc_magic_off(c, tls);
                c->x[0] = 0;
                return;
            }
        }
        if (!IsBadWritePtr((void*)(uintptr_t)(tls + 8), 4))
            *(uint32_t*)(uintptr_t)(tls + 8) = 0;
        lg("[svc]   sm reply on '%s': Result 0 at tls+8 (request echoed)\n", nm);
        dump_tls("rep", tls, 8);
        c->x[0] = 0;
        return;
    }
    /* Kernel svc numbering used by this runtime (matches libnx/Horizon):
       0x06 QueryMemory, 0x0c GetThreadPriority, 0x1e GetSystemTick,
       0x24 GetProcessId, 0x25 GetThreadId, 0x29 GetInfo.  nn::os::detail::
       ThreadManagerImplByHorizon's ctor treats a non-zero w0 from any of
       these as fatal, so each one must write its out parameter and return
       0 -- the old "GetSystemTick at 0x25" answer failed the ctor and made
       nn::os::Initialize abort while building the thread manager. */
    if (imm == 0x0c) {   /* GetThreadPriority(u32* out=x0, Handle=x1) */
        if (c->x[0] && !IsBadWritePtr((void*)(uintptr_t)c->x[0], 4))
            *(uint32_t*)(uintptr_t)c->x[0] = 0x2c;
        c->x[0] = 0;
        return;
    }
    if (imm == 0x24) {   /* GetProcessId(u64* out=x0, Handle=x1) */
        if (c->x[0] && !IsBadWritePtr((void*)(uintptr_t)c->x[0], 8))
            *(uint64_t*)(uintptr_t)c->x[0] = 0x51ull;
        c->x[0] = 0;
        return;
    }
    if (imm == 0x25) {   /* GetThreadId(u64* out=x0, Handle=x1) */
        uint64_t id = c->x[1] ? (c->x[1] & 0x3fffffffull) : 1ull;
        if (c->x[0] && !IsBadWritePtr((void*)(uintptr_t)c->x[0], 8))
            *(uint64_t*)(uintptr_t)c->x[0] = id;
        lg("[svc] GetThreadId(handle=%llx) -> %llx\n",
           (unsigned long long)c->x[1], (unsigned long long)id);
        c->x[0] = 0;
        return;
    }
    if (imm == 0x0d || imm == 0x0f || imm == 0x10) {
        /* SetThreadPriority / SetThreadCoreMask / GetCurrentProcessorNumber */
        if (imm == 0x10 && c->x[0] && !IsBadWritePtr((void*)(uintptr_t)c->x[0], 4))
            *(uint32_t*)(uintptr_t)c->x[0] = 0;
        c->x[0] = 0;
        return;
    }
    /* ArbitrateLock(owner=x0, mutex_word=x1, tag=x2) / ArbitrateUnlock(x1).
       The mutex word packs the owner tag in bits 0..29, bit30 = "locked" and
       bit31 = "has waiters".  nn::os::InternalCriticalSectionImplByHorizon::
       Enter spins on the word until (word & ~0x40000000) == its own thread
       tag, so returning 0 without publishing the tag looped forever (4.6e9
       svc #1a calls at lr=e847964).  Single-threaded runtime: the free/locked
       owner check is real, and a word owned by a *different* tag can never be
       released, so take it and log once. */
    if (imm == 0x1a) {
        uint32_t* w = (uint32_t*)(uintptr_t)c->x[1];
        uint32_t tag = (uint32_t)c->x[2], old = 0;
        if (c->x[1] && !IsBadWritePtr((void*)(uintptr_t)c->x[1], 4)) {
            old = *w;
            if ((old & 0x3fffffffu) != tag && old != 0 && g_arb_log++ < 8)
                lg("[svc] ArbitrateLock: word %08x owner %u -> takeover tag %u\n",
                   old, old & 0x3fffffffu, tag);
            *w = tag;
        }
        c->x[0] = 0;
        return;
    }
    if (imm == 0x1b) {   /* ArbitrateUnlock(mutex_word=x1) */
        if (c->x[1] && !IsBadWritePtr((void*)(uintptr_t)c->x[1], 4))
            *(uint32_t*)(uintptr_t)c->x[1] = 0;
        c->x[0] = 0;
        return;
    }
    if (imm == 0x1e) {   /* GetSystemTick: value in x0, no Result */
        LARGE_INTEGER q, f;
        QueryPerformanceCounter(&q);
        QueryPerformanceFrequency(&f);
        c->x[0] = (uint64_t)((double)q.QuadPart * 19200000.0 / (double)f.QuadPart);
        return;
    }
    if (imm == 0x07 || imm == 0x26) {   /* ExitProcess / Break */
        lg("[svc] %s x0=%llx x1=%llx x2=%llx x3=%llx x4=%llx x30=%llx\n",
           imm == 0x26 ? "BREAK" : "EXIT",
           (unsigned long long)c->x[0], (unsigned long long)c->x[1],
           (unsigned long long)c->x[2], (unsigned long long)c->x[3],
           (unsigned long long)c->x[4], (unsigned long long)c->x[30]);
        /* best effort: break info pointer often points at a message */
        for (int k = 1; k <= 3; k++) {
            unsigned long long p = c->x[k];
            if (p < 0x08500000ull || p >= 0x100000000ull) continue;
            char buf[192];
            size_t n = 0;
            while (n < sizeof(buf) - 1 &&
                   !IsBadReadPtr((const void*)(uintptr_t)(p + n), 1)) {
                char ch = ((const char*)p)[n];
                if (ch == 0) break;
                if (ch < 0x20 || ch > 0x7e) { buf[0] = 0; n = 0; break; }
                buf[n++] = ch;
            }
            if (n) { buf[n] = 0; lg("[svc]   x%d str: %s\n", k, buf); }
            else if (p >= 0x20000000ull && p < 0x21000000ull &&
                     !IsBadReadPtr((const void*)(uintptr_t)p, 4))
                lg("[svc]   x%d u32=%08x u64=%llx\n", k,
                   *(const unsigned int*)p, *(const unsigned long long*)p);
        }
        dump_fp_chain(c);
    dump_stack32(c);
    a64_dump_trace();
    if (getenv("SN_BIGTRACE")) a64_dump_bigtrace(24000);
    }
    c->x[0] = 0;   /* default: Result success for unmodelled services */
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
    lg("[undef] x30=%llx sp=%llx\n", (unsigned long long)c->x[30],
       (unsigned long long)c->x[31]);
    c->undef_count++;
    dump_stack32(c);
    a64_dump_trace();
    c->pc = A64_STOP;
}

/* 96 qwords of guest stack from sp-0x40, clamped to the top of the 16 MB
   guest stack region (20fff000). Used by the unmapped/undef stop paths. */
static void dump_stack32(A64Ctx* c) {
    uint64_t sp = c->x[31];
    uint64_t start = sp - 0x40;
    const uint64_t limit = 0x20fff000ull;
    lg("[stack] x30=%llx sp=%llx dump from %llx\n",
       (unsigned long long)c->x[30], (unsigned long long)sp,
       (unsigned long long)start);
    for (int i = 0; i < 96; i++) {
        uint64_t a = start + (uint64_t)i * 8;
        if (a + 8 > limit) break;
        if (IsBadReadPtr((const void*)(uintptr_t)a, 8)) {
            lg("[stack]   %llx: <unreadable>\n", (unsigned long long)a);
            continue;
        }
        lg("[stack]   %llx: %016llx\n", (unsigned long long)a,
           *(const unsigned long long*)(uintptr_t)a);
    }
}

void a64_unmapped(A64Ctx* c) {
    lg("[unmapped] pc=%llx not in fn table -> STOP (svc_count=%llu)\n",
       (unsigned long long)c->pc, (unsigned long long)c->svc_count);
    lg("[unmapped] x30=%llx x16=%llx x17=%llx x0=%llx x1=%llx x2=%llx "
       "x3=%llx sp=%llx\n",
       (unsigned long long)c->x[30], (unsigned long long)c->x[16],
       (unsigned long long)c->x[17], (unsigned long long)c->x[0],
       (unsigned long long)c->x[1], (unsigned long long)c->x[2],
       (unsigned long long)c->x[3], (unsigned long long)c->x[31]);
    for (int i = 0; i < 30; i++)
        if (c->x[i] >= 0x400000 && c->x[i] < 0x100000000ull)
            lg("[unmapped]   x%d=%llx\n", i, (unsigned long long)c->x[i]);
    dump_stack32(c);
    a64_dump_trace();
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

int64_t a64_f2iz_s(int fsize, double v) { (void)fsize; return f2i_s(v, 0); }
int64_t a64_f2ia_s(int fsize, double v) { (void)fsize; return f2i_s(v, 1); }
int64_t a64_f2im_s(int fsize, double v) { (void)fsize; return f2i_s(v, 2); }
int64_t a64_f2in_s(int fsize, double v) { (void)fsize; return f2i_s(v, 3); }
int64_t a64_f2ip_s(int fsize, double v) { (void)fsize; return f2i_s(v, 4); }
uint64_t a64_f2iz_u(int fsize, double v) { (void)fsize; return f2i_u(v, 0); }
uint64_t a64_f2ia_u(int fsize, double v) { (void)fsize; return f2i_u(v, 1); }
uint64_t a64_f2im_u(int fsize, double v) { (void)fsize; return f2i_u(v, 2); }
uint64_t a64_f2in_u(int fsize, double v) { (void)fsize; return f2i_u(v, 3); }
uint64_t a64_f2ip_u(int fsize, double v) { (void)fsize; return f2i_u(v, 4); }

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

/* Small-address diagnostic (see A64_SMALL_CHK in a64_ctx.h). A guest
   access below 0x1000 faults on the host and the guest pc is lost, so log
   the access before it happens. */
void a64_small_addr_dbg(A64Ctx* c, uint64_t a, int wr, int n) {
    static int seen = 0;
    /* A genuinely unmapped low address cannot be tolerated: the load/store
       that follows this hook would take the whole process down with a host
       access violation and lose the guest pc. Report it as a guest fault and
       stop the run cleanly instead (exit code 0, same shape as an abort). */
    if (a < 0x1000ull &&
        (wr ? IsBadWritePtr((void*)(uintptr_t)a, (size_t)n)
            : IsBadReadPtr((const void*)(uintptr_t)a, (size_t)n))) {
        lg("[mem] guest fault: unmapped %s a=%llx n=%d pc=%llx lr=%llx\n",
           wr ? "wr" : "rd", (unsigned long long)a, n,
           (unsigned long long)c->pc, (unsigned long long)c->x[30]);
        dump_fp_chain(c);
        a64_dump_trace();
        lg("[done] exit\n");
        fflush(g_log);
        ExitProcess(0);
    }
    if (seen > 40) return;
    seen++;
    lg("[mem] SMALL %s a=%llx n=%d pc=%llx lr=%llx sp=%llx x0=%llx x1=%llx x2=%llx x19=%llx x20=%llx\n",
       wr ? "wr" : "rd", (unsigned long long)a, n,
       (unsigned long long)c->pc, (unsigned long long)c->x[30],
       (unsigned long long)c->x[31], (unsigned long long)c->x[0],
       (unsigned long long)c->x[1], (unsigned long long)c->x[2],
       (unsigned long long)c->x[19], (unsigned long long)c->x[20]);
}

void a64_dbg_tls(uint64_t tag, A64Ctx* c) {
    uint64_t a = 0, b = 0, t = 0;
    if (!IsBadReadPtr((const void*)(uintptr_t)(c->tpidrro + 0x1f0), 0x10)) {
        a = *(uint64_t*)(uintptr_t)(c->tpidrro + 0x1f0);
        b = *(uint64_t*)(uintptr_t)(c->tpidrro + 0x1f8);
    }
    if (b && !IsBadReadPtr((const void*)(uintptr_t)(b + 0x1b0), 4))
        t = *(uint32_t*)(uintptr_t)(b + 0x1b0);
    lg("[tl] tag=%llx tpidrro=%llx [1f0]=%llx [1f8]=%llx id180=%llx x0=%llx x19=%llx\n",
       (unsigned long long)tag, (unsigned long long)c->tpidrro,
       (unsigned long long)a, (unsigned long long)b, (unsigned long long)t,
       (unsigned long long)c->x[0], (unsigned long long)c->x[19]);
    lg("[tl] tag=%llx x8=%llx x20=%llx abs1f8=%llx\n",
       (unsigned long long)tag, (unsigned long long)c->x[31],
       (unsigned long long)c->x[8],
       (unsigned long long)c->x[20],
       (unsigned long long)(IsBadReadPtr((const void*)0x300001f8ull, 8) ? 0
                            : *(uint64_t*)(uintptr_t)0x300001f8ull));
}

void a64_dbg_allocmgr(uint64_t tag, uint64_t p) {
    uint64_t f = 0, l = 0, g = 0, vt = 0;
    if (p && !IsBadReadPtr((const void*)(uintptr_t)p, 0x28)) {
        vt = *(uint64_t*)(uintptr_t)p;
        f = *(uint64_t*)(uintptr_t)(p + 0x10);
        l = *(uint64_t*)(uintptr_t)(p + 0x18);
        g = *(uint64_t*)(uintptr_t)(p + 0x20);
    }
    if (tag == 0x7621 && vt && !IsBadReadPtr((const void*)(uintptr_t)vt, 0x20)) {
        lg("[am] vttab=%llx %llx %llx %llx\n",
           (unsigned long long)*(uint64_t*)(uintptr_t)vt,
           (unsigned long long)*(uint64_t*)(uintptr_t)(vt + 8),
           (unsigned long long)*(uint64_t*)(uintptr_t)(vt + 16),
           (unsigned long long)*(uint64_t*)(uintptr_t)(vt + 24));
    }
    if (tag == 0x7621 && p && !IsBadReadPtr((const void*)(uintptr_t)p, 0x50)) {
        lg("[am] fbd0=%llx fbd1=%llx fbd2=%llx fbd3=%llx fbn=%llx\n",
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 0x28),
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 0x30),
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 0x38),
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 0x40),
           (unsigned long long)*(uint64_t*)(uintptr_t)(p + 0x48));
    }
    lg("[am] tag=%llx p=%llx vt=%llx first=%llx last=%llx gran=%llx\n",
       (unsigned long long)tag, (unsigned long long)p,
       (unsigned long long)vt, (unsigned long long)f,
       (unsigned long long)l, (unsigned long long)g);
}

void a64_trace_pc(A64Ctx* c, uint64_t tag) {
    uint64_t m29 = 0, sp8 = 0, v0 = 0, vt = 0;
    if (tag == 0x7640) {
        dump_fp_chain(c);
    }
    if (tag == 0x7401) {
        a64_str_dbg("afmt1", 0xe846910ull);
        a64_str_dbg("afmt2", 0xe846a04ull);
        dump_fp_chain(c);
    }
    if (!IsBadReadPtr((const void*)(uintptr_t)(c->x[29] - 0x10), 8))
        m29 = *(uint64_t*)(uintptr_t)(c->x[29] - 0x10);
    if (!IsBadReadPtr((const void*)(uintptr_t)(c->x[31] + 8), 8))
        sp8 = *(uint64_t*)(uintptr_t)(c->x[31] + 8);
    if (c->x[20] && !IsBadReadPtr((const void*)(uintptr_t)c->x[20], 8)) {
        vt = *(uint64_t*)(uintptr_t)c->x[20];
        if (vt && !IsBadReadPtr((const void*)(uintptr_t)(vt + 32), 8))
            v0 = *(uint64_t*)(uintptr_t)(vt + 32);
    }
    lg("[p7] tag=%llx x0=%llx x8=%llx x19=%llx x20=%llx x21=%llx x29=%llx x30=%llx mem29=%llx sp8=%llx vt=%llx vt20=%llx\n",
       (unsigned long long)tag, (unsigned long long)c->x[0],
       (unsigned long long)c->x[8], (unsigned long long)c->x[19],
       (unsigned long long)c->x[20], (unsigned long long)c->x[21],
       (unsigned long long)c->x[29], (unsigned long long)c->x[30],
       (unsigned long long)m29, (unsigned long long)sp8,
       (unsigned long long)vt, (unsigned long long)v0);
}

void a64_str_dbg(const char* tag, uint64_t p) {
    char out[96];
    int i, n = 0;
    if (p < 0x08500000ull || p >= 0x100000000ull) {
        lg("[str] %s ptr=%llx (non-guest)\n", tag, (unsigned long long)p);
        return;
    }
    for (i = 0; i < 40; i++) {
        uint8_t ch;
        if (IsBadReadPtr((const void*)(uintptr_t)(p + (uint64_t)i), 1)) break;
        ch = *(const uint8_t*)(uintptr_t)(p + (uint64_t)i);
        if (!ch) break;
        out[n++] = (ch >= 0x20 && ch < 0x7f) ? (char)ch : '.';
    }
    out[n] = 0;
    lg("[str] %s len=%d '%s'\n", tag, n, out);
}
