#!/usr/bin/env python3
"""Differential test: recompiled A64 semantics vs Unicorn (golden).

Samples real instructions from the Splatoon 3 sdk .text, recompiles each one
with the same FnEmitter the build uses, compiles a harness that runs them on
known initial states, and compares final architectural state against Unicorn
running the identical instruction on the identical state.

Branch/call/svc instructions are skipped (dispatch-level semantics, not
instruction semantics); unmapped mnemonics are reported separately.
"""
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import nso_scan  # noqa: E402
from a64 import FnEmitter, MD  # noqa: E402

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM  # noqa: E402
from unicorn.arm64_const import (  # noqa: E402
    UC_ARM64_REG_NZCV, UC_ARM64_REG_PC, UC_ARM64_REG_SP)
import unicorn.arm64_const as arm64c  # noqa: E402

XR = [getattr(arm64c, f"UC_ARM64_REG_X{r}") for r in range(31)] + [UC_ARM64_REG_SP]
DR = [getattr(arm64c, f"UC_ARM64_REG_D{r}") for r in range(32)]

ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "build", "diff")
os.makedirs(OUT, exist_ok=True)

SCRATCH = 0x70000000
SCRATCH_SIZE = 0x40000
IMG_BASE = 0x0E6FE000
CASES = 240
SEED = 20261030

SKIP_MNEM = {"b", "bl", "br", "blr", "ret", "svc", "brk", "hlt", "udf",
             "cbz", "cbnz", "tbz", "tbnz", "braa", "retaa"}


def sample_cases(text):
    rng = random.Random(SEED)
    seen = {}
    cands = []
    for i in MD.disasm(text, IMG_BASE):
        m = i.mnemonic
        if m in SKIP_MNEM or m.startswith("b.") or m.startswith("bc."):
            continue
        cands.append(i)
        if m not in seen:
            seen[m] = i
        if len(cands) > 6000:
            break
    picked = list(seen.values())
    rest = [c for c in cands if c.mnemonic not in seen]
    rng.shuffle(rest)
    picked += rest[: max(0, CASES - len(picked))]
    return picked[:CASES]


def sample_from_fdes():
    """Disassemble within FDE ranges (like gen.py) so data at .text start
    cannot stall whole-text disassembly."""
    import gen  # noqa: E402  (module-level defs only; main() is guarded)
    mod = gen.load_module("sdk")
    nso = mod["nso"]
    tlo = nso.segs[0]["mem_off"]
    text = nso.mem[tlo]
    rng0 = random.Random(SEED)
    seen = {}
    cands = []
    for s, e in gen.functions_of(mod):
        for i in MD.disasm(text[s - tlo: e - tlo], IMG_BASE + s):
            m = i.mnemonic
            if m in SKIP_MNEM or m.startswith("b.") or m.startswith("bc."):
                continue
            cands.append(i)
            if m not in seen:
                seen[m] = i
        if len(cands) > 8000:
            break
    picked = list(seen.values())
    rest = [c for c in cands if seen.get(c.mnemonic) is not c]
    rng0.shuffle(rest)
    picked += rest[: max(0, CASES - len(picked))]
    return picked[:CASES]


