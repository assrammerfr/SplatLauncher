#!/usr/bin/env python3
"""Fixpoint scan: find every direct-branch target that would dispatch to a
non-start address because a function range boundary (e.g. a jump-table seed)
splits source and target into different ranges. Those targets must become
function starts. Iterates because adding a start splits ranges further.

Outputs tools/crossers_<mod>.json (image-relative ints) and reports counts,
iterations, and spot checks (ecccc48 must be in sdk's set)."""
import sys, os, json, bisect, array

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa: E402
import capstone  # noqa: E402

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
md.detail = True


def scan_pairs(mod):
    """One capstone pass -> parallel array('I') of (src, tgt), image-relative,
    for every direct branch (b, b.cond, bl, cbz/cbnz, tbz/tbnz) whose target
    lies in .text, 4-aligned. Windows the input and resyncs 4 bytes past a
    decode stop: capstone's disasm() halts entirely at the first invalid word
    (MOD0 header, literal pools), and A64 is fixed 4-byte aligned."""
    t = mod["nso"].segs[0]
    tlo, thi = t["mem_off"], t["mem_off"] + t["size"]
    textb = bytes(mod["nso"].mem[t["mem_off"]])
    base = mod["base"]
    srcs = array.array("I"); tgts = array.array("I")
    n = 0
    off = 0
    W = 4096
    while off + 4 <= len(textb):
        end = min(off + W, len(textb))
        got = list(md.disasm(textb[off:end], base + tlo + off))
        if not got:
            off += 4
            continue
        for i in got:
            m = i.mnemonic
            if m in ("b", "bl") or m.startswith("b."):
                op = 0
            elif m in ("cbz", "cbnz", "tbz", "tbnz"):
                op = -1
            else:
                continue
            o = i.operands[op] if i.operands else None
            if o is None or o.type != capstone.arm64.ARM64_OP_IMM:
                continue
            tgt = o.imm - base
            if not (tlo <= tgt < thi - 4 and (tgt & 3) == 0):
                continue
            srcs.append(i.address - base)
            tgts.append(tgt)
            n += 1
        off += 4 * len(got)
    return srcs, tgts, n


def fixpoint(mod, pairs, max_iter=48, verbose=True):
    srcs, tgts, n = pairs
    added = set()
    for it in range(max_iter):
        mod["extra_starts"] = mod["_extra_base"] | added
        fns = gen.functions_of(mod)
        slist = [s for s, e in fns]
        ends = [e for s, e in fns]

        def rng(x):
            i = bisect.bisect_right(slist, x) - 1
            if i < 0 or x >= ends[i]:
                return -1
            return slist[i]

        known = set(slist)
        cross = set()
        for k in range(n):
            s = srcs[k]; g = tgts[k]
            if g in known:
                continue
            if rng(g) != rng(s):
                cross.add(g)
        new = cross - added
        if verbose:
            print(f"[cross] iter {it + 1}: fns={len(fns)} new_crossers={len(new)}")
        if not new:
            return added, len(fns)
        added |= new
    print("[cross] WARNING: hit max_iter without convergence")
    return added, len(fns)


if __name__ == "__main__":
    SPOT = {"sdk": [0x5cec48]}   # ecccc48
    names = sys.argv[1:] or ["rtld", "sdk", "main"]
    for name in names:
        mod = gen.load_module(name)
        mod["_extra_base"] = set()   # measured against FDE-only ranges? NO:
        # cross must be measured against the FULL current start set (manifest).
        manifest = {int(k) for k in
                    json.load(open(os.path.join(gen.BUILD, f"{name}_manifest.json")))}
        mod["_extra_base"] = manifest - {s for s, e in mod["fdes"]}
        print(f"[cross] {name}: scanning branches ...")
        pairs = scan_pairs(mod)
        print(f"[cross] {name}: {pairs[2]} branch pairs")
        added, nfns = fixpoint(mod, pairs)
        out = os.path.join(HERE, f"crossers_{name}.json")
        with open(out, "w") as f:
            json.dump(sorted(added), f)
        spot = SPOT.get(name, [])
        for s in spot:
            print(f"[cross] spot {s:#x} in set: {s in added}")
        print(f"[cross] {name}: FINAL crossers={len(added)} fns={nfns} -> {out}")
