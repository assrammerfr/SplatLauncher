# SplatNative Gate 1 — Piece A measure, 2026-10-31 15:09 CST
Builder: the builder. Muse: verification and git only.
Gate 1 NOT passed. This is a progress snapshot, not a pass.

## Build / run
- Pipeline: BUILD_EXIT=0, RUN_EXIT=0, task LastResult=0, zero corrupt objs
- New exe: 396,281,344 B at 2026-10-31 15:09 (was 397,064,192 B at 13:02)
- New boot.log: 5,858 B at 2026-10-31 15:09 (was 41,494 B at 13:02, shorter because it stopped earlier)

## Piece A measure vs baseline (13:02 run)
- Fn table: 314,166 -> 360,827 functions (+46,661)
- Previous STOP at [unmapped] pc=ec7d294: GONE
- New STOP, same unmapped class: [unmapped] pc=ecccc48 not in fn table -> STOP (svc_count=0)
  - x30=ecccc1c, trace ends ... e849818, e84981c, ecccc1c, ecccc48
- Init calls: 17 -> 4 (ecb0cd0, e821ab0, e825a40, eceaee0), then STOP at ecccc48
- ThreadManager sentinel seed (e840144 fix) still present in main.c and in the run log
- Still failing: [init] FAILED rc=-1 pc=dead0000dead0000, [done] svc_count=0 undef=1
- No nnMain, no GetService log. Gate 1 pass bar unchanged: nnMain + GetService enumeration log.

## Files in this snapshot
- tools/gen.py: 17,963 -> 24,199 B (jump-table / BR / BLR scan_jump_tables, 2026-10-31 13:41)
- src/runtime/main.c: 7,480 -> 8,381 B (ThreadManager sentinel seed, 2026-10-31 12:58)
- tools/build.py: 4,721 -> 5,204 B (2026-10-31 14:32)
- evidence/gen_report.json: updated, 1,627 B (2026-10-31 14:33)

## Next
Next blocker is ecccc48, same unmapped computed-branch class as ec7d294.
Seed that class too, rebuild, re-run, measure again. Do not claim Gate 1 passed until nnMain + GetService are verified from a real boot.log.
