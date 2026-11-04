# SplatNative Gate 2

Gate 2 is the title / plaza draw: a Splatoon 3 title or plaza scene drawn
through the NVN to DX12 runtime boundary, with a CSV produced from that
same draw run. Gate 1 (nnMain + GetService) passed on 2026-11-03 and is
documented in `docs/native-gate1/`; Gate 2 progress lives here, not there.

## Pass bar

Gate 2 passes only when, in the same run:

- a title / plaza scene is drawn through NVN to DX12, AND
- a CSV is produced from that draw run.

Service calls, a cleared BREAK, or a crash at a new address are progress,
not a pass. No draw + no CSV = Gate 2 not passed.

## Current status

| Item | State |
| --- | --- |
| Gate 2 | **Not passed** — no draw, no CSV in any run yet |
| Latest run | 2026-11-04 00:41 PC time — `nnMain` at `c934940`, `GetService('fsp-srv') -> h=1001` only, ends at BREAK `e79bb18`, svc_count 31, clean exit |
| Current blocker | BREAK `e79bb18` (post-GetService), back after the appletOE reply loop was fixed |
| Build state | Executable linked 2026-11-04 00:42, 706,793,984 B (binary itself is never committed) |
| Next step | Get past BREAK `e79bb18`, then a title / plaza draw + CSV in the same run |

## Progress log

### 2026-11-03 16:27 PC time — BREAK cleared, new crash e8d070c
- Run `boot_20261103_162739.log`, 428,872 B, 8,383 lines, exe 465,902,592 B. Init 6,861, nnMain at `c934940`, GetService fsp-srv -> h=1001, apm -> h=1002, appletOE -> h=1003, svc_count 25, undef 0.
- Old BREAK `e79bb18`: 0 occurrences — cleared.
- New stop: crash `c0000005`, guest_pc `e8d070c`, sp 20ffedc0.
- Full evidence: `evidence/run_2026-11-03_gate2_progress.md`.

### 2026-11-03 evening — BREAK back, early crashes
- 18:33 run: svc_count 40, ends at BREAK `e79bb18` again, no crash.
- 19:03 run: crash `e84790c`, svc_count 14, no nnMain, no GetService.
- 22:17 run: crash `e847628`, svc_count 20, no nnMain, no GetService.
- 22:24 run: BREAK `e79bb18`, svc_count 21, no nnMain/GetService lines.

### 2026-11-04 00:27–00:33 PC time — Gate 1 repeated, appletOE reply loop
- 00:27 run (490,644,576 B log): nnMain + GetService x3 (fsp-srv, apm, appletOE), then a runaway appletOE svc-reply loop — no done, no crash.
- 00:32 run (400,681 B): nnMain, GetService 0, crash `e8513f8`, svc_count 26.
- 00:33 run (513,220,878 B log): nnMain + GetService x3, same appletOE loop.
- Full evidence: `evidence/run_2026-11-04_gate2_progress.md`.

### 2026-11-04 00:41 PC time — loop fixed, back to BREAK e79bb18
- Run `boot_20261104_004123.log`, 432,049 B. nnMain at `c934940`, GetService fsp-srv -> h=1001 only, zero appletOE/apm lines, ends at BREAK `e79bb18`, then `[done] svc_count=31`, exit.
- Reading: the loop is fixed — this run terminates cleanly instead of ballooning to ~500 MB — but it does not reach apm/appletOE, romfs, a draw, or a CSV.
- Source on the build machine at this run: `main.c` 25,442 B, but it is NOT in this push: the build-machine copy still contains a builder name in a log string, and builder names are not committed to this public repo. Runtime source snapshots therefore remain the older copies in `docs/native-gate1/`; only status and evidence are new here.

## Contents

- `evidence/run_2026-11-03_gate2_progress.md` — the 16:27 run that cleared BREAK `e79bb18`
- `evidence/run_2026-11-04_gate2_progress.md` — runs from 22:17 through the loop-gone 00:41 run

No game files, no keys, no firmware, no titlekeys, no NSO bytes, and no decrypted ExeFS are in this folder. No exe, obj, or generated C is committed. This folder is documentation and evidence only; the runtime source lives in `docs/native-gate1/` until Gate 2 work produces its own source snapshot.
