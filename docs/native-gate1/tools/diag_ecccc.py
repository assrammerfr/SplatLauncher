#!/usr/bin/env python3
"""Diagnose the [unmapped] pc=ecccc48 stop: where ecccc1c/ecccc48 came from
in discovery (FDE / BL / jump-table / PLT), which start lies between them,
the FDE range covering them, symbol names, and a disassembly window."""
import sys, os, struct, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa: E402
import capstone  # noqa: E402

mod = gen.load_module("sdk")
nso = mod["nso"]
base = mod["base"]
t = nso.segs[0]
text = bytes(nso.mem[t["mem_off"]])

# --- discovery sets (image-relative) ---
fde_starts = {s for s, e in mod["fdes"]}
fde_ranges = sorted((s, e) for s, e in mod["fdes"])
bl = gen.scan_bl(mod)
jt = gen.scan_jump_tables(mod)

manifest = {int(k): int(v) for k, v in
            json.load(open(os.path.join(gen.BUILD, "sdk_manifest.json"))).items()}

# PLT stubs: reuse gen.scan_plt needs patch_map; reconstruct minimal patch map
# from dyn relocations the same way gen.main does (only JUMP_SLOT matters here).
plt_stubs = set()
try:
    d = dict(mod["dyn"])
    # fall back: PLT stubs are in extra; approximate via manifest - fde - bl - jt
except Exception:
    pass

def img(a):   # abs -> image-relative
    return a - base

def abs_(i):  # image-relative -> abs
    return base + i

targets = [0xecccc1c, 0xecccc48, 0xecccbf0, 0xecccbf8, 0xe849818, 0xe84981c,
           0xeceaee0, 0xec7d294, 0xe849814, 0xe849790]
print("addr      img       in_manifest in_FDE in_BL in_JT")
for a in targets:
    i = img(a)
    print(f"{a:08x} {i:08x}   {str(i in manifest):5s}  "
          f"{str(i in fde_starts):5s} {str(i in bl):5s} {str(i in jt):5s}")

# starts strictly between ecccc1c and ecccc48 (img 0x5cec1c, 0x5cec48)
lo, hi = img(0xecccc1c), img(0xecccc48)
between = sorted(s for s in manifest if lo < s <= hi)
print("\nstarts in (ecccc1c, ecccc48]:", [hex(abs_(s)) for s in between[:20]])
# manifest start right after ecccc1c (its range end)
after = sorted(s for s in manifest if s > lo)
print("next starts after ecccc1c:", [hex(abs_(s)) for s in after[:6]])
print("ecccc1c manifest range end:", hex(abs_(manifest[lo])) if lo in manifest else None)

# FDE covering ecccc1c and ecccc48
cov = [(hex(abs_(s)), hex(abs_(e))) for s, e in fde_ranges if s <= lo < e]
cov2 = [(hex(abs_(s)), hex(abs_(e))) for s, e in fde_ranges if s <= hi < e]
print("FDE covering ecccc1c:", cov)
print("FDE covering ecccc48:", cov2)

# --- symbols near the region ---
best = []
for s in mod["syms"]:
    if s.get("name") and s.get("shndx"):
        v = s["value"]
        if lo - 0x400 <= v <= hi + 0x400:
            best.append((hex(abs_(v)), s["name"]))
print("\nsymbols near eccc region:", best[:20])

# names for the other probe addresses
name_by_val = {}
for s in mod["syms"]:
    if s.get("name") and s.get("shndx"):
        name_by_val.setdefault(s["value"], s["name"])
for a in targets:
    print(f"  {a:08x} sym={name_by_val.get(img(a), '-')}")

# --- disassembly window abs 0xecccb00 .. 0xecccd40 ---
md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
w_lo, w_hi = 0xecccb00, 0xecccd40
off = img(w_lo)
print(f"\ndisasm {w_lo:#x}..{w_hi:#x}:")
for i in md.disasm(text[off: img(w_hi)], w_lo):
    mark = ""
    if i.address == 0xecccc1c: mark = "   <-- fn start (trace)"
    if i.address == 0xecccc48: mark = "   <-- UNMAPPED target"
    if i.address == 0xecccbf0 or i.address == 0xecccbf8: mark = "   <-- trace"
    print(f"  {i.address:08x}: {i.mnemonic:8s} {i.op_str}{mark}")

# also: is there an unconditional b / br / blr in the fn_ecccc1c range?
end_c = manifest.get(lo, lo + 0x100)
print(f"\nfn_ecccc1c range: {lo:#x}..{end_c:#x} (abs {abs_(lo):#x}..{abs_(end_c):#x})")
for i in md.disasm(text[lo - t["mem_off"]: end_c - t["mem_off"]], abs_(lo)):
    if i.mnemonic in ("b", "br", "blr", "ret") or i.mnemonic.startswith("b."):
        print(f"  {i.address:08x}: {i.mnemonic:8s} {i.op_str}")
