#!/usr/bin/env python3
"""Step 1: disassemble the ecccbf0 / ecccbf8 / ecccc1c regions and classify
the ecccc1c -> ecccc48 branch (direct b vs computed br).
Step 2: capstone scan of all direct branch targets (b/bl/b.cond/cb/tb) per
module, reporting:
  - not_starts: targets that are not function starts today
  - cross:       targets whose range differs from the branch source's range
                 (the minimal augmentation set that fixes split-crossing
                  dispatch stops; every other branch still resolves by goto)
"""
import sys, os, json, bisect
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa: E402
import capstone  # noqa: E402

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
md.detail = True

BR_MNEM = {"b", "bl", "cbz", "cbnz", "tbz", "tbnz"}


def scan_module(args):
    name, = args
    m = gen.load_module(name)
    t = m["nso"].segs[0]
    tlo, thi = t["mem_off"], t["mem_off"] + t["size"]
    textb = bytes(m["nso"].mem[t["mem_off"]])
    starts = {int(k) for k in
              json.load(open(os.path.join(gen.BUILD, f"{name}_manifest.json")))}
    slist = sorted(starts)

    def rng(off):
        i = bisect.bisect_right(slist, off) - 1
        return slist[i] if i >= 0 else -1

    all_new, cross = set(), set()
    for i in md.disasm(textb, m["base"] + tlo):
        if i.mnemonic not in BR_MNEM:
            continue
        o = i.operands[0] if i.operands else None
        if o is None or o.type != capstone.arm64.ARM64_OP_IMM:
            continue
        tgt = o.imm - m["base"]
        if not (tlo <= tgt < thi - 4 and (tgt & 3) == 0):
            continue
        if tgt in starts:
            continue
        all_new.add(tgt)
        src = i.address - m["base"]
        if rng(tgt) != rng(src):
            cross.add(tgt)
    return name, len(all_new), cross


def step1(mod):
    nso = mod["nso"]; base = mod["base"]; t = nso.segs[0]
    text = bytes(nso.mem[t["mem_off"]])
    def dis_img(ia, ib, tag=""):
        print(f"--- {tag} img {ia:#x}..{ib:#x} (abs {base + ia:#x}) ---")
        for i in md.disasm(text[ia:ib], base + ia):
            print(f"  {i.address:08x}: {i.mnemonic:8s} {i.op_str}")
    dis_img(0x5cebf0, 0x5cec60, "ecccbf0..ecccc60")
    dis_img(0x14b7f0, 0x14b840, "e8497f0..e849840")


if __name__ == "__main__":
    step1(gen.load_module("sdk"))
    print("\n=== direct-branch target augmentation ===")
    grand_new = grand_cross = 0
    with ProcessPoolExecutor(max_workers=3) as pool:
        for name, n_new, cross in pool.map(scan_module,
                                            [("rtld",), ("sdk",), ("main",)]):
            print(f"{name}: not_starts={n_new}  cross_range={len(cross)}")
            grand_new += n_new
            grand_cross += len(cross)
    print(f"TOTAL all-targets augmentation: {grand_new}")
    print(f"TOTAL minimal (cross-range) augmentation: {grand_cross}")
