#!/usr/bin/env python3
"""Print real capstone operand forms for the emitter-gap mnemonics whose
operand shape decides the implementation (cmlt, stg, sqincd, ldr vector/
literal, fmov/dup element naming, widen/MAC forms)."""
import sys, os, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import gen  # noqa: E402
import capstone  # noqa: E402

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
md.detail = True

WANT = ["cmlt", "sqincd", "stg", "ldr", "str", "fmov", "dup", "mvn",
        "scvtf", "fcvtzs", "fcvtzu", "ucvtf", "addv", "umaxv", "ushll",
        "sshll", "shll", "ushll2", "sshll2", "shll2", "smull", "umull",
        "smull2", "umull2", "smlal", "umlal", "uaddw", "saddw", "faddp",
        "fmaxp", "fminp", "bif", "bit", "bsl", "tbl", "ins", "umov", "smov",
        "ldaxp", "ldxp", "mvni", "shrn", "sqshrn", "sqrshrn", "xtn",
        "fmaxnmv", "fminnmv", "fmla", "fmls", "ext", "zip1", "uzp1",
        "saddlp", "addp", "cnt", "rev64", "clz"]
hits = {k: [] for k in WANT}
counts = collections.Counter()

for name in ("sdk", "main"):
    mod = gen.load_module(name)
    base = mod["base"]
    text = bytes(mod["nso"].mem[mod["nso"].segs[0]["mem_off"]])
    n = len(text)
    off = 0
    while off + 4 <= n:
        end = min(off + 8192, n)
        got = list(md.disasm(text[off:end], base + off))
        if not got:
            off += 4
            continue
        for i in got:
            if i.mnemonic in hits:
                counts[i.mnemonic] += 1
                lst = hits[i.mnemonic]
                if len(lst) < 3:
                    lst.append((name, hex(i.address), i.op_str))
            else:
                # still record unknown-gap suspects by prefix rules
                if i.mnemonic.startswith(("sqinc", "stg", "st2g", "stzg", "ldg")):
                    counts[i.mnemonic] += 1
                    lst = hits.setdefault(i.mnemonic, [])
                    if len(lst) < 3:
                        lst.append((name, hex(i.address), i.op_str))
        off += 4 * len(got)
    print(f"[{name}] scanned {n} bytes", flush=True)

print("\n=== counts ===")
for mn, c in counts.most_common():
    print(f"{mn:10s} {c}")
print("\n=== sample forms ===")
for mn, lst in hits.items():
    if lst:
        for tag, a, s in lst:
            print(f"{mn:10s} [{tag}] {a}: {s}")
