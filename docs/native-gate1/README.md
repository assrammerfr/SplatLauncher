# SplatNative Gate 1

SplatNative is a title-specific static recompilation of Splatoon 3 (A64 to C to x86-64), with an NVN to DX12 runtime boundary and Horizon services handled at the loader. This folder holds its Gate 1 source, tools, and run evidence. It is work in progress.

## Current status

| Item | State |
| --- | --- |
| Gate 1 pass bar | Recompiled rtld, main, and SDK run to `nnMain` with a `GetService` enumeration log |
| Gate 1 | **Passed 2026-11-03 14:26 PC time** — `nnMain` + `GetService('fsp-srv')` in the same run log |
| Latest run (Gate 1) | 2026-11-03 14:26 — see the Gate 1 pass update below. Gate 2 runs live in `docs/native-gate2/`, not here |
| Current work | Gate 1 is done. Gate 2 work continues in `docs/native-gate2/` |
| Build state | Gate 1 pass build: executable linked 2026-11-03 14:26, 463,009,792 B (binary itself is never committed; see `FILES.md`). Current Gate 2 build state is in `docs/native-gate2/` |
| Next step | Gate 2: see `docs/native-gate2/` for the current blocker and pass bar |

## Contents

- `GATE1_MODULE_SCAN.md` — module scan: NSOs and NPDM, exact Splatoon 3 NVN surface and service ACL
- `evidence/` — module scan data, generator reports, and per-run evidence notes
- `tools/` — generator, build, scan, and diagnostic tools
- `src/recomp/a64.py` — A64 to C static recompiler
- `src/runtime/` — host runtime: `main.c`, `nso_loader.c`, `a64_ctx.h`, `a64_helpers.c`
- `BUILD.md` — how the runtime is built and verified
- `FILES.md` — what is source, what is generated, and what is not in this folder

No game files, no keys, no firmware, no titlekeys, no NSO bytes, and no decrypted ExeFS are in this folder. Generated build outputs are not committed; see `FILES.md`.

## Progress log

