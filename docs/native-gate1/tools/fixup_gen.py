#!/usr/bin/env python3
"""Post-generation fixups for shards written by an older in-flight a64.py:
1. MSVC has no __int128: replace the 128-bit carry expression.
2. crc32 mnemonic must be a C string literal in a64_crc32 calls.
Runs over build/gen/*.c after gen.py finishes (idempotent)."""
import glob
import os
import re

BUILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "gen")

fixes = [
    (re.compile(r"\(\(unsigned __int128\)__a \+ __b\) >> 64"),
     "(__a + __b < __a)"),
    (re.compile(r"a64_crc32\((crc32[a-z0-9]*),"),
     r'a64_crc32("\1",'),
]

total = 0
for path in sorted(glob.glob(os.path.join(BUILD, "*.c"))):
    with open(path, "r") as f:
        src = f.read()
    n = 0
    for pat, rep in fixes:
        src, k = pat.subn(rep, src)
        n += k
    if n:
        with open(path, "w") as f:
            f.write(src)
        total += n
        print(f"{os.path.basename(path)}: {n} fixups")
print(f"total fixups: {total}")
