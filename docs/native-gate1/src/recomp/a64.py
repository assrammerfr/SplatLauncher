"""AArch64 -> C emitter for the SplatNative static recompiler (capstone-driven).

Disassembly is delegated to capstone (BSD); this module owns SEMANTICS: it maps
each (mnemonic, operands) pair to C statements over the guest Ctx. Control flow
inside a function becomes C labels; calls set x30 + ctx.pc and return; callers
propagate when ctx.pc changed. Unmapped mnemonics emit a64_undef(...) with the
mnemonic recorded, stopping the thread cleanly so bring-up gaps are logged.
"""
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import (ARM64_OP_REG, ARM64_OP_IMM, ARM64_OP_MEM, ARM64_OP_FP,
                            ARM64_SFT_LSL, ARM64_SFT_LSR, ARM64_SFT_ASR, ARM64_SFT_ROR,
                            ARM64_EXT_UXTB, ARM64_EXT_UXTH, ARM64_EXT_UXTW, ARM64_EXT_UXTX,
                            ARM64_EXT_SXTB, ARM64_EXT_SXTH, ARM64_EXT_SXTW, ARM64_EXT_SXTX)
try:
    from capstone.arm64 import ARM64_OP_SYSREG  # type: ignore
except ImportError:
    ARM64_OP_SYSREG = -100  # op.type will fall into our 'other' branch instead

MD = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
MD.detail = True

CONDS = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
         "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]

CONDC = {  # capstone condition-name -> our 4-bit cond field (same encoding)
    "eq": 0, "ne": 1, "hs": 2, "cs": 2, "lo": 3, "cc": 3, "mi": 4, "pl": 5,
    "vs": 6, "vc": 7, "hi": 8, "ls": 9, "ge": 10, "lt": 11, "gt": 12, "le": 13,
    "al": 14, "nv": 15,
}


class Unsupported(Exception):
    def __init__(self, pc, word, why, mnemonic=""):
        super().__init__(f"pc={pc:#x} op={word:#010x} {mnemonic}: {why}")
        self.pc = pc
        self.word = word
        self.why = why
        self.mnemonic = mnemonic


def _imm(tok):
    """Parse an operand-string token as integer (accepts #-prefixed, hex, negatives)."""
    t = tok.strip()
    if t.startswith("#"):
        t = t[1:]
    return int(t, 0)