## Update 2026-10-30 23:20 CST
- Runtime skeleton started: src/runtime/main.c, nso_loader.c, a64_ctx.h, a64_helpers.c.
- Generator updated: a64.py 59,444 B, gen.py 10,693 B, gen_report.json from the generator run.
- NOT uploaded: build/gen/*.c generated outputs (~57 MB total, build artifacts, not source), vendor/lz4.c + lz4.h (third-party LZ4, dependency only), pycache.
- Gate 1 is still NOT passed — skeleton started, first runnable unit to nnMain not yet reported.

## Update 2026-10-30 23:31 CST — emitter fix
- Recompiler updated: a64.py 62,404 B, a64_ctx.h 11,844 B.
- New build tools: tools/build.py, tools/fixup_gen.py, tools/build.bat.
- Generator re-ran at 23:31, sdk_gen_*.c outputs grew (e.g. sdk_gen_000.c to ~19 MB) — generated outputs NOT uploaded, source only.
- Gate 1 still NOT passed — first runnable unit to nnMain not yet reported.

## Update 2026-10-31 00:12 CST — differential test passes
- Recompiler updated: a64.py 65,131 B (csel/ccmp/madd/movk fixes, plus bfi/mneg/msub/extr + extend family).
- New: tools/diff_test.py (11,929 B), evidence/diff_report.txt: checked 240, mismatches 0.
- Full 300k-function regeneration with the corrected emitter was running at 00:13; generated outputs NOT uploaded.
- Gate 1 to-dos now 3/6 — recompiler with differential test checked off. First runnable unit to nnMain still pending.

## Update 2026-10-31 11:18 CST — REIr loader fix
- Generator bug found: gen.py read the REIr table with DT_RELRENT (0x25, 8-byte entry size) instead of DT_RELSZ (0x23, table size), treating entries as word indices instead of byte offsets. Net: ~one entry per module applied, ~26,162 patches where ~570,000 were needed.
- Fix: gen.py report-only, loader owns REIr application (nso_loader.c). 543,741 slots rebased (sdk 15,013 / main 528,689 / rtld 39), 0 skipped. Old pc=5ec1ac unmapped crash gone.
- Updated: a64.py 69,757 B, gen.py 17,963 B, nso_loader.c 11,952 B, a64_ctx.h 12,118 B, a64_helpers.c 8,628 B, main.c 7,375 B, build.py 4,721 B. Evidence: evidence/relr_fix_2026-10-31.md.
- Gate 1 still NOT passed — first runnable unit to nnMain not yet reported.

## Update 2026-10-31 13:02 CST — e840144 fixed, new blocker ec7d294
- Crash at guest_pc=e840144, fault=0x178: ThreadManager singleton @ 0xf290108, BSS-zeroed, ctor e840214 never ran, guard head != sentinel (this+0x1d0) not NULL. Faulting store str xzr, [x11, #0x80] at e840164, x11 = 0 + 31*8 = 0xf8, fault 0xf8 + 0x80 = 0x178.
- Fix: sentinel seeded in main.c (7,480 -> 8,381 B, head == sentinel == this+0x1d0), guest code untouched. Evidence: evidence/e840144_crash_2026-10-31.md.
- New run boot.log 41,494 B: init 7 -> 17 entries, no crash, graceful STOP at [unmapped] pc=ec7d294 not in fn table, [init] FAILED rc=-1, svc_count=0, undef=1.
- ec7d294 is a jump-table coverage gap, not a code bug: ec7d270 computed-jump dispatcher (BR x10 at ec7d290), target reached only by computed branch, never BL, so static discovery never emitted it.
- Gate 1 still NOT passed — nnMain + GetService log still owed.

## Update 2026-10-31 15:09 CST — Piece A jump-table seeding
- gen.py extended to seed recompilation at computed-branch (BR/BLR) jump-table targets, not just BL targets and eh_frame FDEs. gen.py 17,963 -> 24,199 B, build.py 4,721 -> 5,204 B.
- Pipeline: BUILD_EXIT=0, RUN_EXIT=0, zero corrupt objs. New exe 396,281,344 B, new boot.log 5,858 B.
- Measure: fn table 314,166 -> 360,827 (+46,661: 34,320 main + 12,341 sdk). ec7d294 STOP gone, ec7d294 present in meta (decl + entry).
- New STOP, same class: [unmapped] pc=ecccc48 not in fn table, x30=ecccc1c, init 4 (ecb0cd0, e821ab0, e825a40, eceaee0), [init] FAILED rc=-1, svc_count=0, undef=1.
- Evidence: evidence/piece_a_ecccc48_2026-10-31.md. Generated outputs NOT uploaded.
- Gate 1 still NOT passed — nnMain + GetService log still owed.

## Update 2026-10-31 16:54 CST — ecccc48 cross-range fix, build in progress
- Diagnosis: bne at ecccc24 crosses the ecccc1c split to non-start ecccc48 — a goto->dispatch regression. Entry #4's path hit a branch target that was not a function start, and the emitter used a direct goto instead of dispatching through the fn table. This explains init 17 -> 4: a split changed entry #4's path, not a fn-table loss.
- Stale-file trap resolved: build/boot.log (07:33, fn table 314,166, STOP 5ec1ac) is NOT the 15:09 run. Root boot.log (15:09, fn table 360,827, STOP ecccc48) is the real run.
- Fix: fixpoint scan of cross-range branch targets, verify ecccc48 included, integrate into gen.py (24,199 -> 24,851 B). New tools: scan_crossers.py (4,326 B), diag_ecccc.py (3,965 B), diag_branch.py (3,063 B).
- Meta after fixpoint scan: g_fn_count = 411,370 (was 360,827, +50,543), all three crossers sets loaded, recomp_meta.c 27,133,461 B at 16:18. Crossers JSONs and recomp_meta.c are generated data, NOT uploaded; counts recorded in evidence/piece_a_ecccc48_fix_2026-10-31.md and gen_report.json (7,949 B).
- Build in progress at this update: 76 sources, 62 compiled, 5 in flight, 9 queued, obj 67 files. Pathological TUs main_gen_001 at 75 min, still CPU-active; twin main_gen_002 finished at 16:51 after 70 min. Link ~30-60 min out, then re-run.
- Gate 1 still NOT passed — boot.log / exe are still the 15:09 run until that re-run lands. nnMain + GetService log still owed.

## Update 2026-10-31 17:13 CST — ecccc48 fixed, new blocker e8aa4b4
- Pipeline: BUILD_EXIT=0, RUN_EXIT=0, 76/76 objs. New exe 403,837,440 B, new boot.log 41,424 B.
- Measure: fn table 411,370, init 4 -> 17 again (last e879620, same as 13:02). ec7d294 STOP gone, ecccc48 STOP gone — fixpoint cross-range scan worked, init regression fixed.
- New STOP, later in init: [unmapped] pc=e8aa4b4 not in fn table, x30=e8aa59c, [init] FAILED rc=-1, svc_count=0, undef=1.
- Diagnosis in progress: tools/diag_e8aa.py (3,409 B) — disasm + direct-branch scan for e8aa4b4.
- New in git with this update: BUILD.md (how it is built), FILES.md (what is source vs generated), tools/diag_e8aa.py, tools/jumptab_proto.py, tools/repair_main_shards.py, evidence/e8aa4b4_run_2026-10-31.md.
- Gate 1 still NOT passed — nnMain + GetService log still owed. See BUILD.md and FILES.md for how to build and what is in this folder.

## Update 2026-10-31 18:30 CST — e8aa4b4 fixed, new blocker 90763a0
- Pipeline: new exe 406,729,216 B, new boot.log 73,548 B, 3,108 lines.
- Measure: fn table 411,370 -> 438,383 (+27,013), init 17 -> 438. ec7d294, ecccc48, and e8aa4b4 STOPs all gone.
- New STOP, much later: [undef] pc=90763a0 word=0 mn=dup -> STOP, then [unmapped] pc=90763a0 not in fn table, [init] FAILED rc=-1, svc_count=0, undef=2.
- gen.py 24,851 -> 26,426 B (17:31), gen_report.json 7,949 -> 8,047 B (17:46). Evidence: evidence/e8aa4b4_fix_1830_run_2026-10-31.md. Generated recomp_meta.c (28,839,782 B) NOT uploaded.
- This run met the the builder V4 build watch stop condition (new STOP with init past 17). Watch reported once and is done; removal is the project owner's call.
- Gate 1 still NOT passed — nnMain + GetService log still owed.

## Update 2026-10-31 23:39 CST — emitter coverage work, no new run yet
- Blocker class after 18:30: emitter instruction coverage, not missing-function discovery. STOP 90763a0 is `dup v0.2s, w9` (NEON SIMD) — the emitter hit an unsupported mnemonic, emitted `a64_undef`, and mid-function resume dispatched to nothing. Quantified from the 18:30 run: ~75 SIMD/FP mnemonics, 14,917 sites (fsub x3,860, fmul x2,778, dup x1,850 total sites), init 438 of 6,858 (6.4%).
- the builder is now editing the emitter itself: `src/recomp/a64.py` 69,757 B -> 81,494 B at 21:39, then 82,107 B at 23:39 (+12,350 B total). This push snapshots the 23:39 a64.py; it was still being edited at push time, so a later edit may supersede it before the next run.
- New tools in git with this update: `tools/diag_9076.py` (2,520 B, 18:39) — 90763a0 diagnosis; `tools/diag_forms.py` (2,446 B, 20:20); `tools/census_forms.py` (4,503 B, 21:45) — census of SIMD/FP forms/sites.
- No new run: gen.py unchanged 26,426 B (17:31), boot.log unchanged 73,548 B (18:30), exe unchanged 406,729,216 B (18:30), init 438, STOP still 90763a0, svc_count 0, no nnMain, no GetService.
- Gate 1 still NOT passed — nnMain + GetService log still owed. Next step to watch for is a regen + rebuild + re-run from the new a64.py.

## Update 2026-11-01 09:11 CST — C2440 SIMD header fix, no new run yet
- After the 01:33 run (init 438, STOP 90763a0 dup), a later build failed with C2440 compile errors and left no exe — the 09:03 watch check found `build/splatnative.exe` absent. That failure is what this update fixes, not a new run.
- the builder mapped 5 defect classes to exact emitter lines, verified them against the header (VTERN/VRED macro definitions, bitwise header block, bitwise emission region 2604-2680, every `v_eor / v_and / v_orn` site). Analysis: `src_esz * 2` is strictly correct for the mla fix (no narrowing works with `* 1`, header defines no equal-width pairs).
- Applied: two a64.py emitter fixes, plus the missing header definitions the emitter calls but a64_ctx.h never defined — integer `mla / mls`, lane-suffixed `umaxv`, and 2-arg `uaddlv`.
- Sizes in git with this update: `src/recomp/a64.py` 82,107 B -> 135,621 B (09:05), `src/runtime/a64_ctx.h` 12,118 B -> 84,698 B (09:11). SIMD gap census reported by the builder: classes cleared 1,168 -> 90 total.
- Still no new run: boot.log still 53,609 B (01:33 run, init 438, STOP 90763a0), gen.py still 26,426 B, exe still absent at last check. Evidence: evidence/c2440_simd_header_fix_2026-11-01.md.
- Gate 1 still NOT passed — nnMain + GetService log still owed. Next step is a full regen + build + run that clears C2440 and gets init past 438, with no [unmapped] on jump-table targets.

## Update 2026-11-01 14:35 CST — SIMD emitter work continues, no new run yet
- The 09:11 view was incomplete: a full MSVC census after that fix found 4 translation units ok, 56 failing, ~2,291 errors, ~9 distinct root causes — not just the 5 defect classes mapped at 09:11. Current work is fixing those 9 build-blocker classes: fcvtzs/scvff signed suffix, faddp/fmaxp/fminp naming, fmul broadcast, shsub widths, frecpe/frsqrte/frint, sli/sri arguments, fabd floating-point, and the `__fm` redeclaration.
- Source growth since 09:11, verified from disk on the PC: `src/recomp/a64.py` 135,621 B -> 136,486 B (12:00), `src/runtime/a64_ctx.h` 84,698 B -> 89,777 B (12:27). Sizes in git with this update match the PC at the 14:35 check.
- Still no new run: boot.log still 53,609 B (01:33 run, init 438, STOP 90763a0), gen.py still 26,426 B, exe still absent at the 14:35 check. PC CPU was at 98-100% at 14:10-14:25 with the builder processes dominant, consistent with an in-progress regeneration/build, but no linked executable exists yet to judge.
- Evidence: evidence/simd_header_growth_2026-11-01.md.
- Gate 1 still NOT passed — nnMain + GetService log still owed. Next step is a full regen + build + run that clears the 9 classes and gets init past 438, with no [unmapped] on jump-table targets.

## Update 2026-11-01 19:44 CST — the builder pipeline run 18:43, source growth, no new run yet
- the builder ran again at 18:43 PC time (pipeline_full.log 57 B, a64.py / a64_ctx.h timestamps 18:43:22-23). Source growth verified from disk on the PC and copied to git here: `src/recomp/a64.py` 136,486 B -> 136,812 B, `src/runtime/a64_ctx.h` 89,777 B -> 90,806 B.
- Census5 (14:11, evidence/census5_2026-11-01.log): 9 TUs, main_gen_012.c / 013.c / 014.c / 015.c FAIL — a subset census, not the full 56-TU census, so it does not replace the 4-ok / 56-failing full census from earlier today.
- Still no new run: boot.log still 53,609 B (01:33 run, init 438, STOP 90763a0 [undef] mn=dup, no nnMain, no GetService), exe still absent. No build/gen/*.c, obj, exe, vendor, or __pycache__ files are in this push — source and evidence only.
- Gate 1 still NOT passed — nnMain + GetService log still owed. Next step is still a full regen + build + run that clears the 9 classes and gets init past 438.

## Update 2026-11-01 21:25 CST — Exe built and run, init 5,640, new pc=0 stop
- Exe built: `build/splatnative.exe` 460,216,832 B, 2026-11-01 19:53 PC time — first linked exe after the 09:03 C2440 failure where it was absent. The exe binary itself is NOT in this push; only its build process is documented in evidence/exe_build_process_2026-11-01.md.
- Run: boot.log 324,251 B, 10,983 lines, fn table 438,297, init 438 -> 5,640 calls, then STOP at [unmapped] pc=0 after [init] call bee4480. Old blocker 90763a0 gone, no [crash]. Evidence: evidence/pipeline_full_2026-11-01.log (10,410 B, 19:53).
- New diagnostic tool: tools/_decode_zero.py (2,425 B, 20:04) — parses the trace ring from boot.log to identify the pc=0 blocker.
- Crash theory in progress: pc=0 and x30=0, sp=20fff000; 85061e0 is a real function (24-byte slot, atomic increment, -1 legitimate return), tail-branch dispatch without x30 set is the leading alternative to mid-frame overwrite, with x30=0 propagating through prologue/epilogue. Queued next test for the builder, after the builder is done: log x30/sp at every [init] entry, dump 32 qwords from sp-0x100 to 20fff000 on [unmapped]/[undef] stop, timestamped log file, plus GOT/PLT zero-slot list and 85061e0..8506280 disassembly check.
- Gate 1 still NOT passed — nnMain + GetService log still owed.

## Update 2026-11-01 23:33 CST — init 6,493, first [crash], caa1968
- Run: boot.log 404,791 B, 13,265 lines, exe 460,255,744 B (2026-11-01 22:22 PC time). Init 5,640 -> 6,493 calls, no FAILED line, then [crash] code=c0000005 fault=0x78 guest_pc=caa1968 sp=20ffef10, svc_count=0, undef=0, followed by a 1,024-entry trace ring.
- Previous blockers gone: pc=0 (0 occurrences), 90763a0 (0), ecb0cd4 (0). No [unmapped], no [undef], no nnMain, no GetService. This was the first [crash] terminator in any watched boot.log.
- Evidence: evidence/runs_2026-11-01_22_to_2026-11-02_02.md.
- Gate 1 still NOT passed — nnMain + GetService log still owed.

## Update 2026-11-02 01:03 CST — first nnMain entry, crash eb068f4
- Run: boot.log 431,983 B, 14,117 lines, exe 462,999,552 B (2026-11-01 23:51 PC time). Init 6,493 -> 6,858 (full), last init d390540, then line 13072 `[boot] calling entry nnMain at c934940` — the first nnMain entry in any watched boot.log.
- Immediately after nnMain: [crash] code=c0000005 fault=0x0 guest_pc=eb068f4 sp=20ffee60, svc_count=0, undef=0, followed by a 1,024-entry trace ring. Previous crash caa1968 gone (0), pc=0 (0), 90763a0 (0).
- No GetService (0), no [unmapped] (0), no STOP (0). Gate 1 requires nnMain AND a GetService log, so this is progress, not a pass.
- Evidence: evidence/runs_2026-11-01_22_to_2026-11-02_02.md.
- Gate 1 still NOT passed — GetService log still owed.

## Update 2026-11-02 02:03 CST — nnMain again, new crash ca3e5ec
- Run: boot.log 432,132 B, 14,119 lines, exe 463,000,576 B (2026-11-02 01:02 PC time). Init 6,858 (full, same count), then line 13074 `[boot] calling entry nnMain at c934940`, then [crash] code=c0000005 fault=0x68 guest_pc=ca3e5ec sp=20ffedc0 x30=ca31300, svc_count=0, undef=0, followed by a 1,024-entry trace ring.
- Previous crash eb068f4 gone (0), caa1968 (0), pc=0 (0), 90763a0 (0). No GetService (0), no [unmapped] (0), no STOP (0), [crash] count 20.
- Watch note: `src/runtime/main.c` on the PC grew 8,381 B -> 10,847 B for this run, and `src/recomp/a64.py` on the PC was reported at 139,519 B (2026-11-01 22:49). Neither file could be copied into this repo for this push — remote copy to the PC was unavailable from 02:33 CST onward (transport failure, not a build failure) — so the repo source snapshots still show the 2026-11-01 19:44 sizes, and only the run numbers above are new here. No exe, obj, or generated C is committed.
- Current work, 2026-11-02 morning (builder chat, not disk-verified): re-investigating ca3e5ec with scan 1 state retention plus the field-0 filter, after scan 2's state-clearing was judged too aggressive.
- Gate 1 still NOT passed — nnMain reached, GetService log still owed.

## Update 2026-11-02 08:17 CST — nnMain again, same crash ca3e5ec, source sync
- Run: boot.log 166,734 B, 7,951 lines, exe 463,000,576 B (2026-11-02 08:17 PC time, exe size unchanged from 01:02). Init 6,858 (full, same count), nnMain reached, then [crash] code=c0000005 fault=0x68 guest_pc=ca3e5ec, svc_count=0, no GetService, no [unmapped]. Same crash address as 01:02, smaller boot.log — a re-run, not a new crash.
- Source sync, verified from disk on the PC: `src/recomp/a64.py` 136,812 B -> 139,519 B (2026-11-01 22:49 PC time), now in git here. `src/runtime/main.c` on the PC is 10,919 B (2026-11-02 08:10), but it is NOT in this push: the PC copy contains a builder name in a log string, and builder names are not committed to this public repo. That file stays private until the log string is scrubbed to a neutral term.
- No exe, obj, or generated C is committed.
- Gate 1 still NOT passed — nnMain reached, GetService log still owed.

## Update 2026-11-03 14:26 PC time — Gate 1 PASSED
- Run: `boot_20261103_142644.log`, 403,031 B, 8,171 lines, exe 463,009,792 B (2026-11-03 14:26 PC time). Init 6,858 (full), `[boot] calling entry nnMain at c934940`, `[svc] GetService('fsp-srv') via 'sm:' -> h=1001`, svc_count 11 (histogram #00d:1, #00f:1, #010:1, #01f:1, #021:5, #026:1, #029:1), crashes 0, undef 0, `svcCreateThread (#8)` 0.
- Gate 1 pass bar is met: `nnMain` and a guest-driven `GetService` enumeration line are in the same run log. Full evidence: `evidence/run_2026-11-03_gate1_pass.md`.
- The run still ends at `[svc] BREAK x0=0 x1=20ffed98 x2=4 x3=0 x4=20ffed98 x30=e79bb18`, then `[done] pc=dead0000dead0000 svc_count=11`. That post-GetService BREAK does not undo the pass; it is the first Gate 2 blocker.
- Source sync: `src/recomp/a64.py` unchanged since the 940c88a push (139,519 B, hash verified against the PC copy). `src/runtime/main.c` on the PC is now 15,377 B (2026-11-03 07:58), but it is NOT in this push: the PC copy still contains a builder name in a log string (line 152), and builder names are not committed to this public repo. That file stays private until the log string is scrubbed to a neutral term. Runtime source snapshots in this repo therefore remain the older copies; only the README status and the Gate 1 pass evidence are new here.
- No exe, obj, or generated C is committed.
- Gate 1 PASSED. Next gate: title / plaza draw + CSV.

## Gate 2

Gate 2 progress moved to `docs/native-gate2/` on 2026-11-04. Gate 2 updates are no longer recorded in this folder; this folder ends at the Gate 1 pass.
