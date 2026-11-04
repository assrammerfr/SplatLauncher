# Gate 2 progress — 2026-11-04: appletOE reply loop fixed, back to BREAK e79bb18

Gate 2 is still NOT passed. No draw, no CSV in any run below.
Gate 1 (nnMain + GetService) was already passed on 2026-11-03 and is
repeated in the loop runs below; it is not re-claimed here.

## Runs since the 2026-11-03 push (eaba545)

- 2026-11-03 22:17 PC, `boot_20261103_222201.log` (9,649 B):
  crash `c0000005` at guest_pc `e847628`, svc_count 20, no nnMain,
  no GetService.
- 2026-11-03 22:24 PC, `boot_20261103_222403.log` (13,270 B):
  ends at BREAK `e79bb18`, svc_count 21, no nnMain/GetService lines.
- 2026-11-03 22:25 PC, `boot_20261103_222517.log` (72,588,577 B):
  nnMain count 0, GetService count 0 (full-trace-sized log, no pass).
- 2026-11-04 00:27 PC, `boot_20261104_002731.log` (490,644,576 B):
  nnMain reached, GetService x3 (fsp-srv, apm, appletOE), then a
  runaway appletOE svc-reply loop — no done, no crash line.
- 2026-11-04 00:32 PC, `boot_20261104_003245.log` (400,681 B):
  nnMain reached, GetService 0, crash `c0000005` at guest_pc
  `e8513f8`, svc_count 26 — a new crash point, not a Gate 1 repeat.
- 2026-11-04 00:33 PC, `boot_20261104_003354.log` (513,220,878 B):
  nnMain reached, GetService x3, same appletOE loop, no done,
  no crash line.

## Latest run — loop gone (verified from disk)

- Run: `boot_20261104_004123.log`, 432,049 B, 2026-11-04 00:41 PC.
- `[boot] calling entry nnMain at c934940`
- `[svc] GetService('fsp-srv') via 'sm:' -> h=1001` (only GetService
  in this run; zero appletOE/apm lines)
- Ends at `[svc] BREAK x0=0 x1=20ffeccc x2=4 x3=0 x4=20ffeccc
  x30=e79bb18`, then `[done] pc=dead0000dead0000 svc_count=31
  undef=0`, `[done] exit`
- Zero draw, CSV, or romfs markers in this run.

Reading: the appletOE reply loop is fixed — the run terminates
cleanly instead of ballooning to a ~500 MB log — but this run is
back at the old post-GetService BREAK `e79bb18`, and does not
reach apm/appletOE, romfs, a draw, or a CSV. That BREAK is the
current Gate 2 blocker again.

## Build state at this run

- Executable: 706,793,984 B, linked 2026-11-04 00:42 PC
  (binary itself is never committed; see `FILES.md`).
- `main.c` on the build machine: 25,442 B, but it is NOT in this
  push: the build-machine copy still contains a builder name in a
  log string, and builder names are not committed to this public
  repo. Runtime source snapshots here therefore remain the older
  copies; only the README status and this evidence file are new.
- No exe, obj, or generated C is committed.
