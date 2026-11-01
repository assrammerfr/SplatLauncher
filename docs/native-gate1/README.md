# SplatNative Gate 1 — work in progress snapshot, 2026-10-30 23:05 CST

Freebuff Gate 1 work, snapshot only — the generator was still being edited when this was uploaded (a64.py 58,770 B at 23:05:33 on the PC).

- `GATE1_MODULE_SCAN.md` — module scan: NSOs + NPDM, exact Splatoon 3 NVN surface and service ACL.
- `evidence/` — gate1_module_scan.txt, gate1_scan.json, gate1_nvn_surface.json.
- `tools/` — nso_scan.py, gen.py (generator tool).
- `src/recomp/a64.py` — A64 to C static recompiler, work in progress.

Gate 1 is NOT passed yet. Next steps in Freebuff's plan: host runtime skeleton, first runnable unit to nnMain with GetService log, then Gate 1 report per format.

No game files, no keys, no firmware in this folder. No launcher code changed.

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
- This run met the Freebuff V4 build watch stop condition (new STOP with init past 17). Watch reported once and is done; removal is Seth's call.
- Gate 1 still NOT passed — nnMain + GetService log still owed.

## Update 2026-10-31 23:39 CST — emitter coverage work, no new run yet
- Blocker class after 18:30: emitter instruction coverage, not missing-function discovery. STOP 90763a0 is `dup v0.2s, w9` (NEON SIMD) — the emitter hit an unsupported mnemonic, emitted `a64_undef`, and mid-function resume dispatched to nothing. Quantified from the 18:30 run: ~75 SIMD/FP mnemonics, 14,917 sites (fsub x3,860, fmul x2,778, dup x1,850 total sites), init 438 of 6,858 (6.4%).
- Freebuff is now editing the emitter itself: `src/recomp/a64.py` 69,757 B -> 81,494 B at 21:39, then 82,107 B at 23:39 (+12,350 B total). This push snapshots the 23:39 a64.py; it was still being edited at push time, so a later edit may supersede it before the next run.
- New tools in git with this update: `tools/diag_9076.py` (2,520 B, 18:39) — 90763a0 diagnosis; `tools/diag_forms.py` (2,446 B, 20:20); `tools/census_forms.py` (4,503 B, 21:45) — census of SIMD/FP forms/sites.
- No new run: gen.py unchanged 26,426 B (17:31), boot.log unchanged 73,548 B (18:30), exe unchanged 406,729,216 B (18:30), init 438, STOP still 90763a0, svc_count 0, no nnMain, no GetService.
- Gate 1 still NOT passed — nnMain + GetService log still owed. Next step to watch for is a regen + rebuild + re-run from the new a64.py.