def main():
    nso = nso_scan.Nso(os.path.join(r"E:\S3_extract", "sdk"))
    text = nso.mem[nso.segs[0]["mem_off"]]
    tlo = nso.segs[0]["mem_off"]
    cases = sample_from_fdes()

    rng = random.Random(SEED + 1)
    entries = []   # (idx, insn, body, state)
    unmapped = []
    for idx, ins in enumerate(cases):
        em = FnEmitter(ins.address, ins.address + 4)
        body = em.run(ins.bytes, ins.address)
        if em.gaps:
            unmapped.append((ins.mnemonic, ins.op_str, em.gaps[0][3]))
            continue
        if "fn_dispatch" in body:
            unmapped.append((ins.mnemonic, ins.op_str, "cross-fn call"))
            continue
        st = {
            "x": [SCRATCH + 0x2000 + (r * 0x40) for r in range(32)],
            "nzcv": rng.getrandbits(4),
            "d": [rng.getrandbits(64) for _ in range(32)],
            "pc": ins.address,
        }
        st["x"][31] = SCRATCH + 0x38000
        entries.append((len(entries), ins, body, st))

    print(f"[diff] cases: {len(entries)} executable, {len(unmapped)} unmapped/skip")
    unm = {}
    for m, ops, why in unmapped:
        unm.setdefault(m, why)
    if unm:
        print(f"[diff] unmapped mnemonics: {sorted(unm)}")

    # ---------------- C harness ----------------
    L = []
    L.append('#include "a64_ctx.h"')
    L.append("#include <stdio.h>")
    L.append("#include <string.h>")
    L.append("#include <windows.h>")
    L.append("static A64Ctx g_c;")
    for n, (idx, ins, body, st) in enumerate(entries):
        L.append(f"static void case_fn_{idx}(A64Ctx* c) {{")
        for line in body.splitlines():
            L.append("    " + line)
        L.append("}")
    L.append("int main(void) {")
    L.append(f"    if (!VirtualAlloc((void*){SCRATCH:#x}, {SCRATCH_SIZE:#x},"
             " MEM_RESERVE|MEM_COMMIT, PAGE_READWRITE)) return 3;")
    L.append("    for (int idx = 0; idx < 100000; idx++) {")
    L.append("        A64Ctx* c = &g_c;")
    L.append("        memset(c, 0, sizeof(*c));")
    L.append("        int fault = 0;")
    L.append("        __try {")
    L.append("        switch (idx) {")
    for n, (idx, ins, body, st) in enumerate(entries):
        L.append(f"        case {idx}:")
        for r in range(32):
            L.append(f"            c->x[{r}] = {st['x'][r]:#x}ull;")
        nz = st["nzcv"]
        L.append(f"            c->n = {(nz >> 3) & 1}; c->z = {(nz >> 2) & 1}; "
                 f"c->c = {(nz >> 1) & 1}; c->v = {nz & 1};")
        for r in range(32):
            L.append(f"            c->d[{r}] = {st['d'][r]:#x}ull;")
            L.append(f"            c->dhi[{r}] = 0ull;")
        L.append(f"            c->pc = {st['pc']:#x}ull;")
        L.append(f"            case_fn_{idx}(c);")
        L.append("            break;")
    L.append("        default: goto done;")
    L.append("        }")
    L.append("        } __except(1) { fault = 1; }")
    L.append('        printf("CASE %d", idx);')
    L.append("        for (int r = 0; r < 32; r++) "
             'printf(" %016llx", (unsigned long long)g_c.x[r]);')
    L.append('        printf(" %d%d%d%d", g_c.n, g_c.z, g_c.c, g_c.v);')
    L.append('        printf(" %016llx", (unsigned long long)g_c.pc);')
    L.append("        for (int r = 0; r < 32; r++) "
             'printf(" %016llx", (unsigned long long)g_c.d[r]);')
    L.append('        printf(" %d\\n", fault);')
    L.append("    }")
    L.append("done:")
    L.append("    return 0;")
    L.append("}")

    gen_path = os.path.join(OUT, "diff_gen.c")
    with open(gen_path, "w") as f:
        f.write("/* generated by diff_test.py */\n" + "\n".join(L) + "\n")

    vc = (r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools"
          r"\VC\Auxiliary\Build\vcvars64.bat")
    bat = os.path.join(OUT, "_vcvars.bat")
    with open(bat, "w") as f:
        f.write('@echo off\r\ncall "' + vc + '" >nul 2>&1\r\nset\r\n')
    env_out = subprocess.run(["cmd", "/c", bat], capture_output=True,
                             text=True, cwd=OUT)
    env = dict(line.partition("=")[::2] for line in env_out.stdout.splitlines()
               if "=" in line and not line.startswith("["))
    if "PATH" not in env:
        print(env_out.stdout[:1000], env_out.stderr[:1000])
        raise SystemExit("vcvars64 failed")
    exe = os.path.join(OUT, "diff.exe")
    def find_tool(name):
        for d in env["PATH"].split(";"):
            d = d.strip().strip('"')
            if d and os.path.exists(os.path.join(d, name)):
                return os.path.join(d, name)
        raise SystemExit(name + " not found on vcvars PATH")

    objs = []
    for src in (gen_path,
                os.path.join(ROOT, "src", "runtime", "a64_helpers.c")):
        obj = os.path.join(OUT, os.path.splitext(os.path.basename(src))[0] + ".obj")
        r = subprocess.run([find_tool("cl.exe"), "/nologo", "/c", "/O2", "/MD", "/bigobj",
                            "/I" + os.path.join(ROOT, "src", "runtime"),
                            src, "/Fo" + obj],
                           capture_output=True, text=True, env=env, cwd=OUT)
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit(f"compile failed: {src}")
        objs.append(obj)
    r = subprocess.run([find_tool("cl.exe"), "/nologo", *objs, "/Fe" + exe,
                        "/link", "/SUBSYSTEM:CONSOLE"],
                       capture_output=True, text=True, env=env, cwd=OUT)
    if r.returncode != 0:
        print(r.stdout[-3000:], r.stderr[-3000:])
        raise SystemExit("link failed")

    run = subprocess.run([exe], capture_output=True, text=True, timeout=300)
    c_out = {}
    for line in run.stdout.splitlines():
        if not line.startswith("CASE "):
            continue
        parts = line.split()
        c_out[int(parts[1])] = parts[2:]

    # ---------------- unicorn golden ----------------
    img = bytes(nso.image)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    pages = (len(img) + 0xFFF) & ~0xFFF
    uc.mem_map(IMG_BASE, pages if pages else 0x1000)
    uc.mem_write(IMG_BASE, img)
    uc.mem_map(SCRATCH, SCRATCH_SIZE)

    NZCV_BITS = [(1 << 31), (1 << 30), (1 << 29), (1 << 28)]  # n z c v

    def set_nzcv(uc, nzcv):
        val = 0
        if nzcv & 8:
            val |= NZCV_BITS[0]
        if nzcv & 4:
            val |= NZCV_BITS[1]
        if nzcv & 2:
            val |= NZCV_BITS[2]
        if nzcv & 1:
            val |= NZCV_BITS[3]
        uc.reg_write(UC_ARM64_REG_NZCV, val)

    mismatches = []
    checked = 0
    for idx, ins, body, st in entries:
        if idx not in c_out:
            mismatches.append((idx, ins.mnemonic, ins.op_str, "no C output"))
            continue
        for r in range(32):
            uc.reg_write(XR[r], st["x"][r])
        set_nzcv(uc, st["nzcv"])
        for r in range(32):
            uc.reg_write(DR[r], st["d"][r])
        uc.reg_write(UC_ARM64_REG_PC, st["pc"])
        ufault = 0
        try:
            uc.emu_start(st["pc"], st["pc"] + 4, count=1)
        except Exception:  # noqa: BLE001
            ufault = 1
        golden = []
        for r in range(32):
            golden.append("%016x" % uc.reg_read(XR[r]))
        nz = uc.reg_read(UC_ARM64_REG_NZCV)
        gn = 1 if nz & (1 << 31) else 0
        gz = 1 if nz & (1 << 30) else 0
        gc = 1 if nz & (1 << 29) else 0
        gv = 1 if nz & (1 << 28) else 0
        golden.append(f"{gn}{gz}{gc}{gv}")
        golden.append("%016x" % uc.reg_read(UC_ARM64_REG_PC))
        for r in range(32):
            golden.append("%016x" % uc.reg_read(DR[r]))
        unicorn_fault = ufault
        golden.append(str(unicorn_fault))
        got = c_out[idx]
        checked += 1
        if got != golden:
            diffs = [f"x{r}:c={got[r]} g={golden[r]}"
                     for r in range(32) if got[r] != golden[r]]
            if got[32] != golden[32]:
                diffs.append(f"nzcv:c={got[32]} g={golden[32]}")
            if got[33] != golden[33]:
                diffs.append(f"pc:c={got[33]} g={golden[33]}")
            if got[66] != golden[66]:
                diffs.append(f"fault:c={got[66]} g={golden[66]}")
            for r in range(32):
                if got[34 + r] != golden[34 + r]:
                    diffs.append(f"d{r}:c={got[34 + r]} g={golden[34 + r]}")
            mismatches.append((idx, f"{ins.mnemonic} {ins.op_str}".strip(),
                                f"pc={ins.address:#x}", "; ".join(diffs[:6])))

    print(f"[diff] checked {checked} cases")
    print(f"[diff] mismatches: {len(mismatches)}")
    for m in mismatches[:40]:
        print("  ", m)
    rep = os.path.join(OUT, "diff_report.txt")
    with open(rep, "w") as f:
        f.write(f"checked {checked}\nmismatches {len(mismatches)}\n")
        for m in mismatches:
            f.write(repr(m) + "\n")
    print(f"[diff] report: {rep}")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
