#!/usr/bin/env python3
"""Classify the new stop pc=90763a0 (main module): branch target not seeded,
unsupported-instruction gap site, or missed computed-branch table.
Init entry 9076390 ran, then dispatched 90763a0 (+0x10)."""
import sys, os, json, bisect, struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa: E402
import capstone  # noqa: E402

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
md.detail = True

ABS = 0x90763a0
PREV = 0x9076390

mod = gen.load_module("main")
base = mod["base"]
t = mod["nso"].segs[0]
text = bytes(mod["nso"].mem[t["mem_off"]])
IMG = ABS - base
IMGP = PREV - base

manifest = {int(k) for k in json.load(open(os.path.join(gen.BUILD, "main_manifest.json")))}
slist = sorted(manifest)
print(f"target {ABS:#x} img {IMG:#x} in_manifest={IMG in manifest}")
print(f"prev   {PREV:#x} img {IMGP:#x} in_manifest={IMGP in manifest}")
idx = bisect.bisect_left(slist, IMG)
print("starts around:", [hex(base + s) for s in slist[max(0, idx - 3):idx + 3]])

# crossers?
cx = set(json.load(open(os.path.join(HERE, "crossers_main.json"))))
print("in stale crossers json:", IMG in cx)

# gen_report gap list — is 90763a0 an emitter-gap site?
rep = json.load(open(os.path.join(gen.BUILD, "gen_report.json")))
print("main gap mnemonics sample:", rep["modules"]["main"]["gap_mnemonics"][:20])
print("main gaps:", rep["modules"]["main"]["gap_count"])

def dis(a, b, tag):
    print(f"--- {tag} {a:#x}..{b:#x} ---")
    for i in md.disasm(text[a - base: b - base], a):
        m = "   <<< TARGET" if i.address == ABS else ""
        print(f"  {i.address:08x}: {i.mnemonic:8s} {i.op_str}{m}")

dis(PREV - 0x10, ABS + 0x40, "around prev/target")

# direct branches to target in main?
direct = []
off = 0
while off + 4 <= len(text):
    end = min(off + 4096, len(text))
    got = list(md.disasm(text[off:end], base + off))
    if not got:
        off += 4
        continue
    for i in got:
        if i.mnemonic in ("b", "bl") or i.mnemonic.startswith("b."):
            op = 0
        elif i.mnemonic in ("cbz", "cbnz", "tbz", "tbnz"):
            op = -1
        else:
            continue
        o = i.operands[op] if i.operands else None
        if o and o.type == capstone.arm64.ARM64_OP_IMM and o.imm == ABS:
            direct.append((hex(i.address), i.mnemonic + " " + i.op_str))
    off += 4 * len(got)
print("direct branches to target:", direct[:10])
