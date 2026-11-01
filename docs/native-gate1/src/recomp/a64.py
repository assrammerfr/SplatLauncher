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
        self._tmp = 0           # unique suffix for per-insn temporaries

    # ---------- output ----------
    def em(self, s):
        self.lines.append("    " + s)

    # ---------- register access ----------
    ALIAS = {"fp": "29", "lr": "30", "wfp": "29", "wlr": "30", "wsp": "31"}

    @staticmethod
    def ridx(rn):
        """Register name -> x[]/d[] index string (handles capstone fp/lr aliases).
        Vector registers may carry an arrangement suffix (v0.2d, v4.s); the
        register number before the '.' is the index into c->d[]/c->dhi[].
        Stripping it here keeps every caller from emitting a float subscript
        like c->d[0.2d] (invalid C, observed as C2108/C2059 build failures)."""
        rn = rn.lower()
        if rn in FnEmitter.ALIAS:
            return FnEmitter.ALIAS[rn]
        if rn in ("sp",):
            return "31"
        if "." in rn:
            rn = rn.split(".", 1)[0]
        return rn[1:]

    @staticmethod
    def rd(rn, w=False):
        rn = rn.lower()
        if rn in ("wzr", "xzr"):
            return "0u" if w else "0ull"
        if rn == "sp" or rn == "wsp":
            return "c->x[31]"
        if rn[0] == "w":
            return f"(uint32_t)c->x[{FnEmitter.ridx(rn)}]"
        return f"c->x[{FnEmitter.ridx(rn)}]"

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
            return [f"c->x[{FnEmitter.ridx(rd)}] = (uint32_t)({expr});"]
        return [f"c->x[{FnEmitter.ridx(rd)}] = {expr};"]

    @staticmethod
    def is_w(r):
        r = r.lower()
        return r.startswith("w") and r not in ("wzr", "wsp")

    # ---------- helpers ----------
    def put(self, stmts):
        for s in stmts:
            self.em(s)

    # ---------- vector emit primitives ----------
    # V128 lane layout helpers for the a64_ctx.h vector library.
    # Every vector form resolves to: load source V128s from c->d[]/c->dhi[],
    # call one v_* helper, store the result back.  Sub-128-bit arrangements
    # (.2s/.4h/.8b) write only the low half; the high 64 bits of the register
    # are preserved (UNPREDICTABLE upstream, so keeping the old value matches
    # hardware rather than zeroing).
    ARR_ESZ = {"b": 1, "h": 2, "s": 4, "d": 8}

    @staticmethod
    def _arr_parts(arr):
        """'4s' -> (4, 4).  Returns (lanes, esz_bytes); None if unknown."""
        arr = arr.lower()
        if len(arr) < 2:
            return None
        n = arr[:-1]
        es = FnEmitter.ARR_ESZ.get(arr[-1])
        if not n.isdigit() or es is None:
            return None
        n = int(n)
        if n < 1 or n * es > 16:
            return None
        return n, es

    @staticmethod
    def _lane_suffix(esz):
        """esz bytes -> the a64_ctx.h lane suffix (u8/u16/u32/u64)."""
        return {1: "u8", 2: "u16", 4: "u32", 8: "u64"}[esz]

    @staticmethod
    def _fp_suffix(esz):
        return {4: "f32", 8: "f64"}.get(esz)

    def _newtmp(self, n=4):
        self._tmp += 1
        return [f"__t{self._tmp}_{k}" for k in range(n)]

    def _vld(self, t, reg):
        """Emit V128 load of a vector register -> temp name t."""
        idx = self.ridx(reg)
        self.em(f"V128 {t} = vld128(c, {idx});")
        return t

    def _vst(self, reg, t, esz, lanes):
        """Store temp t back to a vector register honouring sub-128 width."""
        idx = self.ridx(reg)
        if lanes * esz >= 16:
            self.em(f"vst128(c, {idx}, {t});")
        elif lanes * esz == 8:
            self.em(f"c->d[{idx}] = {t}.lo;")
        else:  # 1..4 bytes: keep upper bits of the low 64
            mask = ((1 << (lanes * esz)) - 1)
            self.em(f"c->d[{idx}] = (c->d[{idx}] & ~{mask:#x}ull) | "
                    f"({t}.lo & {mask:#x}ull);")

    def _vzero(self, t):
        self.em(f"V128 {t}; {t}.lo = 0; {t}.hi = 0;")
        return t

    def _vimm(self, t, val, esz, lanes):
        """Emit a V128 broadcast of a scalar immediate across lanes."""
        lane_bits = esz * 8
        lane = val & ((1 << lane_bits) - 1)
        rep = sum(lane << (lane_bits * k) for k in range(64 // lane_bits))
        self.em(f"V128 {t}; {t}.lo = {rep:#x}ull; "
                f"{t}.hi = {rep:#x}ull;")
        return t

    def _vparse(self, i, ops):
        """Parse a vector instruction into (arr, rd, src_toks, imms).

        src_toks are ('v', 'v3') / ('elem', 'v3', esz_char, idx) /
        ('gpr', 'w9') / ('imm', int) / ('fp', float) / ('list', [regs]).
        Operands are taken from capstone's structured ops where possible and
        refined from op_str where the arrangement/element index matters.
        """
        import re as _re
        s = i.op_str
        # destination arrangement comes from op_str (capstone drops it from
        # ops[0] for most SIMD forms)
        head = s.split(",")[0].strip()
        arr = head.split(".")[1] if "." in head else "2d"
        arr = arr.split("[")[0].strip()
        rd = ops[0][1] if ops else ""
        toks = []
        # include ops[0] so element destinations (mov vd.d[1], …) are visible
        for kind, val in ops:
            if kind == "reg":
                r = val.lower() if val else ""
                if not r:
                    continue
                if r[:1] in ("v", "q"):
                    esz_c = None
                    em3 = _re.search(rf"{r}\.([bhsd])\[(\d+)\]", s)
                    if em3:
                        esz_c, eidx = em3.group(1), int(em3.group(2))
                        toks.append(("elem", r, esz_c, eidx))
                    else:
                        toks.append(("v", r))
                elif r[:1] in ("d", "s", "h", "b", "w", "x"):
                    toks.append(("gpr", r))
                else:
                    toks.append(("reg", r))
            elif kind == "imm":
                toks.append(("imm", int(val)))
            elif kind == "fp":
                toks.append(("fp", float(val)))
            elif kind == "mem":
                toks.append(("mem", val))
            else:
                toks.append((kind, val))
        return arr, rd, toks

    def _helper1(self, fn, rd, a, esz, lanes):
        """rd = fn(a)"""
        t = self._newtmp(1)[0]
        self.em(f"V128 {t} = {fn}({a});")
        self._vst(rd, t, esz, lanes)

    def _helper2(self, fn, rd, a, b, esz, lanes):
        t = self._newtmp(1)[0]
        self.em(f"V128 {t} = {fn}({a}, {b});")
        self._vst(rd, t, esz, lanes)

    def _helper3(self, fn, rd, a, b, cc, esz, lanes):
        t = self._newtmp(1)[0]
        self.em(f"V128 {t} = {fn}({a}, {b}, {cc});")
        self._vst(rd, t, esz, lanes)

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
        emitted_labels = set()
        for idx, i in enumerate(insns):
            if i.address >= self.end:
                break
            if i.address in self.labels:
                self.em(f"L_{i.address:x}: ;")
                emitted_labels.add(i.address)
            self._cur_insn = i
            try:
                self.emit_insn(i, idx, insns)
            except Unsupported as e:
                self.gaps.append((e.pc, e.word, e.mnemonic, e.why))
                m = (e.mnemonic or "?").replace('"', "")
                # a64_undef() already logs the pc and sets c->pc = A64_STOP.
                # Do NOT overwrite c->pc with the undef-site address here: that
                # re-dispatches a mid-function pc into fn_dispatch, which misses
                # the table and fires a spurious [unmapped] (misreporting an
                # emitter gap as a fn-table miss) and double-counts undef.
                self.em(f"a64_undef(c, {e.pc:#x}u, {e.word:#x}u, \"{m}\");")
                self.em("return;")
                emitted_upto = e.pc
                break
            except Exception as e:  # noqa: BLE001 - emitter bugs must not kill a build
                self.gaps.append((i.address, struct.unpack("<I", i.bytes[:4])[0],
                                  i.mnemonic, f"emitter:{type(e).__name__}:{e}"))
                self.em(f"a64_undef(c, {i.address:#x}u, {struct.unpack('<I', i.bytes[:4])[0]:#x}u, \"{i.mnemonic}\");")
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
        # define any labels that were referenced but never emitted (decode
        # stalled or an undef stopped emission before reaching them)
        for lbl in sorted(self.labels - emitted_labels):
            self.em(f"L_{lbl:x}: a64_undef(c, {lbl:#x}u, 0, \"no_insn\");")
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
            # tbz branches when the bit is ZERO, tbnz when it is ONE
            cmp_ = "!=" if m == "tbz" else "=="
            self.em(f"if (((({self.rd(reg, self.is_w(reg))} >> {bit}) & 1) {cmp_} 1)) {{")
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
            # must keep THIS frame alive (like _call): an unwind here would
            # leave the callee's return to a mid-function pc with no frame
            # whose loop could resume it inline (observed unmapped resume).
            t = self.rd(ops[0][1])
            self.em(f"{{ uint64_t __r = {nxt:#x}u; c->x[30] = __r; c->pc = {t};")
            self.em("  while (c->pc != __r && c->pc != A64_STOP) fn_dispatch(c);")
            self.em("  if (c->pc != __r) return; }")
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
                 "clrex", "sb", "esb", "psb", "tsb", "csdb", "hint",
                 "xpaclri", "xpaci", "xpacd", "bti",
                 "pacia1716", "autia1716", "pacib1716", "autib1716",
                 "dc", "ic", "at", "tlbi",
                 "stg", "stzg", "st2g", "stz2g"):
            # hints, barriers, cache/TLB maintenance, pointer-auth NOPs:
            # host memory is coherent and we implement no PAC/MTE, so these
            # are architecturally no-ops here.
            return

        # ---- MRS / MSR ----
        if m == "mrs":
            return self._mrs(pc, ops)
        if m == "msr":
            return self._msr(pc, ops)

        # ---- move / arith aliases ----
        if m in ("mov", "movz", "movn", "movk"):
            # vector/scalar-FP mov (ORR alias) must not fall into the GP mov
            # path (it would write c->x[] from c->x[] of the vector numbers).
            # Only inspect operand 1 when it is a register: for an immediate
            # (mov w9,#1) ops[1][1] is an int, and slicing it raised TypeError,
            # turning every scalar reg-imm move into a gap (stopped init at #1).
            if m == "mov" and ops:
                d0 = ops[0][1][:1]
                s0 = ops[1][1][:1] if len(ops) > 1 and ops[1][0] == "reg" else ""
                if d0 in ("v", "q") or s0 in ("v", "q") or \
                   (d0 in ("d", "s") and s0 in ("d", "s")):
                    return self._simd(pc, m, ops, i)
            return self._mov(pc, m, ops)
        if m in ("adrp", "adr"):
            rd = ops[0][1]
            imm = ops[1][1]
            # capstone already resolves adr/adrp immediates to absolute targets
            expr = f"({imm:#x}ull)"
            self.put(self.wr(rd, expr))
            return
        if m in ("add", "adds", "sub", "subs", "cmp", "cmn", "neg", "negs"):
            # vector add/sub/neg must not fall into the GP path (it would
            # write c->x[vector-number]).
            if m in ("add", "sub", "neg") and ops and ops[0][1][:1] in ("v", "q"):
                return self._simd(pc, m, ops, i)
            return self._addsub(pc, m, ops)
        if m in ("and", "ands", "orr", "orn", "eor", "eon", "bic", "bics",
                 "tst", "teq", "mvn"):
            if m in ("and", "orr", "orn", "eor", "bic", "mvn") and ops and \
               ops[0][1][:1] in ("v", "q"):
                return self._simd(pc, m, ops, i)
            return self._logical(pc, m, ops)
        if m in ("mul", "madd", "msub", "mneg", "smulh", "umulh",
                 "smull", "umull", "smaddl", "umaddl", "smsubl", "umsubl",
                 "smnegl", "umnegl"):
            if m in ("smull", "umull", "mul") and ops and ops[0][1][:1] in ("v", "q"):
                return self._simd(pc, m, ops, i)
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
            bits = 32 if w else 64
            a, b = self.rd(rn, w), self.rd(rm, w)
            # EXTR: R = ((Rn:Rm) >> imm), Rm holds the low bits
            if imm == 0:
                expr = b
            else:
                expr = f"((({a}) << {bits - imm}) | (({b}) >> {imm}))" if not w else \
                    f"(uint32_t)(((uint32_t)({a}) << {bits - imm}) | ((uint32_t)({b}) >> {imm}))"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("ubfx", "sbfx", "bfxil", "ubfiz", "sbfiz", "bfm", "sbfm", "ubfm",
                 "bfi", "bfcil" if False else "bfcil", "sbfx16" if False else "sbfx16"):
            return self._bitfield(pc, m, ops)

        # ---- conditional select / compare ----
        if m in ("csel", "csinc", "csinv", "csneg", "cset", "cinc", "cinv",
                 "cneg", "csetm"):
            return self._csel(pc, m, ops, i)
        if m in ("ccmp", "ccmn"):
            return self._ccmp(pc, m, ops, i)

        # ---- loads / stores ----
        if m in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "ldrsw", "ldur", "ldurb",
                 "ldurh", "ldursb", "ldursh", "ldursw", "str", "strb", "strh",
                 "stur", "sturb", "sturh", "prfm", "prfum"):
            return self._ldst(pc, m, ops, i)
        if m in ("ldp", "ldpsw", "stp"):
            return self._ldstp(pc, m, ops, i)
        if m in ("ldtr", "ldtrb", "ldtrh", "ldtrsb", "ldtrsh", "ldtrsw",
                 "sttr", "sttrb", "sttrh"):
            return self._ldst(pc, m, ops, i)
        if m in ("ldxr", "ldaxr", "stxr", "stlxr", "ldar", "stlr",
                 "ldarb", "ldarh", "stlrb", "stlarb", "stlarh", "stlrh",
                 "ldxrb", "ldxrh", "stxrb", "stxrh", "ldaxrb", "ldaxrh",
                 "stlxrb", "stlxrh", "ldar", "ldadd", "ldaddl", "ldset", "ldclr",
                 "ldeor", "swp", "swpl", "cas", "casa", "casal", "casl",
                 "ldaddal", "ldaddb", "ldaddh", "ldclrb", "ldclrh", "ldeorb",
                 "ldeorh", "ldsetb", "ldseth",
                 "ldxp", "stxp", "ldaxp", "stlxp"):
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
                 "fmulx", "fnmul", "faddp", "fmla", "fmls", "fcmeq", "fcmge",
                 "fcmgt", "fcmle", "fcmlt", "fmaxnmv", "fminnmv", "frsqrts",
                 "fdivr" if False else "fdivr"):
            return self._fp(pc, m, ops, i)

        # ---- SIMD immediate: movi vd.<arr>, #imm[, lsl #n] ----
        if m == "movi":
            rd = ops[0][1]
            head = i.op_str.split(",")[0].strip()
            arr = head.split(".")[1] if "." in head else "2d"
            imm_tok = [o[1] for o in ops if o[0] == "imm"]
            if not imm_tok or not isinstance(imm_tok[0], int):
                raise Unsupported(pc, word, "movi imm", m)
            imm = imm_tok[0]
            if "lsl" in i.op_str:
                try:
                    imm <<= int(i.op_str.rsplit("#", 1)[1])
                except (ValueError, IndexError):
                    raise Unsupported(pc, word, "movi lsl", m)
            vi = imm & ((1 << 64) - 1)
            idx = self.ridx(rd)
            if arr in ("2d", "1d"):
                self.em(f"c->d[{idx}] = {vi:#x}ull;")
                if arr == "2d":
                    self.em(f"c->dhi[{idx}] = {vi:#x}ull;")
            else:
                lane_bits = {"4s": 32, "2s": 32, "8h": 16, "4h": 16,
                             "16b": 8, "8b": 8}.get(arr)
                if lane_bits is None:
                    raise Unsupported(pc, word, f"movi arr '{arr}'", m)
                lane = vi & ((1 << lane_bits) - 1)
                rep = sum(lane << (lane_bits * k) for k in range(64 // lane_bits))
                upper = arr in ("4s", "8h", "16b")
                self.em(f"c->d[{idx}] = {rep:#x}ull;")
                self.em(f"c->dhi[{idx}] = {rep:#x}ull;" if upper
                        else f"c->dhi[{idx}] = 0;")
            return

        # ---- SIMD integer / crypto ----
        if m in ("dup", "ins", "umov", "smov", "mov", "tbl", "tbx", "ext", "zip1", "zip2",
                 "uzp1", "uzp2", "trn1", "trn2", "and", "orr", "eor", "bic", "orn",
                 "add", "sub", "sqadd", "uqadd", "sqsub", "uqsub", "mul", "mla", "mls",
                 "shl", "ushl", "sshl", "ushr", "sshr", "srshr", "sqrshl", "uqrshl",
                 "sqshl", "uqshl", "sqshlu", "cnt", "not", "neg", "cmgt", "cmge", "cmeq",
                 "cmlt", "cmle", "abs", "sqabs", "sqneg", "fsqrt", "frecpe", "frsqrte",
                 "cmhi", "cmhs", "cmtst", "addp", "saddlp", "uaddlp", "saddlv", "uaddlv",
                 "smaxp", "umaxp", "sminp", "uminp", "smax", "umax", "smin", "umin",
                 "cmge", "cmhs", "shadd", "shsub", "uhadd", "uhsub", "urhadd",
                 "sqdmulh", "sqrdmulh", "aese", "aesd", "aesimc",
                 "fadd", "fsub", "fmul", "fdiv", "fmax", "fmin", "fabd", "fabe",
                 "scvtf", "ucvtf", "fcvtzs", "fcvtzu", "sqxtn", "uqxtn", "sqxtun",
                 "sxtl", "sxtl2", "uxtl", "uxtl2", "shll", "shll2",
                 "pmull", "pmull2", "pmul", "uadalp", "sadalp", "uaddw", "saddw",
                 "usubw", "ssubw", "sadalp",
                 "ushll", "ushll2", "sshll", "sshll2",                 "xtn", "shrn", "sqshrn", "sqrshrn", "smull", "umull", "smull2", "umull2", "smlal",
                 "smlal2", "umlal", "umlal2",                 "addv", "umaxv", "smaxv", "sminv", "uminv", "sli", "sri",
                 "ssra", "ursra", "srsra", "usra", "bif", "bit", "bsl",
                 "mvni", "mvn"):
            return self._simd(pc, m, ops, i)

        if m in ("ngc", "ngcs"):
            # ngc rd, rm == sbc rd, xzr, rm
            return self._adc(pc, "sbc" if m == "ngc" else "sbcs",
                             [ops[0], (ops[1][0], "xzr"), ops[1]])
        if m in ("adc", "adcs", "sbc", "sbcs"):
            return self._adc(pc, m, ops)
        if m in ("sxtb", "sxth", "sxtw", "uxtb", "uxth", "uxtw"):
            rd, rn = ops[0][1], ops[1][1]
            src_w = m in ("sxtw", "uxtw") or self.is_w(rn)
            bits = {"sxtb": 8, "uxtb": 8, "sxth": 16, "uxth": 16,
                    "sxtw": 32, "uxtw": 32}[m]
            inner = self.rd(rn, src_w or m in ("sxtw", "uxtw"))
            if m.startswith("sx"):
                expr = f"(uint{64 if self.is_w(rd) and rd[0]=='x' or rd[0]=='x' else 64}_t)(int64_t)(int{bits}_t)(uint{bits}_t)({inner})"
            else:
                expr = f"(uint{bits}_t)({inner})"
            # dest is always 64-bit for sxtw/uxtw; keep wr's own width logic
            self.put(self.wr(rd, expr, self.is_w(rd)))
            return
        if m in ("clz", "cls", "rbit", "rev", "rev16", "rev32", "rev64"):
            rd, rn = ops[0][1], ops[1][1]
            if rd.startswith("v"):
                # vector CLZ/REV: byte-wise helpers in a64_ctx.h
                return self._simd(pc, m, ops, i)
            w = self.is_w(rd)
            bits = 32 if w else 64
            if m == "clz":
                expr = f"a64_clz{bits}({self.rd(rn, w)})"
            elif m == "cls":
                expr = f"a64_cls{bits}({self.rd(rn, w)})"
            elif m == "rbit":
                expr = f"a64_rbit{bits}({self.rd(rn, w)})"
            elif m == "rev":
                expr = f"a64_bswap{bits}({self.rd(rn, w)})"
            elif m == "rev16":
                expr = f"a64_rev16_{bits}({self.rd(rn, w)})"
            elif m == "rev32":
                expr = f"a64_bswap32({self.rd(rn, w)})" if w else f"a64_rev32_64({self.rd(rn, w)})"
            else:
                expr = f"a64_bswap64({self.rd(rn, w)})"
            self.put(self.wr(rd, expr, w))
            return
        if m in ("crc32b", "crc32h", "crc32w", "crc32x", "crc32cb", "crc32ch",
                 "crc32cw", "crc32cx"):
            rd, rn, rm = ops[0][1], ops[1][1], ops[2][1]
            self.put(self.wr(rd, f'a64_crc32("{m}", {self.rd(rn, True)}, {self.rd(rm, True)})'))
            return

        raise Unsupported(pc, word, f"unmapped mnemonic '{m}'", m)

    # =============== calls ===============
    def _call(self, target, nxt):
        # PLT / host / tail-chain redirects change c->pc before the callee's
        # own return address comes back. Keep dispatching in THIS frame until
        # __r returns: unwinding here would leave a mid-function return
        # address with no frame to resume (observed: resume pc not in table).
        self.em(f"{{ uint64_t __r = {nxt:#x}u; c->x[30] = __r; c->pc = {target:#x}u;")
        self.em("  while (c->pc != __r && c->pc != A64_STOP) fn_dispatch(c);")
        self.em("  if (c->pc != __r) return; }")

    # =============== flag helpers (statements) ===============
    def _flags_add(self, a, b, w):
        if w:
            self.em(f"{{ uint32_t __a = (uint32_t)({a}); uint32_t __b = (uint32_t)({b}); uint32_t __r = __a + __b;")
            self.em("    c->n = (__r >> 31) & 1; c->z = (__r == 0);")
            self.em("    c->c = ((uint64_t)__a + __b) >> 32; c->v = ((__a ^ __r) & (__b ^ __r)) >> 31; }")
        else:
            self.em(f"{{ uint64_t __a = (uint64_t)({a}); uint64_t __b = (uint64_t)({b}); uint64_t __r = __a + __b;")
            self.em("    c->n = (__r >> 63) & 1; c->z = (__r == 0);")
            self.em("    c->c = (__a + __b < __a); c->v = ((__a ^ __r) & (__b ^ __r)) >> 63; }")

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
        if m in ("movz", "movn", "movk"):
            rd, imm = ops[0][1], ops[1][1]
            shift = ops[2][1] if len(ops) > 2 else 0
            cur_i = getattr(self, "_cur_insn", None)
            if cur_i is not None and len(cur_i.operands) > 1:
                sh = cur_i.operands[1].shift
                if sh.type == ARM64_SFT_LSL:
                    shift = sh.value
            w = self.is_w(rd)
            if m == "movz":
                v = (imm << shift)
                self.put(self.wr(rd, f"{v:#x}ull" if not w else f"{v & 0xFFFFFFFF:#x}u", w))
                return
            if m == "movn":
                v = ~(imm << shift)
                if w:
                    self.put(self.wr(rd, f"{v & 0xFFFFFFFF:#x}u", True))
                else:
                    self.put(self.wr(rd, f"{v & ((1 << 64) - 1):#x}ull", False))
                return
            # movk
            mask = 0xFFFF << shift
            if w:
                cur = f"(uint32_t)c->x[{self.ridx(rd)}]"
                expr = f"(({cur} & ~{mask & 0xFFFFFFFF:#x}u) | {(imm << shift) & 0xFFFFFFFF:#x}u)"
                self.put(self.wr(rd, expr, True))
            else:
                cur = f"c->x[{self.ridx(rd)}]"
                expr = f"(({cur} & ~{mask:#x}ull) | {(imm << shift):#x}ull)"
                self.put(self.wr(rd, expr, False))
            return

    # =============== adc / sbc ===============
    def _adc(self, pc, m, ops):
        rd = ops[0][1]
        rn = ops[1][1]
        rm = ops[2][1]
        w = self.is_w(rd)
        bits = 32 if w else 64
        a = self.rd(rn, w)
        b = self.rd(rm, w)
        carry = m in ("adc", "adcs")
        if not carry:
            b = f"(~{b})" if not w else f"(uint32_t)(~{b})"
        if m in ("adcs", "sbcs"):
            u = "uint32_t" if w else "uint64_t"
            sh = 31 if w else 63
            self.em(f"{{ {u} __a = ({a}); {u} __b = ({b}); "
                    f"{u} __t = ({u})(__a + __b); {u} __r = ({u})(__t + c->c);")
            self.em(f"    c->n = (__r >> {sh}) & 1; c->z = (__r == 0);")
            self.em("    c->c = (__t < __a) || (__r < __t); "
                    f"c->v = ((~(__a ^ __b) & (__a ^ __r)) >> {sh}) & 1;")
            for s in self.wr(rd, "__r", w):
                self.em("    " + s)
            self.em("}")
        else:
            self.put(self.wr(rd, f"({a} + ({b}) + c->c)", w))

    # =============== conditional select ===============
    def cond_tok(self, i):
        """Condition token from op_str's last field (robust across capstone forms)."""
        parts = [p.strip() for p in i.op_str.split(",")]
        tok = parts[-1].lower()
        if tok not in CONDC:
            raise Unsupported(i.address, 0, f"bad cond '{tok}'", i.mnemonic)
        return CONDC[tok]

    def _csel(self, pc, m, ops, i):
        rd = ops[0][1]
        w = self.is_w(rd)
        cc = self.cond_tok(i)
        zero = "0u" if w else "0ull"
        if m in ("cset", "csetm"):
            # empirical (Unicorn golden): cset rd, cond -> cond ? 1 : 0
            if m == "cset":
                expr = f"(a64_cond(c, {cc}) ? ({zero} + 1) : {zero})"
            else:
                expr = f"(a64_cond(c, {cc}) ? ~(uint{32 if w else 64}_t)0 : {zero})"
            self.put(self.wr(rd, expr, w))
            return
        alias = m in ("cinc", "cinv", "cneg")
        rn = ops[1][1]
        a0 = self.rd(rn, w)
        if alias:
            b = a0          # CINC/CINV/CNEG = CSINC/INV/NEG rd, rn, rn, cond
        else:
            rm = ops[2][1]
            b = self.rd(rm, w) if isinstance(rm, str) else zero
        # Empirical truth table (Unicorn golden, verified directly):
        #   CSEL  : cond ? Rn : Rm
        #   CSINC : cond ? Rn : Rm+1    (aliases cinc/cinv/cneg add on the TRUE
        #   CSINV : cond ? Rn : ~Rm      side because capstone prints the
        #   CSNEG : cond ? Rn : -Rm      inverted condition for the alias)
        if not alias:
            if m == "csinc":
                b = f"({b} + 1)" if not w else f"(uint32_t)({b} + 1)"
            elif m == "csinv":
                b = f"(~{b})" if not w else f"(uint32_t)(~{b})"
            elif m == "csneg":
                b = f"(-({b}))" if not w else f"(uint32_t)(-({b}))"
            a = a0
        else:
            a = a0
            if m == "cinc":
                a = f"({a0} + 1)" if not w else f"(uint32_t)({a0} + 1)"
            elif m == "cinv":
                a = f"(~{a0})" if not w else f"(uint32_t)(~{a0})"
            else:
                a = f"(-({a0}))" if not w else f"(uint32_t)(-({a0}))"
        expr = f"(a64_cond(c, {cc}) ? ({a}) : ({b}))"
        self.put(self.wr(rd, expr, w))

    def _ccmp(self, pc, m, ops, i):
        parts = [p.strip() for p in i.op_str.split(",")]
        cc = self.cond_tok(i)
        nzcv_tok = parts[-2].lstrip("#")
        nzcv = int(nzcv_tok, 0)
        rn = ops[0][1]
        w = self.is_w(rn)
        a = self.rd(rn, w)
        rhs = ops[1] if len(ops) > 1 else ("imm", 0)
        if rhs[0] == "reg":
            b = self.rd(rhs[1], w)
        else:
            b = f"({rhs[1]:#x}ull)" if not w else f"({rhs[1] & 0xFFFFFFFF:#x}u)"
        self.em(f"if (a64_cond(c, {cc})) {{")
        if m == "ccmn":
            self._flags_add(a, b, w)
        else:
            self._flags_sublike(a, b, w)   # ccmp compares via a - b like CMP
        self.em("    } else {")
        self.em(f"        c->n = {(nzcv >> 3) & 1}; c->z = {(nzcv >> 2) & 1}; "
                f"c->c = {(nzcv >> 1) & 1}; c->v = {nzcv & 1};")
        self.em("    }")

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
        if m in ("cmp", "cmn") or len(ops) < 3:
            rhs = ops[1]
        else:
            rhs = ops[2]
        # width from any w-prefixed operand (wzr alone is ambiguous)
        w_toks = [rn, rd]
        if rhs[0] == "reg":
            w_toks.append(rhs[1])
        w = any(isinstance(t, str) and t.lower().startswith("w") for t in w_toks)
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
                  "orr": "|", "orn": "|~", "eor": "^", "eon": "^~"}.get(m, "&")
        if m == "mvn":
            # MVN rd, rn{, shift} -> ~(shifted rn); rhs carries the rn operand
            pass
        toks = [rn, rd]
        if rhs[0] == "reg":
            toks.append(rhs[1])
        w = any(t.lower().startswith("w") for t in toks if isinstance(t, str))
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
        if m == "mvn":
            expr = f"(~{b})" if not w else f"(uint32_t)(~{b})"
        else:
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
        if m == "mneg":
            rd = ops[0][1]
            rn, rm = ops[1][1], ops[2][1]
            w = self.is_w(rd)
            self.put(self.wr(rd, f"(0 - ({self.rd(rn, w)} * {self.rd(rm, w)}))", w))
            return
        if m in ("madd", "msub"):
            rn, rm, ra = ops[1][1], ops[2][1], ops[3][1]
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
                if m == "smnegl":
                    expr = (f"(-(int64_t)((int64_t)(int32_t){self.rd(rn, True)} * "
                            f"(int64_t)(int32_t){self.rd(rm, True)}))")
                else:
                    expr = (f"(-(uint64_t)((uint64_t)(uint32_t){self.rd(rn, True)} * "
                            f"(uint64_t)(uint32_t){self.rd(rm, True)}))")
                self.put(self.wr(rd, expr))
                return
            rn, rm, ra = ops[1][1], ops[2][1], ops[3][1]
            if m == "smaddl":
                expr = f"(c->x[{self.ridx(ra)}] + (int64_t)(int32_t){self.rd(rn, True)} * (int64_t)(int32_t){self.rd(rm, True)})"
            elif m == "umaddl":
                expr = f"(c->x[{self.ridx(ra)}] + (uint64_t)(uint32_t){self.rd(rn, True)} * (uint64_t)(uint32_t){self.rd(rm, True)})"
            elif m == "smsubl":
                expr = f"(c->x[{self.ridx(ra)}] - (int64_t)(int32_t){self.rd(rn, True)} * (int64_t)(int32_t){self.rd(rm, True)})"
            else:
                expr = f"(c->x[{self.ridx(ra)}] - (uint64_t)(uint32_t){self.rd(rn, True)} * (uint64_t)(uint32_t){self.rd(rm, True)})"
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
        if m in ("ubfx", "sbfx", "bfxil", "bfi"):
            lsb = ops[2][1]
            width = ops[3][1]
            if m == "bfi":
                mask = (((1 << width) - 1) << lsb)
                dst = self.rd(rd, w)
                bitsx = 32 if w else 64
                inner = f"(((uint{bitsx}_t)({a}) & {((1 << width) - 1):#x}{'ull' if bitsx==64 else 'u'}) << {lsb})"
                expr = f"(({dst} & ~{mask:#x}{'ull' if bitsx==64 else 'u'}) | ({inner}))"
                if bitsx == 32:
                    expr = f"(uint32_t)({expr})"
                self.put(self.wr(rd, expr, w))
                return
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
        if rt[:1] in ("q", "d", "s"):
            # SIMD/FP register transfer (incl. literal form)
            load0 = m.startswith("ld")
            mem0 = ops[1]
            self._cur_insn = i
            if mem0[0] == "mem":
                addr0 = self._mem_expr(mem0[1], i, ops)
                mm0 = mem0[1]
            elif mem0[0] in ("imm", "fp") and load0:
                addr0 = f"{int(mem0[1]) & ((1 << 64) - 1):#x}ull"
                mm0 = None
            else:
                raise Unsupported(pc, 0, "vec ldst non-mem", m)
            return self._vec_ldst(pc, m, ops, i, load0, addr0, mm0)
        w_rt = self.is_w(rt)
        if esz is None:
            esz = 64 if not w_rt and m not in ("ldrsb", "ldrsh") else 32
            if m in ("ldrsb", "ldrsh"):
                esz = 8 if m == "ldrsb" else 16
            if w_rt and load and m in ("ldr", "ldur"):
                esz = 32
        mem = ops[1]
        if mem[0] != "mem" and not (load and mem[0] in ("imm", "fp")):
            raise Unsupported(pc, 0, "ldst non-mem", m)
        self._cur_insn = i
        if mem[0] == "mem":
            mm = mem[1]
            base = self.rd(i.reg_name(mm.base), False)
            addr = self._mem_expr(mm, i, ops)
        else:
            # GP literal: ldr wt/xt, #label (address resolved by capstone)
            mm = None
            base = "0ull"
            addr = f"{int(mem[1]) & ((1 << 64) - 1):#x}ull"
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
        if mm is not None:
            bname = i.reg_name(mm.base)
            if i.op_str.rstrip().endswith("]!"):
                self.em(f"c->x[{self.ridx(bname)}] = {addr};")  # pre-index: new base = addr
            elif ", [" in i.op_str and "], #" in i.op_str and raw_imm is not None:
                self.em(f"c->x[{self.ridx(bname)}] = ({base} + ({raw_imm}));")

    def _mem_expr(self, mm, i, ops):
        base = self.rd(i.reg_name(mm.base), False)
        expr = base
        if mm.index:
            idx = self.rd(i.reg_name(mm.index), False)
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
        base = self.rd(i.reg_name(mem.base), False)
        bname = i.reg_name(mem.base)
        breg = self.ridx(bname) if bname.lower() not in ("xzr", "wzr") else None
        if breg:
            if i.op_str.rstrip().endswith("]!"):
                self.em(f"c->x[{breg}] = {addr};")  # pre-index
            elif ", [" in i.op_str and "], #" in i.op_str:
                imm = ops[3][1] if len(ops) > 3 and ops[3][0] == "imm" else mem.disp
                self.em(f"c->x[{breg}] = ({base} + ({imm}));")

    def _atomic(self, pc, m, ops, i):
        # single-threaded bring-up: exclusives / ordered accesses become
        # plain loads and stores (exclusive store status = 0 = "stored").
        # Width comes from the name suffix (b = byte, h = half).
        width = 8 if m.endswith("b") else (16 if m.endswith("h") else 0)
        is_lse = m.startswith(("ldadd", "ldclr", "ldeor", "ldset", "swp"))
        if not is_lse and not m.startswith("cas"):
            if m.startswith("ld"):
                if m in ("ldxp", "ldaxp") and len(ops) == 3:
                    # load-pair: rt, rt2, [xn] -> two consecutive 64-bit loads
                    rt, rt2, mem = ops[0][1], ops[1][1], ops[2][1]
                    addr = self._mem_expr(mem, i, ops)
                    self.put(self.wr(rt, f"ld64(c, {addr})", False))
                    self.put(self.wr(rt2, f"ld64(c, ({addr} + 8))", False))
                    return
                rt = ops[0][1]
                addr = self._mem_expr(ops[1][1], i, ops)
                w = self.is_w(rt)
                if width == 8:
                    expr = f"ld8(c, {addr})"
                elif width == 16:
                    expr = f"ld16(c, {addr})"
                elif w:
                    expr = f"(uint64_t)(uint32_t)ld32(c, {addr})"
                else:
                    expr = f"ld64(c, {addr})"
                self.put(self.wr(rt, expr, w))
                return
            # store: [rt, mem] or exclusive [ws, rt, mem]
            if m in ("stxp", "stlxp") and len(ops) == 4:
                # store-pair: rs, rt, rt2, [xn]
                rs, rt, rt2, mem = ops[0][1], ops[1][1], ops[2][1], ops[3][1]
                addr = self._mem_expr(mem, i, ops)
                self.em(f"st64(c, {addr}, {self.rd(rt, False)});")
                self.em(f"st64(c, ({addr} + 8), {self.rd(rt2, False)});")
                self.put(self.wr(rs, "0ull", True))
                return
            if len(ops) == 3:
                ws, rt, mem = ops[0][1], ops[1][1], ops[2][1]
            else:
                ws, rt, mem = None, ops[0][1], ops[1][1]
            addr = self._mem_expr(mem, i, ops)
            w = self.is_w(rt)
            if width == 8:
                f = "st8"
            elif width == 16:
                f = "st16"
            else:
                f = "st32" if w else "st64"
            val = self.rd(rt, w) if rt.lower() not in ("xzr", "wzr") else ("0u" if w else "0ull")
            self.em(f"{f}(c, {addr}, {val});")
            if ws is not None:
                self.put(self.wr(ws, "0ull", True))
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
            f = {8: "ld8", 16: "ld16"}.get(width, "ld32" if w else "ld64")
            sf = {8: "st8", 16: "st16"}.get(width, "st32" if w else "st64")
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
            if width:
                new = f"(({new}) & {(1 << width) - 1:#x}u)"
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
        # ld1..ld4 / st1..st4 / ld1r..ld4r register-list forms.
        # op_str: "ld1 {v0.16b, v1.16b}, [x0, #16]" / "ld1 {v0.16b}, [x0], #16"
        import re as _re
        s = i.op_str
        lm = _re.search(r"\{([^}]*)\}", s)
        if not lm:
            raise Unsupported(pc, 0, "simd ldst no list", m)
        regs = [t.strip() for t in lm.group(1).split(",") if t.strip()]
        if not regs:
            raise Unsupported(pc, 0, "simd ldst empty", m)
        memop = next((o for o in ops if o[0] == "mem"), None)
        if memop is None:
            raise Unsupported(pc, 0, "simd ldst non-mem", m)
        self._cur_insn = i
        addr = self._mem_expr(memop[1], i, ops)
        bname = i.reg_name(memop[1].base)
        base = self.rd(bname, False)
        first = regs[0]
        if "." not in first:
            raise Unsupported(pc, 0, "simd ldst arr", m)
        arr = first.split(".", 1)[1].strip().rstrip("}! ")
        esz = {"b": 1, "h": 2, "s": 4, "d": 8}.get(arr[-1:])
        nlanes = int(arr[:-1]) if arr[:-1].isdigit() else 1
        if esz is None or nlanes < 1 or nlanes * esz > 16:
            raise Unsupported(pc, 0, f"simd ldst arr '{arr}'", m)
        bpr = nlanes * esz                 # bytes loaded per register
        nregs = len(regs)
        is_load = m.startswith("ld")
        rep = m.endswith("r")
        kind = int(m[2]) if m[2].isdigit() else 1   # 1..4
        if rep and not is_load:
            raise Unsupported(pc, 0, "simd ldst st_r", m)

        def qwrite(ridx, src, bpr_):
            if bpr_ == 16:
                self.em(f"c->d[{ridx}] = {src}.lo; c->dhi[{ridx}] = {src}.hi;")
            elif bpr_ == 8:
                self.em(f"c->d[{ridx}] = {src}.lo;")
            else:
                self.em(f"c->d[{ridx}] = (c->d[{ridx}] & ~0xffffffffull) | (uint64_t)(uint32_t)({src}.lo);")

        def ldreg(ridx, aexpr, bpr_):
            if bpr_ == 16:
                self.em(f"c->d[{ridx}] = ld64(c, {aexpr}); c->dhi[{ridx}] = ld64(c, ({aexpr} + 8));")
            elif bpr_ == 8:
                self.em(f"c->d[{ridx}] = ld64(c, {aexpr});")
            else:
                self.em(f"c->d[{ridx}] = (c->d[{ridx}] & ~0xffffffffull) | "
                        f"(uint64_t)(uint32_t)ld32(c, {aexpr});")

        def streg(ridx, aexpr, bpr_):
            if bpr_ == 16:
                self.em(f"st64(c, {aexpr}, c->d[{ridx}]); st64(c, ({aexpr} + 8), c->dhi[{ridx}]);")
            elif bpr_ == 8:
                self.em(f"st64(c, {aexpr}, c->d[{ridx}]);")
            else:
                self.em(f"st32(c, {aexpr}, (uint32_t)c->d[{ridx}]);")

        ldfn = {1: "ld8", 2: "ld16", 4: "ld32", 8: "ld64"}[esz]
        stfn = {1: "st8", 2: "st16", 4: "st32", 8: "st64"}[esz]

        for k, rg in enumerate(regs):
            ridx = self.ridx(rg)
            if rep:
                # load one element at addr + k*esz, replicate across lanes
                ea = f"({addr} + {k * esz})"
                self.em("{ V128 __r; uint64_t __e; int __j;")
                self.em(f"  __e = {ldfn}(c, {ea});")
                self.em("  __r.lo = 0; __r.hi = 0;")
                self.em(f"  for (__j = 0; __j < {nlanes}; __j++) "
                        f"memcpy((char*)&__r + __j * {esz}, &__e, {esz});")
                qwrite(ridx, "__r", bpr)
                self.em("}")
            elif kind == 1:
                a = f"({addr} + {k * bpr})"
                if is_load:
                    ldreg(ridx, a, bpr)
                else:
                    streg(ridx, a, bpr)
            else:
                # interleaved: element j of reg k at addr + (j*nregs + k)*esz
                self.em("{ V128 __r; int __j; __r.lo = 0; __r.hi = 0;")
                if is_load:
                    self.em(f"  for (__j = 0; __j < {nlanes}; __j++) {{ uint64_t __e = "
                            f"{ldfn}(c, ({addr} + (__j * {nregs} + {k}) * {esz})); "
                            f"memcpy((char*)&__r + __j * {esz}, &__e, {esz}); }}")
                    qwrite(ridx, "__r", bpr)
                else:
                    self.em(f"  __r.lo = c->d[{ridx}]; __r.hi = c->dhi[{ridx}];")
                    self.em(f"  for (__j = 0; __j < {nlanes}; __j++) {{ uint64_t __e = 0; "
                            f"memcpy(&__e, (const char*)&__r + __j * {esz}, {esz}); "
                            f"{stfn}(c, ({addr} + (__j * {nregs} + {k}) * {esz}), __e); }}")
                self.em("}")

        # writeback: pre-index "[x, #n]!" or post-index "[x], #n"
        if s.rstrip().endswith("]!"):
            self.em(f"c->x[{self.ridx(bname)}] = {addr};")
        else:
            pm = _re.search(r"\],\s*#(-?\d+)", s)
            if pm:
                self.em(f"c->x[{self.ridx(bname)}] = ({base} + ({int(pm.group(1))}));")

    def _vec_ldst(self, pc, m, ops, i, load, addr, mm):
        """LDR/STR of s/d/q registers (and literal forms)."""
        rt = ops[0][1]
        idx = self.ridx(rt)
        cls = rt[0]
        if cls == "q":
            if load:
                self.em(f"c->d[{idx}] = ld64(c, {addr});")
                self.em(f"c->dhi[{idx}] = ld64(c, ({addr} + 8));")
            else:
                self.em(f"st64(c, {addr}, c->d[{idx}]);")
                self.em(f"st64(c, ({addr} + 8), c->dhi[{idx}]);")
        elif cls == "d":
            if load:
                self.em(f"c->d[{idx}] = ld64(c, {addr});")
            else:
                self.em(f"st64(c, {addr}, c->d[{idx}]);")
        else:  # s: low 32 bits, upper bits of the register unchanged
            if load:
                self.em(f"c->d[{idx}] = (c->d[{idx}] & ~0xffffffffull) | "
                        f"(uint64_t)(uint32_t)ld32(c, {addr});")
            else:
                self.em(f"st32(c, {addr}, (uint32_t)c->d[{idx}]);")
        if mm is not None:
            bname = i.reg_name(mm.base)
            base = self.rd(bname, False)
            if i.op_str.rstrip().endswith("]!"):
                self.em(f"c->x[{self.ridx(bname)}] = {addr};")
            elif ", [" in i.op_str and "], #" in i.op_str:
                raw = [o[1] for o in ops if o[0] == "imm"]
                if raw:
                    self.em(f"c->x[{self.ridx(bname)}] = ({base} + ({int(raw[0])}));")

    # =============== FP ===============
    def _fp(self, pc, m, ops, i):
        rd = ops[0][1]
        # scalar vs vector decided by register name (d/s/h/q)
        rclass = rd[0]
        if rclass in ("q", "v") or (len(ops) > 1 and ops[1][0] == "reg" and ops[1][1][0] in ("q", "v")):
            return self._fpvec(pc, m, ops, i)
        # vector-int<->fp conversions print BOTH operands in d/s classes even
        # though the op is the .2D/.4S lane-wise form (verified vs Unicorn:
        # fcvtzs d1, d1 writes V1 with per-lane ints, not X1).  A GPR-dest
        # scalar conversion always prints x/w as the destination.
        #
        # There is no scalar form of these that keeps d/s on BOTH sides: the
        # d/s-to-d/s spelling is always the lane-wise vector op, so it must
        # reach _fpvec (where the arrangement in op_str decides lane count).
        # An h operand is scalar and is caught by the scalar paths below.
        #
        # scvtf/ucvtf are the exception and stay OUT of this hijack: their
        # d/s-to-d/s spelling is the SCALAR form (SCVTF Sd,Sn / Dd,Dn) with
        # the int in the FP reg's low bits and upper lanes zeroed (verified
        # vs Unicorn).  They are handled in the scalar branch below.
        if m in ("fcvtzs", "fcvtzu", "fcvtas", "fcvtau",
                 "fcvtms", "fcvtmu", "fcvtns", "fcvtnu", "fcvtps", "fcvtpu") \
           and rclass in ("d", "s") and len(ops) > 1 and ops[1][0] == "reg" \
           and ops[1][1][0] in ("d", "s"):
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
                if rd[0] in ("x", "w") and src[1][0] in ("d", "s"):   # FP -> GPR (bit move)
                    if src[1][0] == "s" or rd[0] == "w":
                        self.put(self.wr(rd, f"(uint32_t)c->d[{self.ridx(src[1])}]", True))
                    else:
                        self.put(self.wr(rd, f"c->d[{self.ridx(src[1])}]"))
                    return
                if rd[0] in ("d", "s") and src[1][0] in ("x", "w"):   # GPR -> FP (bit move)
                    srcv = self.rd(src[1])
                    if rd[0] == "s":
                        self.em(f"c->d[{self.ridx(rd)}] = (uint64_t)(uint32_t)({srcv});")
                    else:
                        self.em(f"c->d[{self.ridx(rd)}] = (uint64_t)({srcv});")
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
            # NOTE: u32f/u64f at module level are int bit-converters; the C
            # helpers of the same name take c->d[] expressions.
            if rd[0] == "s":
                store32(rd, f"a64_{m}_32(u32f(c->d[{rn[1:]}]), "
                            f"u32f(c->d[{rm[1:]}]), u32f(c->d[{ra[1:]}]))")
            else:
                store64(rd, f"a64_{m}_64(u64f(c->d[{rn[1:]}]), "
                            f"u64f(c->d[{rm[1:]}]), u64f(c->d[{ra[1:]}]))")
            return
        if m == "fnmul":
            rn, rm = ops[1][1], ops[2][1]
            if rd[0] == "s":
                store32(rd, f"negf(u32f(c->d[{rn[1:]}]) * u32f(c->d[{rm[1:]}]))")
            else:
                store64(rd, f"negd(u64f(c->d[{rn[1:]}]) * u64f(c->d[{rm[1:]}]))")
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
            # capstone order: dn, dm, #nzcv (cond only in op_str). If cond
            # holds: compare (sets NZCV); else NZCV := imm.
            rn, rm = ops[0][1], ops[1][1]
            nz_toks = [o[1] for o in ops if o[0] == "imm"]
            if not nz_toks:
                raise Unsupported(pc, word, "fccmp no imm", m)
            nz = int(nz_toks[0])
            cc = self.cond_tok(i)
            fp32 = rn[0] == "s"
            a = f"u32f(c->d[{rn[1:]}])" if fp32 else f"u64f(c->d[{rn[1:]}])"
            b = f"u32f(c->d[{rm[1:]}])" if fp32 else f"u64f(c->d[{rm[1:]}])"
            self.em(f"if (a64_cond(c, {cc})) a64_fcmp(c, {32 if fp32 else 64}, {a}, {b});")
            self.em(f"else {{ c->n = ({nz} >> 3) & 1; c->z = ({nz} >> 2) & 1; "
                    f"c->c = ({nz} >> 1) & 1; c->v = {nz} & 1; }}")
            return
        if m == "fcsel":
            # capstone omits the condition from insn.operands -> use op_str
            rn, rm = ops[1][1], ops[2][1]
            cc = self.cond_tok(i)
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
            f = "a64_i2f_s" if m == "scvtf" else "a64_i2f_u"
            if src[0] in ("d", "s"):
                # SCALAR FP-register source (SCVTF/UCVTF Sd,Sn / Dd,Dn): the
                # int sits in the FP reg's low 32 (s) / 64 (d) bits, the
                # result is written to lane0, and the upper lanes are
                # zeroed.  Verified vs Unicorn: scvtf s1,s1 of int32
                # 0x80000000 -> lane0=0xcf000000, bits[127:32]=0 (ucvtf ->
                # 0x4f000000); scvtf d1,d1 of int64 0x2_00000005 ->
                # 4294967301.0 (full 64-bit read), bits[127:64]=0.
                ib = 32 if src[0] == "s" else 64
                arg = f"(uint32_t)c->d[{src[1:]}]" if ib == 32 else f"c->d[{src[1:]}]"
                if rd[0] == "s":
                    store32(rd, f"(float)({f}(32, {ib}, {arg}))")
                else:
                    store64(rd, f"(double)({f}(64, {ib}, {arg}))")
                self.em(f"c->dhi[{rd[1:]}] = 0;")
                return
            dst_s = rd[0] == "s"
            w = self.is_w(src)
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
        if m in ("fmla", "fmls") and len(ops) == 3:
            # scalar FMLA/FMLS Vd, Vn, Vm: Vd = Vn*Vm (+-) Vd
            rn, rm = ops[1][1], ops[2][1]
            if rd[0] == "s":
                fn = "a64_fmadd_32" if m == "fmla" else "a64_fmsub_32"
                a, b, c = f"u32f(c->d[{rn[1:]}])", f"u32f(c->d[{rm[1:]}])", f"u32f(c->d[{rd[1:]}])"
                store32(rd, f"{fn}({a}, {b}, {c})")
            else:
                fn = "a64_fmadd_64" if m == "fmla" else "a64_fmsub_64"
                a, b, c = f"u64f(c->d[{rn[1:]}])", f"u64f(c->d[{rm[1:]}])", f"u64f(c->d[{rd[1:]}])"
                store64(rd, f"{fn}({a}, {b}, {c})")
            return
        if m in ("fmad", "fms") and len(ops) == 4:
            # FMAD/FMS Dd, Dn, Dm, Da: Dd = Dn*Dm (+/-) Da
            rn, rm, ra = ops[1][1], ops[2][1], ops[3][1]
            if rd[0] == "s":
                fn = "a64_fmadd_32" if m == "fmad" else "a64_fmsub_32"
                store32(rd, f"{fn}(u32f(c->d[{rn[1:]}]), u32f(c->d[{rm[1:]}]), u32f(c->d[{ra[1:]}]))")
            else:
                fn = "a64_fmadd_64" if m == "fmad" else "a64_fmsub_64"
                store64(rd, f"{fn}(u64f(c->d[{rn[1:]}]), u64f(c->d[{rm[1:]}]), u64f(c->d[{ra[1:]}]))")
            return
        raise Unsupported(pc, 0, f"fp '{m}'", m)

    def _fpvec(self, pc, m, ops, i):
        """Advanced-SIMD floating-point vector forms.

        Reached when the destination or a source is a v/q register.  Integer
        SIMD goes to _simd; FP vector arithmetic, comparisons, conversions,
        reductions and pairwise ops land here.
        """
        return self._vector(pc, m, ops, i, fp=True)

    # =============== SIMD ===============
    def _simd(self, pc, m, ops, i):
        return self._vector(pc, m, ops, i, fp=False)

    # ------------------------------------------------------------------
    # shared vector dispatcher
    # ------------------------------------------------------------------
    def _vector(self, pc, m, ops, i, fp):
        """Single dispatcher for every Advanced-SIMD vector form.

        Sub-dispatch by shape: 3-operand lane ops, shift-by-immediate,
        permutes, widening/narrowing, element moves, reductions, pairwise,
        crypto, table lookup, immediate broadcast.
        """
        arr, rd, toks = self._vparse(i, ops)
        if "." not in i.op_str.split(",")[0]:
            # capstone drops the destination arrangement when the dest
            # prints as the low lane of a vector register.  Recover it from
            # a source operand (sminv s1, v1.4s) or, for the int<->fp
            # conversions, from the dest class (fcvtzs s1, s0 is the .4S
            # vector form; a scalar fcvtzs with an FP dest does not exist).
            import re as _re
            sm = _re.search(r"\.(16b|8b|4s|2s|8h|4h|2d|1d)\b", i.op_str)
            if sm:
                arr = sm.group(1)
            elif rd[:1] in ("s", "d") and m in ("fcvtzs", "fcvtzu", "scvtf", "ucvtf"):
                arr = "4s" if rd[0] == "s" else "2d"
        parts = self._arr_parts(arr)
        if parts is None:
            # some forms (umov/smov/dup from GPR) print the dest without an
            # arrangement in op_str; derive from the register class instead
            parts = self._arr_parts(self._guess_arr(rd, toks))
            arr = self._guess_arr(rd, toks)
        if parts is None:
            raise Unsupported(pc, 0, f"vec arr '{arr}' for '{m}'", m)
        lanes, esz = parts

        # ---- element moves: umov / smov / mov-elem / ins / fmov-elem ----
        if m in ("umov", "smov"):
            return self._vec_umov(pc, m, rd, toks, esz, lanes, arr, sign=(m == "smov"))
        if m == "mov":
            d_elem = next((t for t in toks if t[0] == "elem" and t[1] == rd), None)
            if rd[:1] in ("w", "x"):
                # mov wd, vn.s[1] == umov (zero-extend element to GPR)
                return self._vec_umov(pc, "umov", rd, toks, esz, lanes, arr, sign=False)
            if d_elem is not None:
                # INS from a scalar reg (mov vd.s[2], w1) shares _vec_ins
                if any(t[0] == "gpr" for t in toks):
                    return self._vec_ins(pc, rd, toks, arr)
                return self._vec_mov_elem(pc, rd, toks, arr)
            if rd[:1] in ("s", "d", "h", "b"):
                # scalar-register destination from an element source:
                # mov s1, v0.s[1] == FMOV/INS S1, V0.S[1] -- writes the low
                # lane and keeps the rest of the register
                se = next((t for t in toks if t[0] == "elem" and t[1] != rd), None)
                if se is not None:
                    return self._elem_copy(rd, rd[0], 0, se[1], se[2], se[3])
            src_v = next((t for t in toks if t[0] == "v" and t[1] != rd), None)
            if src_v is not None and rd[:1] in ("v", "q"):
                # whole-register copy (ORR alias): both 64-bit halves
                si = self.ridx(src_v[1])
                di = self.ridx(rd)
                self.em(f"c->d[{di}] = c->d[{si}]; c->dhi[{di}] = c->dhi[{si}];")
                return
            raise Unsupported(pc, 0, "vec 'mov' shape", m)
        if m == "ins":
            return self._vec_ins(pc, rd, toks, arr)
        if m == "dup":
            return self._vec_dup(pc, rd, toks, esz, lanes, arr)
        if m in ("fmov",) and any(t[0] in ("fp", "imm") for t in toks):
            return self._vec_fmov_imm(pc, rd, toks, esz, lanes, arr)
        if m == "fmov":
            return self._vec_fmov_reg(pc, m, rd, toks, arr)

        # ---- immediate broadcast: movi / mvni already handled above gen ----
        # ---- pairwise / reduction scalar-dest forms ----
        if m in ("faddp", "fmaxp", "fminp", "fmaxnmv", "fminnmv",
                 "fmaxv", "fminv", "addp", "addv", "saddlv", "uaddlv",
                 "sadalp", "uadalp", "saddlp", "uaddlp", "smaxv", "umaxv",
                 "sminv", "uminv") and (rd[:1] in ("s", "d", "w", "x") or
                                        any(t[0] == "gpr" for t in toks)):
            return self._vec_reduce(pc, m, rd, toks, arr, lanes, esz)

        # ---- table lookup ----
        if m in ("tbl", "tbx"):
            return self._vec_tbl(pc, m, rd, toks, arr, lanes, esz)

        # ---- permutes ----
        if m in ("zip1", "zip2", "uzp1", "uzp2", "trn1", "trn2", "ext"):
            return self._vec_permute(pc, m, rd, toks, arr, lanes, esz)

        # ---- crypto ----
        if m in ("aese", "aesd", "aesimc"):
            return self._vec_crypto(pc, m, rd, toks, arr, lanes, esz)

        # ---- shift by immediate (VSHI / VSHI2 / VSHRREG families) ----
        if m in ("shl", "sshr", "ushr", "srshr", "urshr", "sli", "sri",
                 "sqshl", "uqshl", "sqshlu", "ssra", "ursra", "srsra",
                 "usra") and any(t[0] == "imm" for t in toks):
            return self._vec_shift_imm(pc, m, rd, toks, arr, lanes, esz)

        # ---- shift / arithmetic by register ----
        if m in ("ushl", "sshl", "sqrshl", "uqrshl", "sqshl", "uqshl",
                 "sqshlu", "srshl", "urshl"):
            return self._vec_shift_reg(pc, m, rd, toks, arr, lanes, esz)

        # ---- widening / narrowing ----
        if m in ("xtn", "xtn2", "sqxtn", "sqxtn2", "uqxtn", "uqxtn2",
                 "sqxtun", "sqxtun2", "shrn", "shrn2", "rshrn", "rshrn2",
                 "sqshrn", "sqshrn2", "sqrshrn", "sqrshrn2", "uqshrn",
                 "uqshrn2", "uqrshrn", "uqrshrn2", "sqshrun", "sqrshrun",
                 "shll", "shll2", "ushll", "ushll2", "sshll", "sshll2",
                 "sxtl", "sxtl2", "uxtl", "uxtl2", "addhn", "addhn2",
                 "raddhn", "raddhn2", "subhn", "subhn2", "saddw", "saddw2",
                 "uaddw", "uaddw2", "ssubw", "ssubw2", "usubw", "usubw2",
                 "saddl", "saddl2", "uaddl", "uaddl2", "ssubl", "ssubl2",
                 "usubl", "usubl2", "smull", "smull2", "umull", "umull2",
                 "smlal", "smlal2", "umlal", "umlal2", "smlsl", "smlsl2",
                 "umlsl", "umlsl2", "sqdmull", "sqdmull2", "sqdmlal",
                 "sqdmlal2", "sqdmlsl", "sqdmlsl2", "pmull", "pmull2",
                 "pmul", "sqdmulh", "sqrdmulh"):
            return self._vec_widen(pc, m, rd, toks, arr, lanes, esz)

        # ---- widening by immediate (shifted) ----
        if m in ("shrn", "rshrn", "sqshrn", "sqrshrn", "uqshrn", "uqrshrn",
                 "sqshrun", "sqrshrun") and any(t[0] == "imm" for t in toks):
            return self._vec_widen(pc, m, rd, toks, arr, lanes, esz)

        # ---- general 3-operand lane arithmetic / logic / compare ----
        if len(toks) >= 2:
            return self._vec_laneop(pc, m, rd, toks, arr, lanes, esz, fp)

        raise Unsupported(pc, 0, f"vec '{m}' shape", m)

    def _guess_arr(self, rd, toks):
        """Derive an arrangement string when op_str has none.

        umov/smov/dup from a GPR print no arrangement.  Element ops carry
        their own element size on the token, so prefer that; fall back to the
        destination register class.
        """
        if rd and rd[:1] in ("v", "q"):
            return "16b"
        for t in toks:
            if t[0] == "elem":
                return t[2] + "1"          # 1 x element (lanes unused)
        return None

    # ---- element moves -------------------------------------------------
    def _vec_umov(self, pc, m, rd, toks, esz, lanes, arr, sign):
        src = next((t for t in toks if t[0] == "elem"), None)
        if src is None:
            # umov wd, vn.s  (whole low element, no index printed)
            src = next((t for t in toks if t[0] == "v"), None)
            if src is None:
                raise Unsupported(pc, 0, f"{m} src", m)
            reg, echar, eidx = src[1], "d", 0
        else:
            _, reg, echar, eidx = src
        eesz = self.ARR_ESZ[echar]
        idx = self.ridx(reg)
        off = eidx * eesz
        # read the element out of d[]/dhi[]
        if off + eesz <= 8:
            val = (f"(c->d[{idx}] >> {off * 8})"
                   if eesz == 8 else
                   f"((c->d[{idx}] >> {off * 8}) & {(1 << (eesz * 8)) - 1:#x}ull)")
            if eesz < 8:
                val = f"({val})"
        else:
            ho = off - 8
            val = (f"(c->dhi[{idx}] >> {ho * 8})" if eesz == 8 else
                   f"((c->dhi[{idx}] >> {ho * 8}) & {(1 << (eesz * 8)) - 1:#x}ull)")
        if sign:
            bits = eesz * 8
            if bits < 64:
                val = f"(uint64_t)(int64_t)(int{bits}_t)({val})"
        self.put(self.wr(rd, val, self.is_w(rd)))

    def _vec_mov_elem(self, pc, rd, toks, arr):
        """mov vd.d[1], vn.d[0]  -> element copy preserving other lanes."""
        dst = next((t for t in toks if t[0] == "elem"), None)
        src = next((t[1:] for t in toks if t[0] == "elem" and t is not dst), None)
        if src is None:
            src = next((t[1:] for t in toks if t[0] == "v"), None)
        if dst is None or src is None:
            raise Unsupported(pc, 0, "mov elem shape", "mov")
        _, _dreg, dchar, didx = dst
        esz = self.ARR_ESZ[dchar]
        # src is t[1:]: (reg, esz_char, idx) or (reg,)
        if len(src) >= 3:
            sreg, schar, sidx = src[0], src[1], src[2]
        else:
            sreg, schar, sidx = src[0], dchar, 0
        self._elem_copy(rd, dchar, didx, sreg, schar, sidx)

    def _elem_copy(self, rd, dchar, didx, sreg, schar, sidx):
        esz = self.ARR_ESZ[dchar]
        di, si = self.ridx(rd), self.ridx(sreg)
        doff, soff = didx * esz, sidx * esz
        # read source element into a uint64 temp
        t = self._newtmp(1)[0]
        if soff + esz <= 8:
            self.em(f"uint64_t {t} = (c->d[{si}] >> {soff * 8}) & "
                    f"{(1 << (esz * 8)) - 1:#x}ull;")
        else:
            self.em(f"uint64_t {t} = (c->dhi[{si}] >> {(soff - 8) * 8}) & "
                    f"{(1 << (esz * 8)) - 1:#x}ull;")
        mask = ((1 << (esz * 8)) - 1) << (doff * 8)
        if doff + esz <= 8:
            self.em(f"c->d[{di}] = (c->d[{di}] & ~{mask:#x}ull) | ({t} << {doff * 8});")
        else:
            hoff = doff - 8
            hmask = ((1 << (esz * 8)) - 1) << (hoff * 8)
            self.em(f"c->dhi[{di}] = (c->dhi[{di}] & ~{hmask:#x}ull) | ({t} << {hoff * 8});")

    def _vec_ins(self, pc, rd, toks, arr):
        """ins vd.s[2], w1  /  ins vd.d[0], xn."""
        dst = next((t for t in toks if t[0] == "elem"), None)
        if dst is None:
            raise Unsupported(pc, 0, "ins dst", "ins")
        _, _dreg, dchar, didx = dst
        esz = self.ARR_ESZ[dchar]
        src = next((t for t in toks if t[0] == "gpr"), None)
        if src is not None:
            val = self.rd(src[1], self.is_w(src[1]))
        else:
            se = next((t for t in toks if t[0] == "elem" and t[1] != rd), None)
            if se is None:
                raise Unsupported(pc, 0, "ins src", "ins")
            _, sreg, schar, sidx = se
            si = self.ridx(sreg)
            soff = sidx * self.ARR_ESZ[schar]
            if soff + esz <= 8:
                val = f"((c->d[{si}] >> {soff * 8}) & {(1 << (esz * 8)) - 1:#x}ull)"
            else:
                val = f"((c->dhi[{si}] >> {(soff - 8) * 8}) & {(1 << (esz * 8)) - 1:#x}ull)"
        di = self.ridx(rd)
        doff = didx * esz
        mask = ((1 << (esz * 8)) - 1) << (doff * 8)
        if doff + esz <= 8:
            self.em(f"c->d[{di}] = (c->d[{di}] & ~{mask:#x}ull) | (({val}) << {doff * 8});")
        else:
            hoff = doff - 8
            hmask = ((1 << (esz * 8)) - 1) << (hoff * 8)
            self.em(f"c->dhi[{di}] = (c->dhi[{di}] & ~{hmask:#x}ull) | (({val}) << {hoff * 8});")

    def _vec_dup(self, pc, rd, toks, esz, lanes, arr):
        """dup vd.2d, xn  /  dup vd.4s, v1.s[2]  /  dup vd.16b, w1."""
        idx = self.ridx(rd)
        src = next((t for t in toks if t[0] == "gpr"), None)
        t = self._newtmp(1)[0]
        if src is not None:
            val = self.rd(src[1], self.is_w(src[1]))
            self.em(f"uint64_t {t} = ({val}) & {(1 << (esz * 8)) - 1:#x}ull;")
        else:
            el = next((t_ for t_ in toks if t_[0] == "elem"), None)
            vr = next((t_ for t_ in toks if t_[0] == "v"), None)
            if el is not None:
                _, sreg, schar, sidx = el
                eesz = self.ARR_ESZ[schar]
                soff = sidx * eesz
                si = self.ridx(sreg)
                if soff + eesz <= 8:
                    self.em(f"uint64_t {t} = (c->d[{si}] >> {soff * 8}) & "
                            f"{(1 << (eesz * 8)) - 1:#x}ull;")
                else:
                    self.em(f"uint64_t {t} = (c->dhi[{si}] >> {(soff - 8) * 8}) & "
                            f"{(1 << (eesz * 8)) - 1:#x}ull;")
            elif vr is not None:
                si = self.ridx(vr[1])
                self.em(f"uint64_t {t} = c->d[{si}];")
            else:
                raise Unsupported(pc, 0, "dup src", "dup")
        # broadcast across the arrangement
        lane_bits = esz * 8
        rep = sum(1 << (lane_bits * k) for k in range(64 // lane_bits))
        if lanes * esz >= 16:
            self.em(f"V128 {t}v; {t}v.lo = {t} * {rep:#x}ull; {t}v.hi = {t}v.lo;")
            self.em(f"vst128(c, {idx}, {t}v);")
        elif lanes * esz == 8:
            self.em(f"c->d[{idx}] = {t} * {rep:#x}ull;")
        else:
            mask = (1 << (lanes * esz)) - 1
            self.em(f"c->d[{idx}] = (c->d[{idx}] & ~{mask:#x}ull) | "
                    f"(({t} * {rep:#x}ull) & {mask:#x}ull);")

    def _vec_fmov_imm(self, pc, rd, toks, esz, lanes, arr):
        src = next((t for t in toks if t[0] in ("fp", "imm")), None)
        if src is None:
            raise Unsupported(pc, 0, "fmov imm", "fmov")
        import struct as _s
        idx = self.ridx(rd)
        if esz == 8:
            bits = _s.unpack("<Q", _s.pack("<d", float(src[1])))[0]
            self.em(f"V128 __fm; __fm.lo = {bits:#x}ull; __fm.hi = {bits:#x}ull;")
            self.em(f"vst128(c, {idx}, __fm);")
        else:
            bits = _s.unpack("<I", _s.pack("<f", float(src[1])))[0]
            rep = bits | (bits << 32)
            if lanes * esz >= 16:
                self.em(f"V128 __fm; __fm.lo = {rep:#x}ull; __fm.hi = {rep:#x}ull;")
                self.em(f"vst128(c, {idx}, __fm);")
            elif lanes * esz == 8:
                self.em(f"c->d[{idx}] = {rep:#x}ull;")
            else:
                mask = (1 << (lanes * esz)) - 1
                self.em(f"c->d[{idx}] = (c->d[{idx}] & ~{mask:#x}ull) | "
                        f"({rep:#x}ull & {mask:#x}ull);")

    def _vec_fmov_reg(self, pc, m, rd, toks, arr):
        src = next((t for t in toks if t[0] in ("gpr", "v", "elem")), None)
        if src is None:
            raise Unsupported(pc, 0, "fmov reg", m)
        di = self.ridx(rd)
        if src[0] == "gpr":
            val = self.rd(src[1], self.is_w(src[1]))
            if rd[:1] == "d":
                self.em(f"c->d[{di}] = (uint64_t)({val});")
            else:
                self.em(f"c->d[{di}] = (uint64_t)(uint32_t)({val});")
            return
        if src[0] == "elem":
            self._vec_ins(pc, rd, [("elem", rd, src[2], src[3])] +
                          [t for t in toks if t is not src], arr)
            return
        # fmov d0, v1.d[0] handled by elem; plain fmov s0, v1.s is elem-less
        si = self.ridx(src[1])
        if rd[:1] == "d":
            self.em(f"c->d[{di}] = c->d[{si}];")
        else:
            self.em(f"c->d[{di}] = (c->d[{di}] & ~0xffffffffull) | "
                    f"(c->d[{si}] & 0xffffffffull);")

    # ---- reductions / pairwise-scalar ---------------------------------
    def _vec_reduce(self, pc, m, rd, toks, arr, lanes, esz):
        """Reductions whose destination is a scalar (s/d/w/x register).

        e.g. addv s0, v1.4s  /  faddp d0, v1.2d  /  umaxv b0, v1.16b
        """
        src = next((t for t in toks if t[0] == "v"), None)
        if src is None:
            raise Unsupported(pc, 0, f"{m} src", m)
        a = self._vld(self._newtmp(1)[0], src[1])
        out = self._newtmp(1)[0]
        fp = esz in (4, 8) and m[0] == "f"
        sfx = self._fp_suffix(esz) if fp else self._lane_suffix(esz)

        if m == "addv":
            fn = f"v_addv_{ {'4': '4s', '8': '8h', '16': '16b', '2': '2d'}[str(lanes)] }"
            if lanes not in (4, 8, 16, 2):
                raise Unsupported(pc, 0, f"addv lanes {lanes}", m)
            self.em(f"V128 {out} = {fn}({a}, {lanes});")
        elif m in ("umaxv", "smaxv", "sminv", "uminv", "fmaxv", "fminv",
                   "fmaxnmv", "fminnmv"):
            fn = {"umaxv": "v_umaxv", "smaxv": "v_smaxv", "sminv": "v_sminv",
                  "uminv": "v_uminv", "fmaxv": "v_fmaxv", "fminv": "v_fminv",
                  "fmaxnmv": "v_fmaxnmv", "fminnmv": "v_fminnmv"}[m]
            # these helpers take (V128, int n)
            self.em(f"V128 {out} = {fn}_{sfx}({a}, {lanes});")
        elif m in ("faddp", "fmaxp", "fminp") and rd[:1] == "s" and lanes == 2:
            # scalar pairwise of the low 64 bits
            fn = {"faddp": "v_faddp_scalar_s", "fmaxp": "v_fmaxp_scalar_s",
                  "fminp": "v_fminp_scalar_s"}[m]
            self.em(f"V128 {out}; {out}.lo = 0; {out}.hi = 0;")
            self.em(f"{out}.lo = (uint64_t)(uint32_t)f32b({fn}({a}));")
        elif m in ("faddp", "fmaxp", "fminp", "addp") and rd[:1] == "d":
            fn = {"faddp": "v_faddp_scalar_d", "fmaxp": "v_fmaxp_scalar_d",
                  "fminp": "v_fminp_scalar_d", "addp": "v_addp_scalar_d"}[m]
            self.em(f"V128 {out}; {out}.lo = {fn}({a}); {out}.hi = 0;")
        elif m == "addp" and rd[:1] == "w":
            self.em(f"V128 {out}; {out}.lo = (uint64_t)(uint32_t)(a.lo + a.hi);")
        elif m in ("saddlp", "uaddlp", "sadalp", "uadalp"):
            fn = f"v_{m}_{sfx}"
            self.em(f"V128 {out} = {fn}({a});")
        elif m in ("saddlv", "uaddlv"):
            fn = f"v_{m}_{self._lane_suffix(esz)}"
            self.em(f"V128 {out} = {fn}({a}, {lanes});")
        else:
            raise Unsupported(pc, 0, f"reduce '{m}'", m)
        # store the scalar result
        if rd[:1] in ("w", "x"):
            self.put(self.wr(rd, f"(uint64_t)(uint32_t){out}.lo", self.is_w(rd)))
        elif rd[:1] == "s":
            self.em(f"c->d[{self.ridx(rd)}] = (c->d[{self.ridx(rd)}] & ~0xffffffffull) | "
                    f"({out}.lo & 0xffffffffull);")
        else:  # d
            self.em(f"c->d[{self.ridx(rd)}] = {out}.lo;")

    # ---- table lookup --------------------------------------------------
    def _vec_tbl(self, pc, m, rd, toks, arr, lanes, esz):
        lists = [t for t in toks if t[0] == "list"]
        if not lists:
            # capstone flattens {v0.16b, v1.16b} into reg tokens; rebuild from op_str
            import re as _re
            lm = _re.search(r"\{([^}]*)\}", self._cur_insn.op_str)
            if not lm:
                raise Unsupported(pc, 0, f"{m} list", m)
            regs = [t.strip().split(".")[0] for t in lm.group(1).split(",") if t.strip()]
        else:
            regs = lists[0][1]
        idx_t = next((t for t in toks if t[0] == "v" and t[1] not in regs), None)
        if idx_t is None:
            idx_t = next((t for t in toks if t[0] == "v"), None)
        if idx_t is None:
            raise Unsupported(pc, 0, f"{m} idx", m)
        names = [self._vld(self._newtmp(1)[0], r) for r in regs]
        while len(names) < 4:
            z = self._newtmp(1)[0]
            self._vzero(z)
            names.append(z)
        ia = self._vld(self._newtmp(1)[0], idx_t[1])
        out = self._newtmp(1)[0]
        fn = "v_tbl" if m == "tbl" else "v_tbx"
        args = ", ".join(names[:4] + [ia, str(len(regs))])
        if m == "tbx":
            old = self._vld(self._newtmp(1)[0], rd)
            args = f"{old}, " + args
        self.em(f"V128 {out} = {fn}({args});")
        self._vst(rd, out, esz, lanes)

    # ---- permutes ------------------------------------------------------
    def _vec_permute(self, pc, m, rd, toks, arr, lanes, esz):
        if m == "ext":
            imms = [t[1] for t in toks if t[0] == "imm"]
            if not imms:
                raise Unsupported(pc, 0, "ext imm", m)
            srcs = [t for t in toks if t[0] == "v"]
            if len(srcs) < 2:
                raise Unsupported(pc, 0, "ext srcs", m)
            a = self._vld(self._newtmp(1)[0], srcs[0][1])
            b = self._vld(self._newtmp(1)[0], srcs[1][1])
            out = self._newtmp(1)[0]
            self.em(f"V128 {out} = v_ext({a}, {b}, {imms[0]});")
            self._vst(rd, out, esz, lanes)
            return
        base = {"zip1": "v_zip1", "zip2": "v_zip1",
                "uzp1": "v_uzp1", "uzp2": "v_uzp1",
                "trn1": "v_trn1", "trn2": "v_trn1"}[m]
        hi = 1 if m.endswith("2") else 0
        sfx = self._lane_suffix(esz)
        srcs = [t for t in toks if t[0] == "v"]
        if len(srcs) < 2:
            raise Unsupported(pc, 0, f"{m} srcs", m)
        a = self._vld(self._newtmp(1)[0], srcs[0][1])
        b = self._vld(self._newtmp(1)[0], srcs[1][1])
        out = self._newtmp(1)[0]
        # helpers: v_zip1_u32(a, b, n, hi) -> full-128 result for n lanes
        self.em(f"V128 {out} = {base}_{sfx}({a}, {b}, {lanes}, {hi});")
        self._vst(rd, out, esz, lanes)

    # ---- crypto --------------------------------------------------------
    def _vec_crypto(self, pc, m, rd, toks, arr, lanes, esz):
        if m == "aesimc":
            src = next((t for t in toks if t[0] == "v"), None)
            if src is None:
                raise Unsupported(pc, 0, "aesimc src", m)
            a = self._vld(self._newtmp(1)[0], src[1])
            out = self._newtmp(1)[0]
            self.em(f"V128 {out} = v_aesimc({a});")
            self._vst(rd, out, 1, 16)
            return
        srcs = [t for t in toks if t[0] == "v"]
        if len(srcs) < 2:
            raise Unsupported(pc, 0, f"{m} srcs", m)
        a = self._vld(self._newtmp(1)[0], rd)   # state = current dest
        b = self._vld(self._newtmp(1)[0], srcs[-1][1])
        out = self._newtmp(1)[0]
        self.em(f"V128 {out} = v_{m}({a}, {b});")
        self._vst(rd, out, 1, 16)

    # ---- shifts --------------------------------------------------------
    def _vec_shift_imm(self, pc, m, rd, toks, arr, lanes, esz):
        imms = [t[1] for t in toks if t[0] == "imm"]
        if not imms:
            raise Unsupported(pc, 0, f"{m} imm", m)
        n = imms[0]
        src = next((t for t in toks if t[0] == "v"), None)
        if src is None:
            raise Unsupported(pc, 0, f"{m} src", m)
        a = self._vld(self._newtmp(1)[0], src[1])
        sfx = self._lane_suffix(esz)
        out = self._newtmp(1)[0]
        # VSHI family: v_{m}_{lane}(V128, int n)
        fn = f"v_{m}_{sfx}"
        if m in ("ssra", "ursra", "srsra", "usra"):
            # accumulate-into-dest shifts read the dest first
            cur = self._vld(self._newtmp(1)[0], rd)
            self.em(f"V128 {out} = {fn}({cur}, {a}, {n});")
        else:
            self.em(f"V128 {out} = {fn}({a}, {n});")
        self._vst(rd, out, esz, lanes)

    def _vec_shift_reg(self, pc, m, rd, toks, arr, lanes, esz):
        srcs = [t for t in toks if t[0] == "v"]
        if len(srcs) < 2:
            raise Unsupported(pc, 0, f"{m} srcs", m)
        a = self._vld(self._newtmp(1)[0], srcs[0][1])
        b = self._vld(self._newtmp(1)[0], srcs[1][1])
        sfx = self._lane_suffix(esz)
        out = self._newtmp(1)[0]
        fn = f"v_{m}_{sfx}"
        self.em(f"V128 {out} = {fn}({a}, {b});")
        self._vst(rd, out, esz, lanes)

    # ---- widening / narrowing ------------------------------------------
    def _vec_widen(self, pc, m, rd, toks, arr, lanes, esz):
        """Widening/narrowing/multiply-long family.

        Source element size differs from the destination arrangement's, so
        the pair (src_esz, dst_esz) picks the a64_ctx.h instantiation.
        """
        src = next((t for t in toks if t[0] in ("v", "elem")), None)
        if src is None:
            raise Unsupported(pc, 0, f"{m} src", m)
        if src[0] == "v":
            a = self._vld(self._newtmp(1)[0], src[1])
        else:
            a = self._elem_expr(pc, src, self.ARR_ESZ[src[2]], broadcast=True)
        imms = [t[1] for t in toks if t[0] == "imm"]

        # source element size: infer from the mnemonic family + dst
        # dst esz is `esz`; widening ops have src_esz = esz/2, narrowing esz*2
        WIDEN = {"smull": ("smull", 2), "smull2": ("smull", 2),
                 "umull": ("umull", 2), "umull2": ("umull", 2),
                 "smlal": ("smlal", 2), "smlal2": ("smlal", 2),
                 "umlal": ("umlal", 2), "umlal2": ("umlal", 2),
                 "smlsl": ("smlsl", 2), "smlsl2": ("smlsl", 2),
                 "umlsl": ("umlsl", 2), "umlsl2": ("umlsl", 2),
                 "sqdmull": ("sqdmull", 2), "sqdmull2": ("sqdmull", 2),
                 "sqdmlal": ("sqdmlal", 2), "sqdmlal2": ("sqdmlal", 2),
                 "sqdmlsl": ("sqdmlsl", 2), "sqdmlsl2": ("sqdmlsl", 2),
                 "pmull": ("pmull", 2), "pmull2": ("pmull", 2),
                 "saddw": ("saddw", 2), "saddw2": ("saddw", 2),
                 "uaddw": ("uaddw", 2), "uaddw2": ("uaddw", 2),
                 "ssubw": ("ssubw", 2), "ssubw2": ("ssubw", 2),
                 "usubw": ("usubw", 2), "usubw2": ("usubw", 2),
                 "saddl": ("saddl", 2), "saddl2": ("saddl", 2),
                 "uaddl": ("uaddl", 2), "uaddl2": ("uaddl", 2),
                 "ssubl": ("ssubl", 2), "ssubl2": ("ssubl", 2),
                 "usubl": ("usubl", 2), "usubl2": ("usubl", 2),
                 "ushll": ("ushll", 2), "ushll2": ("ushll", 2),
                 "sshll": ("sshll", 2), "sshll2": ("sshll", 2),
                 "shll": ("shll", 2), "shll2": ("shll", 2),
                 "sxtl": ("sshll", 2), "sxtl2": ("sshll", 2),
                 "uxtl": ("ushll", 2), "uxtl2": ("ushll", 2),
                 "xtn": ("xtn", 1), "xtn2": ("xtn", 1),
                 "sqxtn": ("sqxtn", 1), "sqxtn2": ("sqxtn", 1),
                 "uqxtn": ("uqxtn", 1), "uqxtn2": ("uqxtn", 1),
                 "sqxtun": ("sqxtun", 1), "sqxtun2": ("sqxtun", 1),
                 "shrn": ("shrn", 1), "shrn2": ("shrn", 1),
                 "rshrn": ("rshrn", 1), "rshrn2": ("rshrn", 1),
                 "sqshrn": ("sqshrn", 1), "sqshrn2": ("sqshrn", 1),
                 "sqrshrn": ("sqrshrn", 1), "sqrshrn2": ("sqrshrn", 1),
                 "uqshrn": ("uqshrn", 1), "uqshrn2": ("uqshrn", 1),
                 "uqrshrn": ("uqrshrn", 1), "uqrshrn2": ("uqrshrn", 1),
                 "sqshrun": ("sqshrun", 1), "sqrshrun": ("sqrshrun", 1),
                 "addhn": ("addhn", 1), "addhn2": ("addhn", 1),
                 "raddhn": ("raddhn", 1), "raddhn2": ("raddhn", 1),
                 "subhn": ("subhn", 1), "subhn2": ("subhn", 1),
                 "pmul": ("pmul", 1), "sqdmulh": ("sqdmulh", 0),
                 "sqrdmulh": ("sqrdmulh", 0)}
        if m not in WIDEN:
            raise Unsupported(pc, 0, f"widen '{m}'", m)
        fam, dirn = WIDEN[m]
        s2 = 2 if dirn == 2 else (1 if dirn == 1 else 0)

        if s2 == 0:  # same-width (sqdmulh/sqrdmulh/pmul)
            b_toks = [t for t in toks if t[0] in ("v", "elem") and t is not src]
            if not b_toks:
                raise Unsupported(pc, 0, f"{m} src2", m)
            if b_toks[0][0] == "elem":
                b = self._elem_expr(pc, b_toks[0], esz, broadcast=True)
            else:
                b = self._vld(self._newtmp(1)[0], b_toks[0][1])
            out = self._newtmp(1)[0]
            if fam == "pmul":
                self.em(f"V128 {out} = v_pmul_u8({a}, {b});")
            else:
                self.em(f"V128 {out} = v_{fam}_{self._lane_suffix(esz)}({a}, {b});")
            self._vst(rd, out, esz, lanes)
            return

        # direction: dirn==1 narrowing (src is WIDER), dirn==2 widening
        # (src is NARROWER).  Derive the src lane size from the destination.
        if dirn == 1:
            # narrowing: source lane is DOUBLE the destination lane width
            src_esz = esz * 2
        elif dirn == 2:
            src_esz = esz // 2
        else:
            src_esz = esz
        if src_esz < 1 or src_esz > 8:
            raise Unsupported(pc, 0, f"widen esz {m}", m)
        d_sfx = self._lane_suffix(esz)
        s_sfx = self._lane_suffix(src_esz)

        # two-source widening ops need both sources
        TWO = ("saddw", "uaddw", "ssubw", "usubw", "saddl", "uaddl",
               "ssubl", "usubl", "smull", "umull", "smlal", "umlal",
               "smlsl", "umlsl", "sqdmull", "sqdmlal", "sqdmlsl",
               "pmull", "sqdmulh", "sqrdmulh")
        b_toks = [t for t in toks if t[0] in ("v", "elem") and t is not src]

        if dirn == 2:  # widening: src half-width -> dst
            if fam in TWO and b_toks:
                b = self._vld(self._newtmp(1)[0], b_toks[0][1])
                # accumulating ops read dest first as z
                if fam in ("smlal", "umlal", "smlsl", "umlsl", "sqdmlal",
                           "sqdmlsl"):
                    z = self._vld(self._newtmp(1)[0], rd)
                    out = self._newtmp(1)[0]
                    self.em(f"V128 {out} = v_{fam}_{d_sfx}_{s_sfx}({z}, {a}, {b});")
                else:
                    out = self._newtmp(1)[0]
                    self.em(f"V128 {out} = v_{fam}_{d_sfx}_{s_sfx}({a}, {b});")
            elif imms:
                # ushll/sshll/shll with immediate shift: shift = esz*8 - 16
                # is the usual SXTL/UXTL form; otherwise the encoded imm.
                sh = imms[0]
                shamt = 0 if sh is None else int(sh)
                fn = {"ushll": "v_ushlll", "sshll": "v_sshlll",
                      "shll": "v_ushlll", "uxtl": "v_ushlll",
                      "sxtl": "v_sshlll"}.get(fam, f"v_{fam}")
                if fam in ("uxtl", "sxtl"):
                    shamt = esz * 4
                out = self._newtmp(1)[0]
                self.em(f"V128 {out} = {fn}_{d_sfx}_{s_sfx}({a}, {shamt});")
            else:
                raise Unsupported(pc, 0, f"widen '{m}' args", m)
            self._vst(rd, out, esz, lanes)
            return

        # narrowing (dirn == 1): dst half-width, optional shift immediate
        sh = imms[0] if imms else 0
        base = fam
        out = self._newtmp(1)[0]
        if base in ("shrn", "rshrn"):
            self.em(f"V128 {out} = v_{base}_{d_sfx}_{s_sfx}({a}, {sh});")
        elif base in ("sqshrn", "sqrshrn", "uqshrn", "uqrshrn",
                      "sqshrun", "sqrshrun"):
            self.em(f"V128 {out} = v_{base}_{d_sfx}_{s_sfx}({a}, {sh});")
        elif base in ("xtn", "sqxtn", "uqxtn", "sqxtun"):
            self.em(f"V128 {out} = v_{base}_{d_sfx}_{s_sfx}({a});")
        elif base in ("addhn", "raddhn", "subhn"):
            b_toks2 = [t for t in toks if t[0] == "v" and t is not src]
            if not b_toks2:
                raise Unsupported(pc, 0, f"{m} src2", m)
            b = self._vld(self._newtmp(1)[0], b_toks2[0][1])
            self.em(f"V128 {out} = v_{base}_{d_sfx}_{s_sfx}({a}, {b});")
        else:
            raise Unsupported(pc, 0, f"narrow '{m}'", m)
        self._vst(rd, out, esz, lanes)

    # ---- general lane op ------------------------------------------------
    def _vec_laneop(self, pc, m, rd, toks, arr, lanes, esz, fp):
        """Generic 2/3-operand lane-wise operation.

        Maps the mnemonic + lane type onto an a64_ctx.h helper (VINT2/VINT4/
        VFP2/VFP3/VCMP families) and emits load/call/store.
        """
        import struct as _s
        imms = [t for t in toks if t[0] in ("imm", "fp")]
        srcs = [t for t in toks if t[0] in ("v", "elem", "list")]

        # ---- immediate forms: and/orr/eor/bic/mov(movi) with #imm ----
        if imms and m in ("and", "orr", "eor", "bic", "orn", "mvni",
                          "cmeq", "cmge", "cmgt", "cmle", "cmlt", "fcmeq",
                          "fcmge", "fcmgt", "fcmle", "fcmlt"):
            val = imms[0][1]
            a_t = next((t for t in toks if t[0] == "v"), None)
            if a_t is None:
                raise Unsupported(pc, 0, f"{m} imm src", m)
            a = self._vld(self._newtmp(1)[0], a_t[1])
            out = self._newtmp(1)[0]
            if m in ("cmeq", "cmge", "cmgt", "cmle", "cmlt"):
                z = self._newtmp(1)[0]
                self._vzero(z)
                fn = {"cmeq": "v_cmeq", "cmge": "v_cmge", "cmgt": "v_cmgt",
                      "cmle": "v_cmle", "cmlt": "v_cmlt"}[m]
                # immediate compares are vs zero or the scalar imm; ARM only
                # encodes #0 for cmlt/cmeq, so treat the imm as a broadcast
                if val == 0:
                    self.em(f"V128 {out} = {fn}_{self._lane_suffix(esz)}({a}, {z});")
                else:
                    b = self._newtmp(1)[0]
                    self._vimm(b, val, esz, lanes)
                    self.em(f"V128 {out} = {fn}_{self._lane_suffix(esz)}({a}, {b});")
            elif m in ("fcmeq", "fcmge", "fcmgt", "fcmle", "fcmlt"):
                if val == 0:
                    z = self._newtmp(1)[0]
                    self._vzero(z)
                    fn = {"fcmeq": "v_fcmeq", "fcmge": "v_fcmge",
                          "fcmgt": "v_fcmgt", "fcmle": "v_fcmle",
                          "fcmlt": "v_fcmlt"}[m]
                    self.em(f"V128 {out} = {fn}_{self._fp_suffix(esz)}({a}, {z});")
                else:
                    raise Unsupported(pc, 0, f"{m} #{val}", m)
            else:
                # bitwise immediate: and/orr/eor/bic/mvni/mov-mvi
                lane_bits = esz * 8
                lane = val & ((1 << lane_bits) - 1)
                rep = sum(lane << (lane_bits * k) for k in range(64 // lane_bits))
                b = self._newtmp(1)[0]
                self.em(f"V128 {b}; {b}.lo = {rep:#x}ull; {b}.hi = {rep:#x}ull;")
                if m == "mvni":
                    self.em(f"{b}.lo = ~{b}.lo; {b}.hi = ~{b}.hi;")
                fn = {"and": "v_and", "orr": "v_orr", "eor": "v_eor",
                      "bic": "v_bic", "orn": "v_orn", "mvni": "v_orr",
                      }.get(m, "v_and")
                self.em(f"V128 {out} = {fn}({a}, {b});")
            self._vst(rd, out, esz, lanes)
            return

        UNARY = {"neg", "abs", "not", "mvn", "fneg", "fabs", "fsqrt",
                 "frecpe", "frsqrte", "sqabs", "sqneg", "cnt", "clz",
                 "rev16", "rev32", "rev64", "cls", "urecpe", "ursqrte"}
        if not srcs and m in UNARY:
            # unary with dest as source (neg/abs/not/fneg/fabs/fsqrt/fcvt*)
            a = self._vld(self._newtmp(1)[0], rd)
            out = self._newtmp(1)[0]
            sfx = self._fp_suffix(esz) if fp else self._lane_suffix(esz)
            if m == "not":
                self.em(f"V128 {out} = v_not({a});")
            elif m in ("mvn",):
                self.em(f"V128 {out} = v_orn({a}, {a});")
            elif m in ("cnt",):
                self.em(f"V128 {out} = v_cnt({a});")
            elif m in ("rev16", "rev32", "rev64"):
                self.em(f"V128 {out} = v_{m}({a});")
            elif m in ("clz", "cls"):
                self.em(f"V128 {out} = v_{m}_vec({a});")
            elif m in ("sqabs",):
                self.em(f"V128 {out} = v_abs_{self._lane_suffix(esz)}({a});")
            elif m in ("sqneg", "neg"):
                z = self._newtmp(1)[0]
                self._vzero(z)
                self.em(f"V128 {out} = v_sub_{self._lane_suffix(esz)}({z}, {a});")
            elif m in ("fabs", "fneg", "fsqrt", "frecpe", "frsqrte",
                       "urecpe", "ursqrte"):
                self.em(f"V128 {out} = v_{m}_{sfx}({a});")
            else:
                raise Unsupported(pc, 0, f"unary '{m}'", m)
            self._vst(rd, out, esz, lanes)
            return
        # ---- int<->FP conversions are unary, one vector source ----
        # (placed before the no-src check: capstone may print the vector's
        # low lane as an s/d-class token, e.g. fcvtzs s1, s0 is the .4S
        # vector form fcvtzs v1.4s, v0.4s)
        if m in ("scvtf", "ucvtf", "fcvtzs", "fcvtzu"):
            src = srcs[0] if srcs else None
            if src is None:
                g = next((t for t in toks if t[0] == "gpr"
                          and t[1][:1] in ("s", "d") and t[1] != rd), None)
                if g is None:
                    g = next((t for t in toks if t[0] == "gpr"
                              and t[1][:1] in ("s", "d")), None)
                if g is not None:
                    src = ("v", g[1])
            if src is None:
                raise Unsupported(pc, 0, f"{m} src", m)
            return self._vec_cvt(pc, m, rd, src, esz, lanes, imms)
        if not srcs:
            raise Unsupported(pc, 0, f"laneop '{m}' no src", m)

        a_t = srcs[0]
        a = (self._vld(self._newtmp(1)[0], a_t[1]) if a_t[0] == "v"
             else self._elem_expr(pc, a_t, esz))

        # ---- unary with an explicit single source (cnt v0.8b, v1.8b) ----
        if m in UNARY and len(srcs) >= 1:
            out = self._newtmp(1)[0]
            if m in ("fabs", "fneg", "fsqrt", "frecpe", "frsqrte",
                     "urecpe", "ursqrte"):
                sfx = "_f32" if esz == 4 else "_f64"
                self.em(f"V128 {out} = v_{m}{sfx}({a});")
            elif m in ("mvn", "not"):
                self.em(f"V128 {out} = v_not({a});")
            elif m in ("cnt", "rev16", "rev32", "rev64"):
                self.em(f"V128 {out} = v_{m}({a});")
            elif m in ("clz", "cls"):
                self.em(f"V128 {out} = v_{m}_{self._lane_suffix(esz)}({a});")
            elif m == "sqabs":
                self.em(f"V128 {out} = v_abs_{self._lane_suffix(esz)}({a});")
            elif m in ("neg", "sqneg"):
                z = self._newtmp(1)[0]
                self._vzero(z)
                self.em(f"V128 {out} = v_sub_{self._lane_suffix(esz)}({z}, {a});")
            else:
                raise Unsupported(pc, 0, f"unary '{m}'", m)
            self._vst(rd, out, esz, lanes)
            return

        # ---- BSL / BIT / BIF (3 operands; dest is a source too) ----
        if m in ("bsl", "bit", "bif"):
            srcs3 = [t for t in toks if t[0] == "v"]
            if len(srcs3) < 3:
                raise Unsupported(pc, 0, f"{m} needs 3 vector srcs", m)
            a3 = self._vld(self._newtmp(1)[0], srcs3[0][1])
            b3 = self._vld(self._newtmp(1)[0], srcs3[1][1])
            c3 = self._vld(self._newtmp(1)[0], srcs3[2][1])
            out = self._newtmp(1)[0]
            # BSL Vd,Vn,Vm = (Vd&Vn)|(~Vd&Vm); BIT = (Vm&Vn)|(~Vm&Vd);
            # BIF = (Vm&Vd)|(~Vm&Vn)
            if m == "bsl":
                self.em(f"V128 {out} = v_bsl({a3}, {b3}, {c3});")
            elif m == "bit":
                self.em(f"V128 {out} = v_bit({c3}, {b3}, {a3});")
            else:
                self.em(f"V128 {out} = v_bif({c3}, {a3}, {b3});")
            self._vst(rd, out, esz, lanes)
            return

        # second source (register or by-element broadcast)
        b_expr = None
        if len(toks) >= 3 or (len(srcs) >= 2):
            b_t = next((t for t in srcs[1:]), None)
            if b_t is not None:
                if b_t[0] == "v":
                    b_expr = self._vld(self._newtmp(1)[0], b_t[1])
                elif b_t[0] == "elem":
                    b_expr = self._elem_expr(pc, b_t, esz, broadcast=True)

        # third source (fmla/fmls/acc forms: rd is the accumulator)
        c_expr = None
        if m in ("fmla", "fmls", "mla", "mls", "sqdmlal", "sqdmlsl",
                 "smlal", "smlsl", "umlal", "umlsl") and b_expr is not None:
            c_expr = self._vld(self._newtmp(1)[0], rd)

        out = self._newtmp(1)[0]
        sfx = self._fp_suffix(esz) if fp else self._lane_suffix(esz)

        # ---- mnemonic -> helper ----
        FP_BIN = {"fadd": "v_fadd", "fsub": "v_fsub", "fmul": "v_fmul",
                  "fdiv": "v_fdiv", "fmax": "v_fmax", "fmin": "v_fmin",
                  "fmaxnm": "v_fmaxnm", "fminnm": "v_fminnm", "fabd": "v_fabd",
                  "fmulx": "v_fmulx", "faddp": "v_faddp", "fmaxp": "v_fmaxp",
                  "fminp": "v_fminp", "frecps": "v_frecps", "frsqrts": "v_frsqrts"}
        FP_CMP = {"fcmeq": "v_fcmeq", "fcmge": "v_fcmge", "fcmgt": "v_fcmgt",
                  "fcmle": "v_fcmle", "fcmlt": "v_fcmlt"}
        INT_BIN = {"add": "v_add", "sub": "v_sub", "mul": "v_mul",
                   "and": "v_and", "orr": "v_orr", "eor": "v_eor",
                   "bic": "v_bic", "orn": "v_orn", "addp": "v_addp",
                   "smax": "v_smax", "umax": "v_umax", "smin": "v_smin",
                   "umin": "v_umin", "smaxp": "v_smaxp", "umaxp": "v_umaxp",
                   "sminp": "v_sminp", "uminp": "v_uminp",
                   "shadd": "v_shadd", "uhadd": "v_uhadd", "shsub": "v_shsub",
                   "uhsub": "v_uhsub", "urhadd": "v_urhadd",
                   "sqadd": "v_sqadd", "uqadd": "v_uqadd", "sqsub": "v_sqsub",
                   "uqsub": "v_uqsub", "sqdmulh": "v_sqdmulh",
                   "sqrdmulh": "v_sqrdmulh", "pmul": "v_pmul"}
        INT_CMP = {"cmeq": "v_cmeq", "cmge": "v_cmge", "cmgt": "v_cmgt",
                   "cmhi": "v_cmhi", "cmhs": "v_cmhs", "cmtst": "v_cmtst",
                   "cmle": "v_cmle", "cmlt": "v_cmlt"}
        ACC3 = {"fmla": "v_fmla", "fmls": "v_fmls", "mla": "v_mla",
                "mls": "v_mls"}

        if m in FP_CMP and b_expr is not None and sfx:
            self.em(f"V128 {out} = {FP_CMP[m]}_{sfx}({a}, {b_expr});")
        elif m in INT_CMP and b_expr is not None:
            self.em(f"V128 {out} = {INT_CMP[m]}_{self._lane_suffix(esz)}({a}, {b_expr});")
        elif m in ACC3 and b_expr is not None and c_expr is not None:
            self.em(f"V128 {out} = {ACC3[m]}_{sfx}({c_expr}, {a}, {b_expr});")
        elif m in FP_BIN and b_expr is not None and sfx:
            self.em(f"V128 {out} = {FP_BIN[m]}_{sfx}({a}, {b_expr});")
        elif m in INT_BIN and b_expr is not None:
            if m in ("and", "orr", "eor", "bic", "orn"):
                # bitwise ops act on the whole 128-bit register; the header
                # defines them unsuffixed (v_and/v_orr/v_eor/v_bic/v_orn).
                self.em(f"V128 {out} = {INT_BIN[m]}({a}, {b_expr});")
            else:
                self.em(f"V128 {out} = {INT_BIN[m]}_{self._lane_suffix(esz)}({a}, {b_expr});")
        elif m in ("fnm", "fms"):
            # fnm vd, vn, vm = -(vn*vm) ; fms vd, vn, vm = -(vn*vm) + vd
            t1 = self._newtmp(1)[0]
            self.em(f"V128 {t1} = v_fmul_{sfx}({a}, {b_expr});")
            z = self._newtmp(1)[0]
            self._vzero(z)
            t2 = self._newtmp(1)[0]
            self.em(f"V128 {t2} = v_fsub_{sfx}({z}, {t1});")
            if m == "fms" and c_expr is not None:
                self.em(f"V128 {out} = v_fadd_{sfx}({t2}, {c_expr});")
            else:
                self.em(f"V128 {out} = {t2};")
        elif m in ("fabs", "fneg", "fsqrt", "frecpe", "frsqrte") and sfx:
            self.em(f"V128 {out} = v_{m}_{sfx}({a});")
        elif m in ("scvtf", "ucvtf", "fcvtzs", "fcvtzu", "fcvtas", "fcvtau",
                   "fcvtms", "fcvtmu", "fcvtns", "fcvtnu", "fcvtps", "fcvtpu"):
            return self._vec_cvt(pc, m, rd, a, esz, lanes, imms)
        elif m in ("frintn", "frintp", "frintm", "frintz", "frinta",
                   "frintx", "frinti"):
            self.em(f"V128 {out} = v_{m}_{sfx}({a});")
        elif m in ("sxtl", "uxtl"):
            self.em(f"V128 {out} = v_{'sshlll' if m == 'sxtl' else 'ushlll'}_"
                    f"{self._lane_suffix(esz)}_{self._lane_suffix(esz // 2)}({a}, {esz * 4});")
        else:
            raise Unsupported(pc, 0, f"laneop '{m}'", m)
        self._vst(rd, out, esz, lanes)

    def _elem_expr(self, pc, t, esz, broadcast=False):
        """Expression for an element operand; broadcast builds a V128."""
        _, reg, echar, eidx = t
        eesz = self.ARR_ESZ[echar]
        si = self.ridx(reg)
        off = eidx * eesz
        if off + eesz <= 8:
            val = f"((c->d[{si}] >> {off * 8}) & {(1 << (eesz * 8)) - 1:#x}ull)"
        else:
            val = f"((c->dhi[{si}] >> {(off - 8) * 8}) & {(1 << (eesz * 8)) - 1:#x}ull)"
        if not broadcast:
            return val
        t_ = self._newtmp(1)[0]
        lane_bits = eesz * 8
        rep = sum(1 << (lane_bits * k) for k in range(64 // lane_bits))
        self.em(f"V128 {t_}; {t_}.lo = ({val}) * {rep:#x}ull; {t_}.hi = {t_}.lo;")
        return t_

    def _vec_cvt(self, pc, m, rd, a, esz, lanes, imms):
        """Vector FP<->int conversions (per-lane), incl. #fbits scaling.

        `a` is a source token.  Only scvtf/ucvtf/fcvtzs/fcvtzu have helpers
        in a64_ctx.h; the other rounding-mode variants stay gaps.
        """
        if a[0] == "v":
            a = self._vld(self._newtmp(1)[0], a[1])
        else:
            a = self._elem_expr(pc, a, esz)
        sfx = self._fp_suffix(esz)
        out = self._newtmp(1)[0]
        sh = imms[0][1] if imms else None
        src = a
        if sh is not None:
            st = self._newtmp(1)[0]
            if sfx == "f32":
                self.em(f"V128 {st} = v_scalbn_f32({a}, {sh});")
            else:
                self.em(f"V128 {st} = v_scalbn_f64({a}, {sh});")
            src = st
        # int result -> lane suffix of the int side
        if m.startswith("fcvt"):
            isz = esz  # same lane width for fcvtzs/fcvtzu on same arrangement
            fn = f"v_{m}_{self._lane_suffix(isz)}_{sfx}"
            self.em(f"V128 {out} = {fn}({src});")
        else:
            fn = f"v_{m}_{sfx}_{self._lane_suffix(esz)}"
            self.em(f"V128 {out} = {fn}({src});")
        self._vst(rd, out, esz, lanes)


def u32f(bits):
    import struct as _s
    b = _s.pack("<I", bits & 0xFFFFFFFF)
    return _s.unpack("<f", b)[0]


def u64f(bits):
    import struct as _s
    b = _s.pack("<Q", bits & ((1 << 64) - 1))
    return _s.unpack("<d", b)[0]
