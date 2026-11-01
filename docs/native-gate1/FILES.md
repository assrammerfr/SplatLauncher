# SplatNative Gate 1 — What's In This Folder, and What's Built

Snapshot as of the 2026-10-31 17:13 run. Sizes are from the PC, verified
from disk at push time. Generated outputs are listed so you can tell
source from build artifact — generated outputs are NOT in git.

## Source — committed
- `src/recomp/a64.py` (69,757 B) — A64 to C static recompiler.
- `src/runtime/main.c` (8,381 B) — host runtime entry, ThreadManager
  sentinel seed, crash logging, fn table, init dispatch.
- `src/runtime/nso_loader.c` (11,952 B) — NSO loader, owns REIr application.
- `src/runtime/a64_ctx.h` (12,118 B), `src/runtime/a64_helpers.c` (8,628 B).
- `src/runtime/vendor/lz4.c` + `lz4.h` — third-party LZ4, NOT committed.

## Tools — committed
- `tools/nso_scan.py` (16,510 B) — Gate 1 module scan.
- `tools/gen.py` (26,426 B) — generator, jump-table + cross-range seeding.
- `tools/build.py` (5,204 B), `tools/build.bat` (610 B), `tools/fixup_gen.py` (2,421 B).
- `tools/diff_test.py` (11,929 B) — differential test, 240/240 scalar.
- `tools/jumptab_proto.py` (6,864 B) — Piece A prototype jump-table decoder.
- `tools/scan_crossers.py` (4,326 B) — cross-range branch-target scan.
- `tools/diag_ecccc.py` (3,965 B), `tools/diag_branch.py` (3,063 B),
  `tools/diag_e8aa.py` (3,409 B) — diagnostic tools for ecccc48 / e8aa4b4.
- `tools/repair_main_shards.py` (2,780 B) — repair main shards deleted by race.

## Evidence — committed
- `GATE1_MODULE_SCAN.md` (6,959 B), `evidence/gate1_module_scan.txt`
  (30,941 B), `evidence/gate1_scan.json` (14,459 B),
  `evidence/gate1_nvn_surface.json` (20,283 B).
- `evidence/diff_report.txt` — checked 240, mismatches 0.
- `evidence/gen_report.json` (8,047 B) — latest generator report.
- `evidence/relr_fix_2026-10-31.md` — REIr fix, 543,741 slots.
- `evidence/e840144_crash_2026-10-31.md` — e840144 NULL-head analysis.
- `evidence/piece_a_ecccc48_2026-10-31.md` — Piece A measure, 15:09 run.
- `evidence/piece_a_ecccc48_fix_2026-10-31.md` — ecccc48 fix, g_fn_count 411,370.
- `evidence/e8aa4b4_run_2026-10-31.md` — 17:13 run, init 17, STOP e8aa4b4.
- `evidence/e8aa4b4_fix_1830_run_2026-10-31.md` — 18:30 run, init 438,
  STOP 90763a0. This is the latest run.
  See that file for the latest run, not the README snapshot alone.

## Generated — built on the PC, NOT committed
- `build/gen/recomp_meta.c` (28,839,782 B at 17:46, then regenerated for
  the 18:30 run) — fn table, 438,383 functions at 18:30.
- `build/gen/*_gen_*.c` — main / sdk / rtld shards, ~17-31 MB per TU,
  76 sources total at 17:13.
- `build/obj/*.obj` — 76/76 objs at 17:13, ~498 MB at the 16:54 check,
  larger after the final shards.
- `build/splatnative.exe` — 406,729,216 B at 18:30 (was 403,837,440 B at
  17:13, 396,281,344 B at 15:09, 397,064,192 B at 13:02). Generated, NOT committed.
- `boot.log` — 73,548 B at 18:30, fn table 438,383, init 438, STOP 90763a0.
  Summarised in evidence, not committed raw.
- `build/boot.log` — STALE, 40,590 B at 07:33, fn table 314,166, STOP
  5ec1ac. Do NOT use this file to judge a new run.
- Crossers JSONs (`tools/crossers_main.json` 396,423 B,
  `tools/crossers_sdk.json` 88,063 B, `tools/crossers_rtld.json` 2 B) —
  generated scan data, NOT committed; counts are in evidence notes.

## What is NOT in this folder
No game files, no keys, no firmware, no titlekeys, no NSO bytes, no
decrypted ExeFS, no Nintendo art, no circumvention wording, no anticheat /
online work, no binary release.
