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
| Latest run | 2026-11-04 01:07 PC time (osinit entry) — `boot_20261104_010739.log`, 9,310 lines, exit 0, no retry loops; stop at `QueryMountRomCacheSize` (see below) |
| Current blocker | DFC file-system proxy never created (`0xf2883b8` = 0), so `QueryMountRomCacheSize` aborts in the fs create path before any fsp-srv IPC |
| Build state | Executable linked 2026-11-04 00:42, 706,793,984 B (binary itself is never committed); `a64_helpers.c` updated to 55,324 B in this push |
| Next step | Populate the DFC proxy during fs init, then serve romfs from real bytes, then a title / plaza draw + CSV in the same run |

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

### 2026-11-04 01:16 PC time — IPC reply framing fixed, DFC proxy is the romfs blocker
- IPC fixes, in `a64_helpers.c` (now 55,324 B, in this push): reply-packet duplication removed (it wrote a second SFCO header into the client's output), inert code patches and the `svc #0x7e` hook removed, descriptor-free CMIF replies seal as type 1, domain IDs only for appletOE opens and fsp-srv cmd 0 (`ConvertSessionToDomain`), fsp-srv cmd 3 (`QueryPointerBufferSize`) returns `u16 0x1000`, no invented payloads. Every reply is verified by the guest's own `ParseHeader`: 6 replies parse as type 01, 5 requests as type 00.
- Verified state: build exit 0, osinit run exit 0 (`boot_20261104_010739.log`, 9,310 lines, `[done] exit`), full trace run 303,154 lines / 147,946 entries. No applet retry loop, no fsp retry loop.
- Blocker diagnosis changed: `QueryMountRomCacheSize` does NOT fail on an IPC reply — no `sm:/fsp-srv` traffic is attempted in the create path. It dies inside the fs state machine before IPC, between the Once check and `InitializeHipcServiceResolution` / `ConnectToHipcService` (neither appears in the trace after the create path starts).
- Precise cause: the DFC file-system proxy is never created. `InitializeDfcFileSystemProxyServiceObject` (0xe7c02b0) is never called (0 occurrences in the full trace), so its slot `0xf2883b8` stays 0. `GetFileSystemProxyServiceObject` consults only that slot, clears the live proxy, enters the create path, and asserts. A live proxy at `0xf288410` exists, but it is not the slot the mount-cache path reads.
- Next step: find the caller of `InitializeDfcFileSystemProxyServiceObject` (via GOT thunk resolution, as with the other thunks) and satisfy what it needs, so `0xf2883b8` is populated, `QueryMountRomCacheSize` reaches its real fsp-srv call, and romfs can be served from real bytes.
- Still missing for Gate 2 beyond romfs: the NVN→DX12 layer and the Maxwell→DXIL translation — neither exists in `src/` yet.
- Full analysis: `evidence/GATE1_BOOT_PROGRESS.md` (358 lines, build-machine copy, clean of builder names).
- Source note: `main.c` on the build machine is still withheld for the same builder-name log string as before; `a64_helpers.c` in this push is clean of it.

## Contents

- `evidence/run_2026-11-03_gate2_progress.md` — the 16:27 run that cleared BREAK `e79bb18`
- `evidence/run_2026-11-04_gate2_progress.md` — runs from 22:17 through the loop-gone 00:41 run
- `evidence/GATE1_BOOT_PROGRESS.md` — boot progress, IPC reply rules, and the DFC proxy diagnosis (01:16 PC)

No game files, no keys, no firmware, no titlekeys, no NSO bytes, and no decrypted ExeFS are in this folder. No exe, obj, or generated C is committed. This folder is documentation and evidence only; the runtime source lives in `docs/native-gate1/` until Gate 2 work produces its own source snapshot.