class FnEmitter:
    def __init__(self, fn_addr, fn_end):
        self.addr = fn_addr
        self.end = fn_end
        self.labels = set()
        self.lines = []
        self.gaps = []          # (pc, word, mnemonic, why)
        self.used_helpers = set()

    # ---------- output ----------
    def em(self, s):
        self.lines.append("    " + s)

    # ---------- register access ----------
    @staticmethod
    def rd(rn, w=False):
        rn = rn.lower()
        if rn in ("wzr", "xzr"):
            return "0u" if w else "0ull"
        if rn == "sp" or rn == "wsp":
            return "c->x[31]"
        if rn[0] == "w":
            return f"(uint32_t)c->x[{rn[1:]}]"
        return f"c->x[{rn[1:]}]"

    @staticmethod
    def wr(rd, expr, w=False, sp=False):
        rd = rd.lower()
        if rd in ("wzr", "xzr"):
            return []
        if rd == "sp" or rd == "wsp":
            if sp:
                return [f"c->x[31] = {expr};"]
            return []
        if w or rd[0] == "w":
            return [f"c->x[{rd[1:]}] = (uint32_t)({expr});"]
        return [f"c->x[{rd[1:]}] = {expr};"]

    @staticmethod
    def is_w(r):
        r = r.lower()
        return r.startswith("w") and r not in ("wzr", "wsp")

    # ---------- helpers ----------
    def put(self, stmts):
        for s in stmts:
            self.em(s)

    def branch_to(self, target, is_known_fn):
        """Emit a taken branch to `target` (no fallthrough)."""
        if self.addr <= target < self.end:
            self.em(f"goto L_{target:x};")
        else:
            # cross-function transfer: hand off to dispatcher
            self.em(f"c->pc = {target:#x}u;")
            self.em("return;")

    # ---------- operand parsing ----------
    def ops(self, insn):
        out = []
        for o in insn.operands:
            if o.type == ARM64_OP_REG:
                out.append(("reg", insn.reg_name(o.reg)))
            elif o.type == ARM64_OP_IMM:
                out.append(("imm", o.imm))
            elif o.type == ARM64_OP_MEM:
                out.append(("mem", o.mem))
            elif o.type == ARM64_OP_FP:
                out.append(("fp", o.fp))
            else:
                # SYSREG / SVC / PREFETCH / BARRIER / PSTATE: normalize to a
                # token the emitters can consume.
                v = None
                for attr in ("imm", "value", "fp", "sysreg"):
                    v = getattr(o, attr, None)
                    if isinstance(v, (int, float)):
                        break
                if v is None:
                    v = getattr(o, "reg", 0)
                out.append(("sys", v))
        return out

    @staticmethod
    def mem_addr(m, insn, base_expr_getter):
        """C expression for a mem operand (base + [index<<shift + disp])."""
        base = base_expr_getter(m.base)
        disp = m.disp
        expr = base
        if m.index:
            idx = base_expr_getter(m.index)
            # capstone puts the shift on the operand's shift field via mem? For A64,
            # index shift/extend lives in insn operand shift on the MEM operand itself.
            sh = getattr(m, "shift", None)
            # capstone arm64: mem.index extend is in operand.ext on the mem operand
            ext = getattr(m, "ext", None)
            # handled by caller passing pre-shifted index; default lsl by size below
            expr = f"({base} + {idx} /*index*/)"
        if disp:
            expr = f"({expr} + {disp})" if disp > 0 else f"({expr} - {-disp})"
        return expr

    # ---------- main ----------
    def run(self, text, base):
        """Disassemble [base, base+len(text)) and emit C."""
        insns = list(MD.disasm(text, base))
        by_addr = {i.address: i for i in insns}
        # pre-collect branch targets that land inside the function
        for i in insns:
            if i.mnemonic in ("b", "bl") or i.mnemonic.startswith("b."):
                if i.operands and i.operands[0].type == ARM64_OP_IMM:
                    t = i.operands[0].imm
                    if self.addr <= t < self.end:
                        self.labels.add(t)
            elif i.mnemonic in ("cbz", "cbnz", "tbz", "tbnz"):
                # last operand is the label
                if i.operands and i.operands[-1].type == ARM64_OP_IMM:
                    t = i.operands[-1].imm
                    if self.addr <= t < self.end:
                        self.labels.add(t)

        emitted_upto = None
        for idx, i in enumerate(insns):
            if i.address >= self.end:
                break
            if i.address in self.labels:
                self.em(f"L_{i.address:x}: ;")
            self._cur_insn = i
            try:
                self.emit_insn(i, idx, insns)
            except Unsupported as e:
                self.gaps.append((e.pc, e.word, e.mnemonic, e.why))
                m = (e.mnemonic or "?").replace('"', "")
                self.em(f"a64_undef(c, {e.pc:#x}u, {e.word:#x}u, \"{m}\");")
                self.em(f"c->pc = {e.pc:#x}u;")
                self.em("return;")
                emitted_upto = e.pc
                break
            except Exception as e:  # noqa: BLE001 - emitter bugs must not kill a build
                self.gaps.append((i.address, struct.unpack("<I", i.bytes[:4])[0],
                                  i.mnemonic, f"emitter:{type(e).__name__}:{e}"))
                self.em(f"a64_undef(c, {i.address:#x}u, {struct.unpack('<I', i.bytes[:4])[0]:#x}u, \"{i.mnemonic}\");")
                self.em(f"c->pc = {i.address:#x}u;")
                self.em("return;")
                emitted_upto = i.address
                break
            emitted_upto = i.address + 4
        if emitted_upto is not None and emitted_upto < self.end:
            # stopped early; fallthrough already set by undef
            pass
        elif emitted_upto is not None:
            self.em(f"c->pc = {self.end:#x}u;")
            self.em("return;")
        return "\n".join(self.lines)

    # ==================================================================
    def emit_insn(self, i, idx, insns):
        pc = i.address
        word = struct.unpack("<I", i.bytes[:4])[0] if len(i.bytes) >= 4 else 0
        nxt = pc + 4
        m = i.mnemonic
        s = i.op_str
        ops = self.ops(i) if i.operands else []

        # ---- branches ----
        if m == "b":
            self.branch_to(ops[0][1], None)
            return
        if m == "bl":
            self._call(ops[0][1], nxt)
            return
        if m.startswith("b."):
            cond = CONDC[m[2:]]
            t = ops[0][1]
            in_fn = self.addr <= t < self.end
            if in_fn:
                self.labels.add(t)
            self.em(f"if (a64_cond(c, {cond})) {{")
            if in_fn:
                self.lines.append(f"        goto L_{t:x};")
            else:
                self.lines.append(f"        c->pc = {t:#x}u;")
                self.lines.append("        return;")
            self.em("    }")
            return
        if m in ("cbz", "cbnz"):
            reg = ops[0][1]
            t = ops[1][1]
            val = self.rd(reg, self.is_w(reg))
            in_fn = self.addr <= t < self.end
            if in_fn:
                self.labels.add(t)
            cmp_ = "==" if m == "cbz" else "!="
            self.em(f"if ({val} {cmp_} 0) {{")
            if in_fn:
                self.lines.append(f"        goto L_{t:x};")
            else:
                self.lines.append(f"        c->pc = {t:#x}u;")
                self.lines.append("        return;")
            self.em("    }")
            return
        if m in ("tbz", "tbnz"):
            reg = ops[0][1]
            bit = ops[1][1]
            t = ops[2][1]
            in_fn = self.addr <= t < self.end
            if in_fn:
                self.labels.add(t)
            cmp_ = "==" if m == "tbz" else "!="
            self.em(f"if (((({self.rd(reg, True)} >> {bit}) & 1) {cmp_} 1)) {{")
            if in_fn:
                self.lines.append(f"        goto L_{t:x};")
            else:
                self.lines.append(f"        c->pc = {t:#x}u;")
                self.lines.append("        return;")
            self.em("    }")
            return
        if m == "br":
            self.em(f"c->pc = {self.rd(ops[0][1])};")
            self.em("return;")
            return
        if m == "blr":
            self.em(f"c->x[30] = {nxt:#x}u;")
            self.em(f"c->pc = {self.rd(ops[0][1])};")
            self.em("return;")
            return
        if m == "ret":
            rn = ops[0][1] if ops else "x30"
            self.em(f"c->pc = {self.rd(rn)};")
            self.em("return;")
            return
        if m in ("braa", "brab", "retaa", "retab", "blraa", "blrab", "eret", "drps", "reset"):
            raise Unsupported(pc, word, "auth/system branch", m)

        # ---- svc / brk / hlt / udf / sys ----
        if m == "svc":
            imm = ops[0][1] if ops else 0
            self.em(f"a64_svc(c, {imm:#x});")
            if imm in (0x07, 0x26):   # ExitProcess / Break do not return
                self.em("c->pc = A64_STOP; return;")
            return
        if m in ("brk", "hlt", "udf", "dcps1", "dcps2", "dcps3"):
            self.em(f"a64_break(c, {pc:#x}u, {word:#x}u);")
            self.em(f"c->pc = {nxt:#x}u;")
            self.em("return;")
            return

        # ---- hints / barriers / prefetch ----
        if m in ("nop", "yield", "wfe", "wfi", "sev", "sevl", "isb", "dsb", "dmb",
                 "clrex", "sb", "esb", "psb", "tsb", "csdb", "hint"):
            return

        # ---- MRS / MSR ----
        if m == "mrs":
            return self._mrs(pc, ops)
        if m == "msr":
            return self._msr(pc, ops)

        # ---- move / arith aliases ----
        if m in ("mov", "movz", "movn", "movk"):
            return self._mov(pc, m, ops)
        if m in ("adrp", "adr"):
            rd = ops[0][1]
            imm = ops[1][1]
            if m == "adrp":
                page = pc & ~0xFFF
                imm = (imm & ~0xFFF)
                expr = f"{page:#x}ull + ({imm:#x}ull)"
            else:
                expr = f"{pc:#x}ull + ({imm:#x}ull)"
            self.put(self.wr(rd, expr))
            return
        if m in ("add", "adds", "sub", "subs", "cmp", "cmn", "neg", "negs"):
            return self._addsub(pc, m, ops)
        if m in ("and", "ands", "orr", "orn", "eor", "eon", "bic", "bics", "tst", "teq"):
            return self._logical(pc, m, ops)
        if m in ("mul", "madd", "msub", "mneg", "smulh", "umulh", "smull", "umull",
                 "smaddl", "umaddl", "smsubl", "umsubl", "smnegl", "umnegl"):
            return self._mul(pc, m, ops)
        if m in ("udiv", "sdiv"):
            rd, rn, rm = ops[0][1], ops[1][1], ops[2][1]
            w = self.is_w(rd)
            f = "a64_udiv32" if w else "a64_udiv64"
            if m == "sdiv":
                f = "a64_sdiv32" if w else "a64_sdiv64"
            self.put(self.wr(rd, f"{f}({self.rd(rn, w)}, {self.rd(rm, w)})", w))
            return
        if m in ("lsl", "lsr", "asr", "ror"):
            return self._shift(pc, m, ops)
        if m in ("extr",):
            rd, rn, rm = ops[0][1], ops[1][1], ops[2][1]
            imm = ops[3][1]
            w = self.is_w(rd)
            a, b = self.rd(rn, w), self.rd(rm, w)
            if w:
                expr = f"(uint32_t)((({a}) >> {imm}) | (({b}) << (32 - {imm})))" if imm else f"(uint32_t)({b})"
            else:
                expr = f"((({a}) >> {imm}) | (({b}) << (64 - {imm})))" if imm else f"({b})"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("ubfx", "sbfx", "bfxil", "ubfiz", "sbfiz", "bfm", "sbfm", "ubfm",
                 "bfi", "bfcil" if False else "bfcil", "sbfx16" if False else "sbfx16"):
            return self._bitfield(pc, m, ops)

        # ---- conditional select / compare ----
        if m in ("csel", "csinc", "csinv", "csneg"):
            rd, rn, rm, cond = ops[0][1], ops[1][1], ops[2][1], ops[3][1]
            w = self.is_w(rd)
            a = self.rd(rn, w)
            b = self.rd(rm, w)
            if m == "csinc":
                b = f"({b} + 1)" if not w else f"(uint32_t)({b} + 1)"
            elif m == "csinv":
                b = f"(~{b})" if not w else f"(uint32_t)(~{b})"
            elif m == "csneg":
                b = f"(-({b}))" if not w else f"(uint32_t)(-({b}))"
            c = CONDC[cond]
            expr = f"(a64_cond(c, {c}) ? ({b}) : ({a}))"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("ccmp", "ccmn"):
            rn, op2 = ops[0][1], ops[1][1]
            # capstone: ccmp x0, #0, eq  OR ccmp x0, x1, #0, eq  (ops vary)
            if len(ops) == 4:
                rhs, nzcv, cond = ops[1][1], ops[2][1], ops[3][1]
                rhs_is_reg = ops[1][0] == "reg"
            else:
                rhs, nzcv, cond = ops[1][1], ops[2][1], ops[3][1]
                rhs_is_reg = ops[1][0] == "reg"
            w = self.is_w(rn)
            a = self.rd(rn, w)
            b = self.rd(rhs, w) if rhs_is_reg else (f"{rhs:#x}ull" if not w else f"{rhs:#x}u")
            cc = CONDC[cond]
            self.em(f"if (a64_cond(c, {cc})) {{")
            if m == "ccmp":
                self._flags_add(a, b, w)
            else:
                self._flags_sublike(a, b, w)
            self.em("    } else {")
            self.em(f"        c->n = {(nzcv >> 3) & 1}; c->z = {(nzcv >> 2) & 1}; c->c = {(nzcv >> 1) & 1}; c->v = {nzcv & 1};")
            self.em("    }")
            return

        # ---- loads / stores ----
        if m in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "ldrsw", "ldur", "ldurb",
                 "ldurh", "ldursb", "ldursh", "ldursw", "str", "strb", "strh",
                 "stur", "sturb", "sturh", "prfm"):
            return self._ldst(pc, m, ops, i)
        if m in ("ldp", "ldpsw", "stp"):
            return self._ldstp(pc, m, ops, i)
        if m in ("ldtr", "ldtrb", "ldtrh", "ldtrsb", "ldtrsh", "ldtrsw",
                 "sttr", "sttrb", "sttrh"):
            return self._ldst(pc, m, ops, i)
        if m in ("ldxr", "ldaxr", "stxr", "stlxr", "ldar", "stlr", "stlxr",
                 "ldar", "ldadb", "ldadd", "ldaddl", "ldset", "ldclr", "ldeor",
                 "swp", "swpl", "cas", "casa", "casal", "casl", "ldaddal"):
            return self._atomic(pc, m, ops, i)
        if m.startswith(("ld1", "ld2", "ld3", "ld4", "st1", "st2", "st3", "st4", "ldnp", "stnp")):
            return self._simd_ldst(pc, m, ops, i)

        # ---- FP ----
        if m in ("fmov", "fabs", "fneg", "fsqrt", "fadd", "fsub", "fmul", "fdiv",
                 "fmax", "fmin", "fmaxnm", "fminnm", "fmadd", "fmsub", "fnmadd",
                 "fnmsub", "fcmp", "fcmpe", "fccmp", "fccmn", "fcsel", "fcvt",
                 "fcvtzs", "fcvtzu", "scvtf", "ucvtf", "frintn", "frintp", "frintm",
                 "frintz", "frinta", "frintx", "frinti", "fnm", "fms", "fmad", "fmia",
                 "fminp", "fmaxp", "fminnm", "fcvtas", "fcvtau", "fcvtms", "fcvtmu",
                 "fcvtns", "fcvtnu", "fcvtps", "fcvtpu", "frecpe", "frecps", "frsqrte",
                 "fmulx", "fdivr" if False else "fdivr"):
            return self._fp(pc, m, ops, i)

        # ---- SIMD integer / crypto ----
        if m in ("dup", "ins", "umov", "smov", "mov", "tbl", "tbx", "ext", "zip1", "zip2",
                 "uzp1", "uzp2", "trn1", "trn2", "and", "orr", "eor", "bic", "orn",
                 "add", "sub", "sqadd", "uqadd", "sqsub", "uqsub", "mul", "mla", "mls",
                 "shl", "ushl", "sshl", "ushr", "sshr", "srshr", "sqrshl", "uqrshl",
                 "sqshl", "uqshl", "sqshlu", "cnt", "not", "neg", "cmgt", "cmge", "cmeq",
                 "cmhi", "cmhs", "cmtst", "addp", "saddlp", "uaddlp", "saddlv", "uaddlv",
                 "smaxp", "umaxp", "sminp", "uminp", "smax", "umax", "smin", "umin",
                 "fadd", "fsub", "fmul", "fdiv", "fmax", "fmin", "fabd", "fabe",
                 "scvtf", "ucvtf", "fcvtzs", "fcvtzu", "sqxtn", "uqxtn", "sqxtun",
                 "sxtl", "sxtl2", "uxtl", "uxtl2", "shll", "shll2", "rev64", "rev32",
                 "rev16", "pmull", "pmull2", "pmul", "uadalp", "sadalp", "uaddw", "saddw",
                 "usubw", "ssubw", "sadalp"):
            return self._simd(pc, m, ops, i)

        if m in ("crc32b", "crc32h", "crc32w", "crc32x", "crc32cb", "crc32ch",
                 "crc32cw", "crc32cx"):
            rd, rn, rm = ops[0][1], ops[1][1], ops[2][1]
            self.put(self.wr(rd, f"a64_crc32({m}, {self.rd(rn, True)}, {self.rd(rm, True)})"))
            return

        raise Unsupported(pc, word, f"unmapped mnemonic '{m}'", m)

    # =============== calls ===============
    def _call(self, target, nxt):
        self.em(f"{{ uint64_t __r = {nxt:#x}u; c->x[30] = __r; c->pc = {target:#x}u; fn_dispatch(c); if (c->pc != __r) return; }}")

    # =============== flag helpers (statements) ===============
    def _flags_add(self, a, b, w):
        if w:
            self.em(f"{{ uint32_t __a = (uint32_t)({a}); uint32_t __b = (uint32_t)({b}); uint32_t __r = __a + __b;")
            self.em("    c->n = (__r >> 31) & 1; c->z = (__r == 0);")
            self.em("    c->c = ((uint64_t)__a + __b) >> 32; c->v = ((__a ^ __r) & (__b ^ __r)) >> 31; }")
        else:
            self.em(f"{{ uint64_t __a = (uint64_t)({a}); uint64_t __b = (uint64_t)({b}); uint64_t __r = __a + __b;")
            self.em("    c->n = (__r >> 63) & 1; c->z = (__r == 0);")
            self.em("    c->c = ((unsigned __int128)__a + __b) >> 64; c->v = ((__a ^ __r) & (__b ^ __r)) >> 63; }")

    def _flags_sublike(self, a, b, w):
        # CMP: a - b flags
        if w:
            self.em(f"{{ uint32_t __a = (uint32_t)({a}); uint32_t __b = (uint32_t)({b}); uint32_t __r = __a - __b;")
            self.em("    c->n = (__r >> 31) & 1; c->z = (__r == 0);")
            self.em("    c->c = (__a >= __b); c->v = ((__a ^ __b) & (__a ^ __r)) >> 31; }")
        else:
            self.em(f"{{ uint64_t __a = (uint64_t)({a}); uint64_t __b = (uint64_t)({b}); uint64_t __r = __a - __b;")
            self.em("    c->n = (__r >> 63) & 1; c->z = (__r == 0);")
            self.em("    c->c = (__a >= __b); c->v = ((__a ^ __b) & (__a ^ __r)) >> 63; }")

    # =============== move ===============
    def _mov(self, pc, m, ops):
        rd = ops[0][1]
        w = self.is_w(rd) or rd.lower() in ("wsp",)
        sp = rd.lower() in ("sp", "wsp")
        if m == "mov":
            src = ops[1]
            if src[0] == "reg":
                expr = self.rd(src[1], self.is_w(src[1]))
                # mov sp, x / mov x, sp
                if src[1].lower() in ("sp", "wsp") and rd[0].lower() == "x":
                    expr = "c->x[31]"
                if rd.lower() in ("sp", "wsp"):
                    self.put(self.wr(rd, expr if src[1].lower() not in ("sp", "wsp") else "c->x[31]", sp=True))
                    return
                self.put(self.wr(rd, expr, w))
                return
            if src[0] == "imm":   # mov (bitmask imm alias of orr) or movz alias
                v = src[1]
                if v < 0:
                    v &= (1 << 64) - 1
                if w:
                    self.put(self.wr(rd, f"{v & 0xFFFFFFFF:#x}u", True))
                else:
                    self.put(self.wr(rd, f"{v:#x}ull"))
                return
            raise Unsupported(pc, 0, "mov operand", m)
        if m == "movz":
            rd, imm = ops[0][1], ops[1][1]
            shift = ops[2][1] if len(ops) > 2 else 0
            v = (imm << shift)
            w = self.is_w(rd)
            self.put(self.wr(rd, f"{v:#x}ull" if not w else f"{v & 0xFFFFFFFF:#x}u", w))
            return
        if m == "movn":
            rd, imm = ops[0][1], ops[1][1]
            shift = ops[2][1] if len(ops) > 2 else 0
            v = ~(imm << shift)
            w = self.is_w(rd)
            if w:
                self.put(self.wr(rd, f"{v & 0xFFFFFFFF:#x}u", True))
            else:
                self.put(self.wr(rd, f"{v & ((1 << 64) - 1):#x}ull", False))
            return
        if m == "movk":
            rd, imm = ops[0][1], ops[1][1]
            shift = ops[2][1] if len(ops) > 2 else 0
            w = self.is_w(rd)
            mask = 0xFFFF << shift
            if w:
                cur = f"(uint32_t)c->x[{rd[1:]}]"
                expr = f"(({cur} & ~{mask & 0xFFFFFFFF:#x}u) | {(imm << shift) & 0xFFFFFFFF:#x}u)"
                self.put(self.wr(rd, expr, True))
            else:
                cur = f"c->x[{rd[1:]}]"
                expr = f"(({cur} & ~{mask:#x}ull) | {(imm << shift):#x}ull)"
                self.put(self.wr(rd, expr, False))
            return

    # =============== add/sub ===============
    def _addsub(self, pc, m, ops):
        # forms: rd, rn, #imm / rd, rn, rm{, shift #n} / rd, rn, rm, extend #n / cmp aliases
        neg = m in ("neg", "negs")
        setflags = m in ("adds", "subs", "cmp", "cmn", "negs")
        is_sub = m in ("sub", "subs", "cmp", "neg", "negs")
        is_add = m in ("add", "adds", "cmn")
        if m in ("cmp", "cmn"):
            rn = ops[0][1]
            rd = "xzr" if self.is_w(rn) else "xzr"
        elif neg:
            rn = "xzr" if self.is_w(ops[1][1]) else "xzr"
            rd = ops[0][1]
        else:
            rd = ops[0][1]
            rn = ops[1][1]
        w = self.is_w(rd) or (m in ("cmp", "cmn") and self.is_w(ops[0][1]))
        if m in ("cmp", "cmn"):
            w = self.is_w(ops[0][1])
            rn = ops[0][1]
            rd = "xzr"
        elif neg:
            w = self.is_w(rd)
            rn = "xzr"
        if len(ops) >= 3:
            rhs = ops[2]
        else:
            rhs = ops[1]
        # operand count varies for cmp: ops[0]=rn, ops[1]=operand
        if m in ("cmp", "cmn"):
            rhs = ops[1]
        a = self.rd(rn, w)
        shift_amt = 0
        shift_type = None
        extend = None
        rhs_tok = rhs
        if rhs[0] == "imm":
            v = rhs[1]
            b = f"({v:#x}ull)" if not w else f"({v & 0xFFFFFFFF:#x}u)"
            if neg:
                b = f"(-({v:#x}ull))" if not w else f"(-({v & 0xFFFFFFFF:#x}u))"
        elif rhs[0] == "reg":
            # check trailing shift/extend encoded in extra ops (capstone puts them
            # on the operand itself for some, in extra operands for others)
            b = self.rd(rhs[1], w)
            if len(ops) > (3 if m not in ("cmp", "cmn") else 2):
                pass
            # capstone exposes shift on operand via i.operands[j].shift — handled below
        else:
            raise Unsupported(pc, 0, f"addsub rhs {rhs}", m)

        # shift/extend info must come from raw operands — pass through instance state
        i = getattr(self, "_cur_insn", None)
        if i is not None and rhs[0] == "reg":
            # find index of the rhs operand in raw ops
            ridx = 0
            if m in ("cmp", "cmn"):
                ridx = 1
            elif m in ("neg", "negs"):
                ridx = 1
            else:
                ridx = 2 if len(ops) >= 3 else 1
            if ridx < len(i.operands):
                raw = i.operands[ridx]
                sh = raw.shift
                if sh.type != ARM64_SFT_LSL and sh.type != 0 or (sh.type == ARM64_SFT_LSL and sh.value):
                    st = {ARM64_SFT_LSL: "<<", ARM64_SFT_LSR: ">>", ARM64_SFT_ASR: ">>a",
                          ARM64_SFT_ROR: "ror"}[sh.type]
                    if sh.type == ARM64_SFT_ROR:
                        b = f"a64ror{32 if w else 64}({b}, {sh.value})"
                    elif sh.type == ARM64_SFT_LSR:
                        b = f"({b} >> {sh.value})" if not w else f"(uint32_t)({b} >> {sh.value})"
                    elif sh.type == ARM64_SFT_ASR:
                        b = f"a64asr{32 if w else 64}({b}, {sh.value})"
                    else:
                        b = f"({b} << {sh.value})" if not w else f"(uint32_t)({b} << {sh.value})"
                ext = raw.ext
                if ext in (ARM64_EXT_UXTB, ARM64_EXT_UXTH, ARM64_EXT_UXTW, ARM64_EXT_UXTX,
                           ARM64_EXT_SXTB, ARM64_EXT_SXTH, ARM64_EXT_SXTW, ARM64_EXT_SXTX):
                    amt = {ARM64_EXT_UXTB: 0, ARM64_EXT_UXTH: 1, ARM64_EXT_UXTW: 2, ARM64_EXT_UXTX: 3,
                           ARM64_EXT_SXTB: 0, ARM64_EXT_SXTH: 1, ARM64_EXT_SXTW: 2, ARM64_EXT_SXTX: 3}[ext]
                    b = f"(uint64_t)(int64_t)(int{8 << amt}_t)(uint{8 << amt}_t)({b})" if amt else \
                        f"(uint64_t)(int64_t)(int8_t)(uint8_t)({b})"
                    if w:
                        b = f"(uint32_t)({b})"
        if rhs[0] == "imm" and neg:
            expr = f"({a} - ({rhs[1]:#x}ull))" if not w else f"(uint32_t)({a} - ({rhs[1] & 0xFFFFFFFF:#x}u))"
        elif rhs[0] == "imm":
            sign = "+" if is_add else "-"
            expr = f"({a} {sign} {rhs[1]:#x}ull)" if not w else f"(uint32_t)({a} {sign} {rhs[1] & 0xFFFFFFFF:#x}u)"
        else:
            sign = "-" if is_sub else "+"
            expr = f"({a} {sign} {b})" if not w else f"(uint32_t)({a} {sign} {b})"
        if setflags:
            if is_sub or neg:
                self._flags_sublike(a, b if rhs[0] == "reg" else f"({rhs[1]:#x}ull)", w)
            else:
                self._flags_add(a, b if rhs[0] == "reg" else f"({rhs[1]:#x}ull)", w)
        if m not in ("cmp", "cmn"):
            sp = rd.lower() in ("sp", "wsp") or rn.lower() in ("sp", "wsp")
            self.put(self.wr(rd, expr, w, sp=sp))

    # =============== logical ===============
    def _logical(self, pc, m, ops):
        if m in ("tst", "teq"):
            rd = "xzr"
            rn = ops[0][1]
            rhs = ops[1]
            setflags = True
            op = "&"
        else:
            rd = ops[0][1]
            rn = ops[1][1]
            rhs = ops[2] if len(ops) > 2 else ops[1]
            setflags = m in ("ands", "bics")
            op = {"and": "&", "ands": "&", "bic": "&~", "bics": "&~",
                  "orr": "|", "orn": "|~", "eor": "^", "eon": "^~"}[m]
        w = self.is_w(rd) or (m in ("tst", "teq") and self.is_w(rn))
        if m in ("tst", "teq"):
            w = self.is_w(rn)
        a = self.rd(rn, w)
        if rhs[0] == "imm":
            v = rhs[1] & ((1 << 64) - 1)
            b = f"({v:#x}ull)" if not w else f"({v & 0xFFFFFFFF:#x}u)"
        else:
            b = self.rd(rhs[1], w)
            # optional shift on rhs (capstone raw)
            i = getattr(self, "_cur_insn", None)
            ridx = 1 if m in ("tst", "teq") else 2
            if i is not None and ridx < len(i.operands):
                sh = i.operands[ridx].shift
                if sh.type == ARM64_SFT_LSL and sh.value:
                    b = f"({b} << {sh.value})" if not w else f"(uint32_t)({b} << {sh.value})"
                elif sh.type == ARM64_SFT_LSR and sh.value:
                    b = f"({b} >> {sh.value})" if not w else f"(uint32_t)({b} >> {sh.value})"
                elif sh.type == ARM64_SFT_ASR and sh.value:
                    b = f"a64asr{32 if w else 64}({b}, {sh.value})"
                elif sh.type == ARM64_SFT_ROR and sh.value:
                    b = f"a64ror{32 if w else 64}({b}, {sh.value})"
        # BSL/BSL16/etc appear as 'bsl' handled by simd; here standard ops
        expr = f"({a} {op} {b})"
        if setflags:
            if w:
                self.em(f"c->n = ((uint32_t){expr} >> 31) & 1; c->z = ((uint32_t){expr} == 0);")
            else:
                self.em(f"c->n = ({expr} >> 63) & 1; c->z = ({expr} == 0);")
            self.em("c->c = 0; c->v = 0;")
        if m not in ("tst", "teq"):
            self.put(self.wr(rd, expr, w))

    # =============== mul family ===============
    def _mul(self, pc, m, ops):
        rd = ops[0][1]
        if m == "mul":
            rn, rm = ops[1][1], ops[2][1]
            w = self.is_w(rd)
            self.put(self.wr(rd, f"({self.rd(rn, w)} * {self.rd(rm, w)})", w))
            return
        if m in ("smulh", "umulh"):
            rn, rm = ops[1][1], ops[2][1]
            f = "a64_smulh" if m == "smulh" else "a64_umulh"
            self.put(self.wr(rd, f"{f}({self.rd(rn)}, {self.rd(rm)})"))
            return
        if m in ("madd", "msub", "mneg"):
            rn, ra, rm = ops[1][1], ops[2][1], ops[3][1]
            w = self.is_w(rd)
            a, b, r = self.rd(rn, w), self.rd(rm, w), self.rd(ra, w)
            if m == "madd":
                expr = f"({r} + ({a} * {b}))"
            elif m == "msub":
                expr = f"({r} - ({a} * {b}))"
            else:
                expr = f"({r} - ({a} * {b}))"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("smaddl", "umaddl", "smsubl", "umsubl", "smnegl", "umnegl",
                 "smull", "umull"):
            # smull rd, rn, rm  (32x32->64); smaddl rd, rn, rm, ra
            if m in ("smull", "umull"):
                rn, rm = ops[1][1], ops[2][1]
                if m == "smull":
                    expr = f"((int64_t)(int32_t){self.rd(rn, True)} * (int64_t)(int32_t){self.rd(rm, True)})"
                else:
                    expr = f"((uint64_t)(uint32_t){self.rd(rn, True)} * (uint64_t)(uint32_t){self.rd(rm, True)})"
                self.put(self.wr(rd, expr))
                return
            if m in ("smnegl", "umnegl"):
                rn, rm = ops[1][1], ops[2][1]
                sgn = "int" if m == "smnegl" else "uint"
                expr = f"(-(({sgn}64_t)({'' if m=='smnegl' else 'u'}{'' }"
                raise Unsupported(pc, 0, m, m)
            rn, rm, ra = ops[1][1], ops[2][1], ops[3][1]
            if m == "smaddl":
                expr = f"(c->x[{ra[1:]}] + (int64_t)(int32_t){self.rd(rn, True)} * (int64_t)(int32_t){self.rd(rm, True)})"
            elif m == "umaddl":
                expr = f"(c->x[{ra[1:]}] + (uint64_t)(uint32_t){self.rd(rn, True)} * (uint64_t)(uint32_t){self.rd(rm, True)})"
            elif m == "smsubl":
                expr = f"(c->x[{ra[1:]}] - (int64_t)(int32_t){self.rd(rn, True)} * (int64_t)(int32_t){self.rd(rm, True)})"
            else:
                expr = f"(c->x[{ra[1:]}] - (uint64_t)(uint32_t){self.rd(rn, True)} * (uint64_t)(uint32_t){self.rd(rm, True)})"
            self.put(self.wr(rd, expr))
            return
        raise Unsupported(pc, 0, m, m)

    # =============== shifts ===============
    def _shift(self, pc, m, ops):
        rd = ops[0][1]
        w = self.is_w(rd)
        if len(ops) >= 3 and ops[2][0] == "imm":   # lsl rd, rn, #imm
            rn = ops[1][1]
            imm = ops[2][1]
            a = self.rd(rn, w)
            bits = 32 if w else 64
            if m == "lsl":
                expr = f"({a} << {imm})" if not w else f"(uint32_t)({a} << {imm})"
            elif m == "lsr":
                expr = f"(({a}) >> {imm})" if not w else f"(uint32_t)(({a}) >> {imm})"
            elif m == "asr":
                expr = f"a64asr{bits}({a}, {imm})"
            else:
                expr = f"a64ror{bits}({a}, {imm})"
            self.put(self.wr(rd, expr, w))
            return
        rn, rm = ops[1][1], ops[2][1]
        bits = 32 if w else 64
        a = self.rd(rn, w)
        b = self.rd(rm, w)
        if m == "lsl":
            expr = f"a64lsl{bits}({a}, {b})"
        elif m == "lsr":
            expr = f"a64lsr{bits}({a}, {b})"
        elif m == "asr":
            expr = f"a64asr{bits}({a}, {b})"
        else:
            expr = f"a64ror{bits}({a}, {b})"
        self.put(self.wr(rd, expr, w))

    # =============== bitfield ===============
    def _bitfield(self, pc, m, ops):
        # capstone gives: ubfx x0, x1, #lsb, #width  etc. plus bfm family raw forms
        rd = ops[0][1]
        w = self.is_w(rd)
        bits = 32 if w else 64
        a = self.rd(ops[1][1], w)
        if m in ("ubfx", "sbfx", "bfxil"):
            lsb = ops[2][1]
            width = ops[3][1]
            if m == "ubfx":
                expr = f"((uint{bits}_t)(({a}) >> {lsb}))" + (f" & {(1 << width) - 1:#x}u" if width < bits else "")
                expr = f"(((uint{bits}_t)({a}) >> {lsb}) & {((1 << width) - 1):#x}ull)" if bits == 64 else \
                       f"(((uint32_t)({a}) >> {lsb}) & {((1 << width) - 1):#x}u)"
            elif m == "sbfx":
                inner = f"(((uint{bits}_t)({a}) >> {lsb}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'})"
                expr = f"((uint{bits}_t)((int{bits}_t)((int{bits}_t)({inner}) << {bits - width}) >> {bits - width}))"
                if bits == 32:
                    expr = f"((uint32_t)((int32_t)((uint32_t)({inner}) << {32 - width}) >> {32 - width}))"
            else:  # bfxil: insert into rd
                inner = f"(((uint{bits}_t)({a}) >> {lsb}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'})"
                dst = self.rd(rd, w)
                mask = (((1 << width) - 1) << lsb)
                expr = f"(({dst} & ~{mask:#x}{'ull' if bits==64 else 'u'}) | (({inner}) << {lsb}))"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("ubfiz", "sbfiz"):
            lsb = ops[2][1]
            width = ops[3][1]
            shift = bits - width
            if m == "ubfiz":
                inner = f"(((uint{bits}_t)({a}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'}) << {lsb})"
                expr = f"({inner})"
                if bits == 32:
                    expr = f"(uint32_t)({inner})"
            else:
                inner = f"((uint{bits}_t)({a}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'})"
                ext = f"((uint{bits}_t)((int{bits}_t)({inner} << {bits - width}) >> {bits - width}) << {lsb})"
                if bits == 32:
                    ext = f"(uint32_t)((int32_t)((uint32_t)({inner}) << {32 - width}) >> {32 - width}) << {lsb}"
                expr = f"({ext})"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("bfm", "sbfm", "ubfm"):
            # capstone format: bfm x0, x1, #immr, #imms
            immr = ops[2][1]
            imms = ops[3][1]
            if m == "ubfm":
                if imms >= immr:
                    width = imms - immr + 1
                    expr = f"(((uint{bits}_t)({a}) >> {immr}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'})"
                else:
                    expr = f"(((uint{bits}_t)({a}) << {bits - immr}) & {((1 << (imms + 1)) - 1):#x}{'ull' if bits==64 else 'u'})"
                if bits == 32:
                    expr = f"(uint32_t)({expr})"
            elif m == "sbfm":
                if imms >= immr:
                    width = imms - immr + 1
                    inner = f"(((uint{bits}_t)({a}) >> {immr}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'})"
                else:
                    inner = f"(((uint{bits}_t)({a}) << {bits - immr}) & {((1 << (imms + 1)) - 1):#x}{'ull' if bits==64 else 'u'})"
                expr = f"((uint{bits}_t)((int{bits}_t)({inner}) << {bits - imms - 1}) >> {bits - imms - 1})"
                if bits == 32:
                    expr = f"(uint32_t)((int32_t)({inner}) << {32 - imms - 1} >> {32 - imms - 1})"
            else:  # bfm generic
                dst = self.rd(rd, w)
                if imms >= immr:
                    width = imms - immr + 1
                    src = f"(((uint{bits}_t)({a}) >> {immr}) & {((1 << width) - 1):#x}{'ull' if bits==64 else 'u'})"
                    mask = (((1 << width) - 1) << immr)
                else:
                    src = f"(((uint{bits}_t)({a}) << {bits - immr}) & {((1 << (imms + 1)) - 1):#x}{'ull' if bits==64 else 'u'})"
                    mask = ((1 << (imms + 1)) - 1)
                expr = f"(({dst} & ~{mask:#x}{'ull' if bits==64 else 'u'}) | ({src}))"
                if bits == 32:
                    expr = f"(uint32_t)({expr})"
            self.put(self.wr(rd, expr, w))
            return
        raise Unsupported(pc, 0, m, m)

    # =============== mrs/msr ===============
    def _mrs(self, pc, ops):
        rd = ops[0][1]
        i = self._cur_insn
        # capstone versions differ: sysreg may be an enum or only in op_str
        name = i.op_str.split(",", 1)[1].strip() if "," in i.op_str else str(ops[1][1])
        sr = None
        for o in i.operands[1:]:
            sr = getattr(o, "sysreg", None)
        if name in ("nzcv",) or (sr is not None and str(sr).endswith("NZCV")):
            self.put(self.wr(rd, "((uint64_t)c->n << 63) | ((uint64_t)c->z << 62) | ((uint64_t)c->c << 61) | ((uint64_t)c->v << 60)", self.is_w(rd)))
            return
        if "cntvct" in name.lower() or (sr is not None and "CNTPCT" in str(sr)):
            self.put(self.wr(rd, "a64_ticks()", self.is_w(rd)))
            return
        if "tpidr_el0" in name.lower():
            self.put(self.wr(rd, "c->tpidr", self.is_w(rd)))
            return
        if "tpidrro" in name.lower():
            self.put(self.wr(rd, "c->tpidrro", self.is_w(rd)))
            return
        if "ctr_el0" in name.lower():
            self.put(self.wr(rd, "0x8444400070080080ull", False))
            return
        if "dczid" in name.lower():
            self.put(self.wr(rd, "4ull", False))
            return
        if "fpcr" in name.lower():
            self.put(self.wr(rd, "c->fpcr", False))
            return
        if "fpsr" in name.lower():
            self.put(self.wr(rd, "c->fpsr", False))
            return
        if "nzcv" in name.lower():
            self.put(self.wr(rd, "((uint64_t)c->n << 63) | ((uint64_t)c->z << 62) | ((uint64_t)c->c << 61) | ((uint64_t)c->v << 60)", self.is_w(rd)))
            return
        # generic: log + return 0
        self.em(f'a64_mrs_unknown(c, "{name}");')
        self.put(self.wr(rd, "0ull", self.is_w(rd)))

    def _msr(self, pc, ops):
        i = self._cur_insn
        name = i.op_str.split(",", 1)[0].strip()
        sr = None
        for o in i.operands:
            sr = getattr(o, "sysreg", None)
        if len(ops) > 1 and ops[1][0] == "reg":
            rexpr = self.rd(ops[1][1], self.is_w(ops[1][1]))
        elif len(ops) > 1:
            rexpr = f"{ops[1][1]:#x}ull"
        else:
            rexpr = "0ull"
        if "nzcv" in name.lower():
            self.em(f"{{ uint64_t __v = {rexpr}; c->n = (__v >> 63) & 1; c->z = (__v >> 62) & 1; c->c = (__v >> 61) & 1; c->v = (__v >> 60) & 1; }}")
            return
        if "tpidr_el0" in name.lower():
            self.em(f"c->tpidr = {rexpr};")
            return
        if "fpcr" in name.lower():
            self.em(f"c->fpcr = {rexpr};")
            return
        if "fpsr" in name.lower():
            self.em(f"c->fpsr = {rexpr};")
            return
        self.em(f'a64_msr_unknown(c, "{name}", {rexpr});')

    # =============== loads/stores ===============
    def _ldst(self, pc, m, ops, i):
        if m == "prfm":
            return
        load = m.startswith("ld")
        neg_ext = "sb" in m or "sh" in m or m == "ldrsw"
        wide = m in ("ldrsw", "ldursw")
        sz = {"b": 8, "h": 16}.get(m.replace("ldr", "l").replace("ldur", "l")
                                   .replace("str", "l").replace("stur", "l")[-1:], None)
        # determine element size from mnemonic
        if m.endswith("b") and m not in ("sxtb", "uxtb"):
            esz = 8
        elif m.endswith("h"):
            esz = 16
        elif m in ("ldrsw", "ldursw"):
            esz = 32
        else:
            esz = None  # default by rt/operand width below
        # operands: rt, [base{, index} {, #imm}] {, #imm(post/pre)}
        rt = ops[0][1]
        w_rt = self.is_w(rt)
        if esz is None:
            esz = 64 if not w_rt and m not in ("ldrsb", "ldrsh") else 32
            if m in ("ldrsb", "ldrsh"):
                esz = 8 if m == "ldrsb" else 16
            if w_rt and load and m in ("ldr", "ldur"):
                esz = 32
        mem = ops[1]
        if mem[0] != "mem":
            raise Unsupported(pc, 0, "ldst non-mem", m)
        mm = mem[1]
        self._cur_insn = i
        base = self.rd(mm.base, False)
        addr = self._mem_expr(mm, i, ops)
        # post/pre index: extra imm operand (3rd token) — detect via raw operand count
        # capstone: for [x0, #8]!  the writeback is implied; op_str ends with '!'
        post = i.op_str.endswith("]")  and len(ops) == 3 and ops[2][0] == "imm"
        wb = "!" in i.op_str
        raw_imm = None
        if len(ops) == 3 and ops[2][0] == "imm":
            raw_imm = ops[2][1]
        if load:
            if esz == 8:
                if neg_ext:
                    expr = f"(int64_t)(int8_t)ld8(c, {addr})"
                else:
                    expr = f"(uint64_t)(uint8_t)ld8(c, {addr})"
            elif esz == 16:
                if neg_ext:
                    expr = f"(int64_t)(int16_t)ld16(c, {addr})"
                else:
                    expr = f"(uint64_t)(uint16_t)ld16(c, {addr})"
            elif esz == 32:
                if neg_ext or m in ("ldrsw", "ldursw"):
                    expr = f"(int64_t)(int32_t)ld32(c, {addr})"
                else:
                    expr = f"(uint64_t)(uint32_t)ld32(c, {addr})"
            else:
                expr = f"ld64(c, {addr})"
            if m in ("ldrsb", "ldrsh", "ldursb", "ldursh", "ldrsw", "ldursw"):
                self.put(self.wr(rt, expr, False))
            else:
                self.put(self.wr(rt, expr, w_rt))
        else:
            val = self.rd(rt, w_rt) if rt.lower() not in ("wzr", "xzr") else ("0u" if w_rt else "0ull")
            self.em(f"st{esz}(c, {addr}, {val if esz >= 32 else f'(uint{esz}_t)({val})'});")
        # writeback
        if raw_imm is not None:
            if i.op_str.rstrip().endswith("]!"):
                self.em(f"c->x[{mm.base[1:]}] = ({base} + ({raw_imm}));")
            elif not i.op_str.rstrip().endswith("]") or "]" in i.op_str.split(",")[-1]:
                pass
        # post-index detection: capstone op_str: "x0, [x1], #8"
        if ", [" in i.op_str and "], #" in i.op_str:
            self.em(f"c->x[{mm.base[1:]}] = ({base} + ({raw_imm}));" if raw_imm is not None else "")

    def _mem_expr(self, mm, i, ops):
        base = self.rd(mm.base, False)
        expr = base
        if mm.index:
            idx = self.rd(mm.index, False)
            # shift from operand shift (capstone attaches to mem op? use op_str heuristic)
            mstr = i.op_str
            shift = None
            if "lsl #" in mstr:
                shift = int(mstr.split("lsl #")[1].split("]")[0].split(",")[0], 0)
            if shift:
                idx = f"({idx} << {shift})"
            expr = f"({base} + {idx})"
        if mm.disp:
            expr = f"({expr} + {mm.disp})" if mm.disp > 0 else f"({expr} - {-mm.disp})"
        return expr

    def _ldstp(self, pc, m, ops, i):
        load = m in ("ldp", "ldpsw")
        r1, r2 = ops[0][1], ops[1][1]
        mm = ops[2]
        if mm[0] != "mem":
            raise Unsupported(pc, 0, "ldstp non-mem", m)
        mem = mm[1]
        self._cur_insn = i
        addr = self._mem_expr(mem, i, ops)
        w1 = self.is_w(r1)
        esz = 32 if w1 else 64
        if load:
            if m == "ldpsw":
                self.put(self.wr(r1, f"(int64_t)(int32_t)ld32(c, {addr})"))
                self.put(self.wr(r2, f"(int64_t)(int32_t)ld32(c, ({addr} + 4))"))
            else:
                f = "ld32" if w1 else "ld64"
                ext = "(uint64_t)(uint32_t)" if w1 else ""
                self.put(self.wr(r1, f"{ext}{f}(c, {addr})", w1))
                self.put(self.wr(r2, f"{ext}{f}(c, ({addr} + {4 if w1 else 8}))", w1))
        else:
            f = "st32" if w1 else "st64"
            v1 = self.rd(r1, w1) if r1.lower() not in ("wzr", "xzr") else ("0u" if w1 else "0ull")
            v2 = self.rd(r2, w1) if r2.lower() not in ("wzr", "xzr") else ("0u" if w1 else "0ull")
            self.em(f"{f}(c, {addr}, {v1});")
            self.em(f"{f}(c, ({addr} + {4 if w1 else 8}), {v2});")
        base = self.rd(mem.base, False)
        breg = mem.base[1:] if mem.base.lower() not in ("xzr", "wzr") else None
        if breg:
            if ", [" in i.op_str and "], #" in i.op_str.split("[")[-1]:
                imm = ops[3][1] if len(ops) > 3 else 0
                self.em(f"c->x[{breg}] = ({base} + ({imm}));")
            elif i.op_str.rstrip().endswith("]!"):
                imm = ops[3][1] if len(ops) > 3 else 0
                self.em(f"c->x[{breg}] = ({base} + ({imm}));")

    def _atomic(self, pc, m, ops, i):
        # MVP: model as plain load/store + 0 to the status reg (single-threaded bring-up)
        if m in ("ldar", "ldxr", "ldaxr"):
            rt, mem = ops[0][1], ops[1]
            mm = mem[1]
            addr = self._mem_expr(mm, i, ops)
            w = self.is_w(rt)
            expr = f"ld64(c, {addr})" if not w else f"(uint64_t)(uint32_t)ld32(c, {addr})"
            if m in ("ldaxr", "ldxr") and len(ops) > 2 and ops[2][0] == "reg":
                pass
            self.put(self.wr(rt, expr, w))
            return
        if m in ("stxr", "stlxr"):
            # stxr ws, rt, [xn]
            ws = ops[0][1]
            rt = ops[1][1]
            mem = ops[2]
            mm = mem[1]
            addr = self._mem_expr(mm, i, ops)
            w = self.is_w(rt)
            f = "st32" if w else "st64"
            val = self.rd(rt, w) if rt.lower() not in ("xzr", "wzr") else ("0u" if w else "0ull")
            self.em(f"{f}(c, {addr}, {val});")
            self.put(self.wr(ws, "0ull", True))
            return
        if m in ("stlr",):
            rt, mem = ops[0][1], ops[1]
            mm = mem[1]
            addr = self._mem_expr(mm, i, ops)
            w = self.is_w(rt)
            f = "st32" if w else "st64"
            val = self.rd(rt, w)
            self.em(f"{f}(c, {addr}, {val});")
            return
        if m in ("ldadd", "ldaddl", "ldclr", "ldclrl", "ldeor", "ldeorl", "ldset",
                 "ldsetl", "swp", "swpl"):
            # op Xt, Xn|[mem] style: ldadd x0, x1, [x2]
            if len(ops) == 3 and ops[2][0] == "mem":
                src, rt, mem = ops[0][1], ops[1][1], ops[2][1]
                addr = self._mem_expr(mem, i, ops)
            elif len(ops) == 3:
                src, rt, mem = ops[0][1], ops[1][1], ops[2][1]
                addr = self._mem_expr(mem, i, ops)
            else:
                raise Unsupported(pc, 0, f"atomic arity {len(ops)}", m)
            w = self.is_w(rt)
            f = "ld32" if w else "ld64"
            sf = "st32" if w else "st64"
            self.em(f"{{ uint{32 if w else 64}_t __old = ({f})(c, {addr});")
            base = m.replace("l", "", 1) if False else m
            opname = m[2:] if m.startswith("ld") else m[2:]
            core = m
            if core.startswith("ldadd"):
                new = f"__old + {self.rd(src, w)}"
            elif core.startswith("ldclr"):
                new = f"__old & ~{self.rd(src, w)}"
            elif core.startswith("ldeor"):
                new = f"__old ^ {self.rd(src, w)}"
            elif core.startswith("ldset"):
                new = f"__old | {self.rd(src, w)}"
            elif core.startswith("swp"):
                new = self.rd(src, w)
            else:
                raise Unsupported(pc, 0, m, m)
            self.em(f"    {sf}(c, {addr}, (uint{32 if w else 64}_t)({new}));")
            self.put(self.wr(rt, "__old", w))
            self.em("}")
            return
        if m in ("cas", "casa", "casal", "casl"):
            if len(ops) == 3 and ops[2][0] == "mem":
                rs, rt, mem = ops[0][1], ops[1][1], ops[2][1]
            else:
                raise Unsupported(pc, 0, "cas form", m)
            addr = self._mem_expr(mem, i, ops)
            w = self.is_w(rt)
            f = "ld32" if w else "ld64"
            sf = "st32" if w else "st64"
            self.em(f"{{ uint{32 if w else 64}_t __old = ({f})(c, {addr});")
            self.em(f"    if (__old == (uint{32 if w else 64}_t){self.rd(rs, w)}) {sf}(c, {addr}, (uint{32 if w else 64}_t){self.rd(rt, w)});")
            self.put(self.wr(rt, "__old", w))
            self.em("}")
            return
        raise Unsupported(pc, 0, m, m)

    def _simd_ldst(self, pc, m, ops, i):
        raise Unsupported(pc, 0, "simd ldst", m)

    # =============== FP ===============
    def _fp(self, pc, m, ops, i):
        rd = ops[0][1]
        # scalar vs vector decided by register name (d/s/h/q)
        rclass = rd[0]
        if rclass in ("q", "v") or (len(ops) > 1 and ops[1][0] == "reg" and ops[1][1][0] in ("q", "v")):
            return self._fpvec(pc, m, ops, i)

        def f64v(tok):
            r = tok[1]
            if tok[0] == "imm":
                return f"{tok[1]:.10e}"
            if r[0] == "s":
                return f"(double)u32f(c->d[{r[1:]}])"
            return f"u64f(c->d[{r[1:]}])"

        def f32v(tok):
            r = tok[1]
            if tok[0] == "imm":
                return f"{tok[1]:.10e}f"
            if r[0] == "d":
                return f"f32u(c->d[{r[1:]}])"
            return f"u32f(c->d[{r[1:]}])"

        def store32(rd, e):
            self.em(f"c->d[{rd[1:]}] = (uint64_t)(uint32_t)f32b({e});")

        def store64(rd, e):
            self.em(f"c->d[{rd[1:]}] = f64b({e});")

        is_s = rclass == "s"
        if m == "fmov":
            src = ops[1]
            if src[0] in ("imm", "fp"):
                v = src[1]
                if rd[0] == "s":
                    store32(rd, f"{v:.10e}f")
                elif rd[0] == "d":
                    store64(rd, f"{v:.10e}")
                else:
                    self.em(f"c->d[{rd[1:]}] = f64b({v:.10e}); c->dhi[{rd[1:]}] = 0;")
                return
            if src[0] == "reg":
                if rd[0] in ("x", "w") and src[1][0] in ("d", "s"):   # gpr -> fpr
                    if src[1][0] == "s":
                        self.em(f"c->d[{rd[1:]}] = (uint64_t)(uint32_t)c->x[{src[1][1:]}];" if rd[0] == "w" else f"c->d[{rd[1:]}] = c->x[{src[1][1:]}];")
                    else:
                        if rd[0] == "w":
                            store32(rd, f"u32f((uint32_t)c->x[{src[1][1:]}])")
                        else:
                            store64(rd, f"u64f(c->x[{src[1][1:]}])")
                    return
                if rd[0] in ("d", "s") and src[1][0] in ("x", "w"):   # fpr -> gpr
                    if src[1][0] == "w" or rd[0] == "s":
                        self.put(self.wr(rd, f"(uint32_t)c->d[{src[1][1:]}]", True))
                    else:
                        self.put(self.wr(rd, f"c->d[{src[1][1:]}]"))
                    return
                # fpr -> fpr
                if rd[0] == "s" and src[1][0] == "s":
                    self.em(f"c->d[{rd[1:]}] = c->d[{src[1][1:]}];")
                    return
                if rd[0] == "d" and src[1][0] == "d":
                    self.em(f"c->d[{rd[1:]}] = c->d[{src[1][1:]}];")
                    return
                if rd[0] == "q":
                    self.em(f"c->d[{rd[1:]}] = c->d[{src[1][1:]}]; c->dhi[{rd[1:]}] = 0;")
                    return
            raise Unsupported(pc, 0, "fmov form", m)
        if m == "fmov" and len(ops) > 2 and ops[2][0] == "imm":
            pass
        if m in ("fabs", "fneg", "fsqrt"):
            if rd[0] == "s":
                fn = {"fabs": "fabsf", "fneg": "negf", "fsqrt": "sqrtf"}[m]
                store32(rd, f"{fn}(u32f(c->d[{ops[1][1][1:]}]))")
            else:
                fn = {"fabs": "fabsd", "fneg": "negd", "fsqrt": "sqrtd"}[m]
                store64(rd, f"{fn}(u64f(c->d[{ops[1][1][1:]}]))")
            return
        if m in ("fadd", "fsub", "fmul", "fdiv", "fmax", "fmin", "fmaxnm", "fminnm", "fmulx", "fabd"):
            op = {"fadd": "+", "fsub": "-", "fmul": "*", "fdiv": "/",
                  "fmax": "", "fmin": "", "fmaxnm": "", "fminnm": "", "fmulx": "*", "fabd": ""}[m]
            if rd[0] == "s":
                a, b = f"u32f(c->d[{ops[1][1][1:]}])", f"u32f(c->d[{ops[2][1][1:]}])"
                if op:
                    expr = f"({a} {op} {b})"
                else:
                    expr = f"a64_{m}_32({a}, {b})"
                store32(rd, expr)
            else:
                a, b = f"u64f(c->d[{ops[1][1][1:]}])", f"u64f(c->d[{ops[2][1][1:]}])"
                if op:
                    expr = f"({a} {op} {b})"
                else:
                    expr = f"a64_{m}_64({a}, {b})"
                store64(rd, expr)
            return
        if m in ("fmadd", "fmsub", "fnmadd", "fnmsub"):
            rn, rm, ra = ops[1][1], ops[2][1], ops[3][1]
            if rd[0] == "s":
                store32(rd, f"a64_{m}_32({u32f(rn)}, {u32f(rm)}, {u32f(ra)})")
            else:
                store64(rd, f"a64_{m}_64({u64f(rn)}, {u64f(rm)}, {u64f(ra)})")
            return
        if m in ("fcmp", "fcmpe"):
            rn = ops[0][1]
            if len(ops) > 1:
                rm = ops[1][1]
                if ops[1][0] in ("imm", "fp"):
                    rhs = f"{ops[1][1]:.10e}" + ("f" if rn[0] == "s" else "")
                else:
                    rhs = (f"u32f(c->d[{rm[1:]}])" if rm[0] == "s" else f"u64f(c->d[{rm[1:]}])")
            else:
                rhs = "0.0f" if rn[0] == "s" else "0.0"
            a = f"u32f(c->d[{rn[1:]}])" if rn[0] == "s" else f"u64f(c->d[{rn[1:]}])"
            self.em(f"a64_fcmp(c, {'32' if rn[0]=='s' else '64'}, {a}, {rhs});")
            return
        if m in ("fcmp" ,"fcmpe"):
            return
        if m in ("fccmp", "fccmn"):
            rn, cond, nzcv = ops[1][1], ops[3][1] if len(ops) > 3 else ops[2][1], None
            # capstone: fcmp-like arg order: fccmp x, y, #nzcv, cond
            rhs = ops[1][1]
            nz = ops[2][1]
            cond = ops[3][1]
            cc = CONDC[cond]
            a = f"u64f(c->d[{ops[0][1][1:]}])" if ops[0][1][0] == "d" else f"u32f(c->d[{ops[0][1][1:]}])"
            raise Unsupported(pc, 0, "fccmp raw", m)
        if m == "fcsel":
            rn, rm, cond = ops[1][1], ops[2][1], ops[3][1]
            cc = CONDC[cond]
            self.em(f"c->d[{rd[1:]}] = a64_cond(c, {cc}) ? c->d[{rm[1:]}] : c->d[{rn[1:]}];")
            return
        if m == "fcvt":
            # fcvt s0, d0  or fcvt d0, s0
            src = ops[1][1]
            if rd[0] == "s" and src[0] == "d":
                store32(rd, f"(float)u64f(c->d[{src[1:]}])")
            elif rd[0] == "d" and src[0] == "s":
                store64(rd, f"(double)u32f(c->d[{src[1:]}])")
            else:
                raise Unsupported(pc, 0, "fcvt form", m)
            return
        if m in ("fcvtzs", "fcvtzu", "fcvtas", "fcvtau", "fcvtms", "fcvtmu",
                 "fcvtns", "fcvtnu", "fcvtps", "fcvtpu"):
            src = ops[1][1]
            fp32 = src[0] == "s"
            a = f"u32f(c->d[{src[1:]}])" if fp32 else f"u64f(c->d[{src[1:]}])"
            rnd = {"fcvtzs": "z", "fcvtzu": "z", "fcvtas": "a", "fcvtau": "a",
                   "fcvtms": "m", "fcvtmu": "m", "fcvtns": "n", "fcvtnu": "n",
                   "fcvtps": "p", "fcvtpu": "p"}[m]
            signed = m in ("fcvtzs", "fcvtas", "fcvtms", "fcvtns", "fcvtps")
            f = f"a64_f2i{rnd}_{'s' if signed else 'u'}"
            if rd[0] == "w" or rd[0] == "x" and fp32 and False:
                pass
            self.put(self.wr(rd, f"{f}({'32' if fp32 else '64'}, {a})", rd[0] == "w"))
            return
        if m in ("scvtf", "ucvtf"):
            src = ops[1][1]
            dst_s = rd[0] == "s"
            w = self.is_w(src)
            f = "a64_i2f_s" if m == "scvtf" else "a64_i2f_u"
            expr = f"{f}({'32' if dst_s else '64'}, {'32' if w else '64'}, {self.rd(src, w)})"
            if dst_s:
                store32(rd, f"(float)({expr})")
            else:
                store64(rd, f"(double)({expr})")
            return
        if m.startswith("frint"):
            rnd = m[5:]
            src = ops[1][1]
            if rd[0] == "s":
                store32(rd, f"a64_frint{rnd}_32(u32f(c->d[{src[1:]}]))")
            else:
                store64(rd, f"a64_frint{rnd}_64(u64f(c->d[{src[1:]}]))")
            return
        if m in ("frecpe", "frsqrte", "frecps", "frsqrts"):
            raise Unsupported(pc, 0, m, m)
        raise Unsupported(pc, 0, f"fp '{m}'", m)

    def _fpvec(self, pc, m, ops, i):
        raise Unsupported(pc, 0, f"fp vector '{m}'", m)

    # =============== SIMD ===============
    def _simd(self, pc, m, ops, i):
        raise Unsupported(pc, 0, f"simd '{m}'", m)


def u32f(bits):
    import struct as _s
    b = _s.pack("<I", bits & 0xFFFFFFFF)
    return _s.unpack("<f", b)[0]


def u64f(bits):
    import struct as _s
    b = _s.pack("<Q", bits & ((1 << 64) - 1))
    return _s.unpack("<d", b)[0]
