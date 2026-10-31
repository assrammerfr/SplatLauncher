"""Repair: regenerate the main_gen shard files a racing gen run deleted (000..009).

The manifest written by the authoritative gen run IS functions_of() output
({start: end} for all 307,941 functions, sorted by start). emit_module writes
SHARD(6000) functions per file in sorted order, so shards 000..009 hold exactly
the first 60,000 manifest keys. We re-emit that slice with gen.py's own
emit_module -> byte-identical output, touching nothing in the intact 010..051.

Sanity-checked: the first start of shard 010 must already be on disk, and the
last start of shard 009 must not, before we write anything.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))
import gen  # noqa: E402

N_MISSING = 10
PER = gen.SHARD  # 6000
MAIN_BASE = gen.LOAD_BASE["main"]  # 0x08506000


def defined_starts():
    """Absolute fn addresses defined across the existing main shards."""
    pat = re.compile(r"^void fn_([0-9a-f]+)\(A64Ctx\* c\) \{", re.M)
    out = set()
    for fn in sorted(os.listdir(gen.BUILD)):
        if not re.fullmatch(r"main_gen_\d+\.c", fn):
            continue
        with open(os.path.join(gen.BUILD, fn)) as f:
            for m in pat.finditer(f.read()):
                out.add(int(m.group(1), 16))
    return out


def main():
    mpath = os.path.join(gen.BUILD, "main_manifest.json")
    man = {int(k): int(v) for k, v in json.load(open(mpath)).items()}
    starts = sorted(man)
    print(f"[repair] manifest={len(starts)} starts (range {starts[0]:#x}..{starts[-1]:#x})")

    present_abs = defined_starts()
    present_keys = {a - MAIN_BASE for a in present_abs}
    missing = [s for s in starts if s not in present_keys]
    print(f"[repair] on disk={len(present_keys & set(starts))} missing={len(missing)}")

    if len(missing) != N_MISSING * PER:
        print(f"[repair] expected {N_MISSING * PER} missing, found {len(missing)}; aborting")
        sys.exit(1)
    if missing != starts[:N_MISSING * PER]:
        print("[repair] missing set is not the first shard prefix; aborting")
        sys.exit(1)
    # boundary check: last start of shard 009 absent, first of shard 010 present
    if starts[N_MISSING * PER] not in present_keys or starts[N_MISSING * PER - 1] in present_keys:
        print("[repair] shard boundary mismatch; aborting")
        sys.exit(1)

    mod = gen.load_module("main")
    sub = [(s, man[s]) for s in missing]
    print(f"[repair] re-emitting {len(sub)} functions as main_gen_000..{N_MISSING-1:03d}")
    r = gen.emit_module(mod, sub, "main_gen")
    for p, size in r["written"]:
        print(f"  {p} ({size} B)")
    print("[repair] done")


if __name__ == "__main__":
    main()
