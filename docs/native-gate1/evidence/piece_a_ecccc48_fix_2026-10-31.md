# SplatNative Gate 1 — Piece A ecccc48 fix, 2026-10-31 16:00-16:54 CST
Builder: Freebuff. Muse: verification and git only.
Gate 1 NOT passed. No new run yet at push time — this is a diagnosis + fix snapshot, not a run result.

## Diagnosis (verified from disk)
- Previous run (15:09): STOP at [unmapped] pc=ecccc48, init 4, FAILED rc=-1, svc_count=0, no nnMain.
- Cause: bne at ecccc24 crosses the ecccc1c split to non-start ecccc48 — a goto->dispatch regression.
  Entry #4's path hit a branch target that was not a function start, and the emitter used a direct goto instead of dispatching through the fn table, so it fell through to an unmapped pc. This explains init 17 -> 4: a split changed entry #4's path, not a fn-table loss.
- Stale-file trap, already resolved: build/boot.log (07:33, fn table 314,166, STOP 5ec1ac) is NOT the 15:09 run. Root boot.log (15:09, fn table 360,827, STOP ecccc48) is the real run. ec7d294 IS in meta (decl + entry), ecccc48 was absent from meta at 15:09.

## Fix
- Fixpoint scan of cross-range branch targets, verify ecccc48 included, integrate into gen.py.
- New tools in this push: scan_crossers.py, diag_ecccc.py, diag_branch.py.
- Crossers sets: crossers_main.json 396,423 B (16:04), crossers_sdk.json 88,063 B (16:00), crossers_rtld.json 2 B (16:54 listing, essentially empty) — JSONs themselves are generated data and are NOT committed here; counts are recorded in this note and in gen_report.json.
- Meta after fixpoint scan: g_fn_count = 411,370 (was 360,827, +50,543), all three crossers sets loaded, recomp_meta.c 27,133,461 B at 16:18.
- gen.py: 24,199 -> 24,851 B (+652 B, 16:00).

## Build in progress at push time
- 76 sources total, 62 compiled, 5 in flight, 9 queued, remaining ~15 shards, obj count 67 files / 498,403,438 B.
- Pathological TUs: main_gen_001 at 75 min, still CPU-active; twin main_gen_002 finished at 16:51 after 70 min. Slow big TUs, not a deadlock — all 6 cl.exe CPU-active, pool cycling.
- Freebuff estimate at 16:54: link ~30-60 min out, then re-run.
- Boot.log and exe are still the 15:09 run (5,858 B / 396,281,344 B) until that re-run lands.

## Next
Rebuild + re-run, measure init count + unmapped stops vs the 15:09 baseline (init 4, STOP ecccc48).
Gate 1 pass bar unchanged: nnMain + GetService enumeration log.
