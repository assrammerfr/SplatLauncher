#!/usr/bin/env python3
"""Diagnose the new [unmapped] pc=e8aa4b4 stop: how fn_e8aa480 transfers to
e8aa4b4, whether it's a direct branch target (should have been a crosser), a
computed BR table, a BLR/vtable target, or a range-end handoff."""
import sys, os, json, bisect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa: E402
import capstone  # noqa: E402

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
md.detail = True

ABS = 0xe8aa4b4
ABS_PREV = 0xe8aa480
ABS_TRC = [0xe8aa590, 0xe8aa480]

mod = gen.load_module("sdk")
base = mod["base"]
t = mod["nso"].segs[0]
text = bytes(mod["nso"].mem[t["mem_off"]])
IMG = ABS - base
IMGP = ABS_PREV - base

manifest = {int(k) for k in json.load(open(os.path.join(gen.BUILD, "sdk_manifest.json")))}
slist = sorted(manifest)

print(f"target {ABS:#x} img {IMG:#x} in_manifest={IMG in manifest}")
print(f"prev   {ABS_PREV:#x} img {IMGP:#x} in_manifest={IMGP in manifest}")

# neighbors around target
idx = bisect.bisect_left(slist, IMG)
print("starts around target:", [hex(base + s) for s in slist[max(0, idx - 3):idx + 3]])
print("e8aa4b4 img 0x1ac4b4 in crossers sdk:",
      IMG in set(json.load(open(os.path.join(HERE, "crossers_sdk.json")))))

# symbols
for s in mod["syms"]:
    if s.get("name") and s.get("shndx"):
        if 0x1a0000 <= (s["value"] - 0) <= 0x1b4000 and base + s["value"] in range(ABS - 0x8000, ABS + 0x8000):
            print("sym", hex(base + s["value"]), s["name"])

# range of the fn containing target
i0 = bisect.bisect_right(slist, IMG) - 1
st = slist[i0]
nxt = slist[i0 + 1] if i0 + 1 < len(slist) else None
print(f"containing fn start={base + st:#x} next start={hex(base + nxt) if nxt else None}")

# disasm prev fn region
def dis(a, b, tag):
    print(f"--- {tag} {a:#x}..{b:#x} ---")
    for i in md.disasm(text[a - base: b - base], a):
        m = "   <<<" if i.address == ABS else ""
        print(f"  {i.address:08x}: {i.mnemonic:8s} {i.op_str}{m}")

dis(ABS_PREV - 0x40, ABS + 0x40, "around prev/target")

# scan sdk text for ANY branch (direct) targeting IMG
direct_hits = []
off = 0
textb = text
while off + 4 <= len(textb):
    end = min(off + 4096, len(textb))
    got = list(md.disasm(textb[off:end], base + off))
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
            direct_hits.append((i.address, i.mnemonic + " " + i.op_str))
    off += 4 * len(got)
print("direct branches to target:", [(hex(a), s) for a, s in direct_hits])

# any 64-bit data word equal to ABS or IMG inside sdk (vtable/table pointers)?
import struct
count_abs = count_img = 0
first = []
for o in range(0, len(mod["nso"].image) - 8, 8):
    w = struct.unpack_from("<Q", mod["nso"].image, o)[0]
    if w == ABS:
        count_abs += 1
        if len(first) < 5: first.append(("abs", o))
    elif w == IMG:
        count_img += 1
        if len(first) < 5: first.append(("img", o))
print(f"data words == abs: {count_abs}, == img: {count_img}, first: {first}")
