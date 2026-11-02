#!/usr/bin/env python3
"""Identify the pc=0 blocker: decode the trace-ring functions + parse the ring."""
import os, re, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nso_scan
import capstone

EXTRACT = r"E:\S3_extract"
LOAD_BASE = {"rtld": 0x08500000, "main": 0x08506000, "sdk": 0x0E6FE000}
md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

# ---- 1. parse the trace ring from boot.log ----
log = open(r"E:\SplatNative\boot.log", encoding="utf-8", errors="replace").read().splitlines()
ring = []
on = False
for ln in log:
    if ln.startswith("[trace] last"):
        on = True
        continue
    if on:
        m = re.match(r"\[trace\]\s+([0-9a-f]+)", ln)
        if m:
            ring.append(int(m.group(1), 16))
        else:
            break
print(f"ring entries: {len(ring)}")
cnt = collections.Counter(ring)
print("unique:", len(cnt))
for v, c in cnt.most_common(20):
    print(f"  {v:#x} x{c}")
print("last 24:", " ".join(f"{v:#x}" for v in ring[-24:]))
# cycle detection: find the repeating pattern in last 200
tail = ring[-120:]
print("tail seq (first 60 of last 120):", " ".join(f"{v:#x}" for v in tail[:60]))

# ---- 2. decode target functions ----
TARGETS = [
    ("main", 0xbee4480, 48),   # last init entry
    ("main", 0x85061e0, 40),   # ring: near main base
    ("main", 0x8506250, 40),
    ("main", 0x9a76060, 40),   # ring hot
    ("main", 0x9a761e0, 40),
    ("sdk",  0xed49ed0, 24),   # ring oldest
    ("sdk",  0xe84790c, 24),
]
cache = {}
for mod in {t[0] for t in TARGETS}:
    nso = nso_scan.Nso(os.path.join(EXTRACT, mod))
    t0 = nso.segs[0]
    cache[mod] = (nso, t0, bytes(nso.mem[t0["mem_off"]]))
    print(f"[{mod}] segs: " + ", ".join(
        f"{s.get('kind', '?')}: mem_off={s['mem_off']:#x} size={s['size']:#x}" for s in nso.segs))

for mod, abs_addr, n in TARGETS:
    nso, t0, text = cache[mod]
    off = abs_addr - LOAD_BASE[mod]
    idx = off - t0["mem_off"]
    print(f"\n=== {mod} {abs_addr:#x} (module off {off:#x}, idx {idx:#x}) ===")
    if idx < 0 or idx >= len(text):
        print("  OUT OF RANGE")
        continue
    shown = 0
    for insn in md.disasm(text[idx:idx + n * 4], abs_addr):
        print(f"  {insn.address:#x}: {insn.mnemonic} {insn.op_str}")
        shown += 1
        if shown >= n:
            break
    if not shown:
        print("  no decode (bytes:", text[idx:idx + 16].hex(), ")")
