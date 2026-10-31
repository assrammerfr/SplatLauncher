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
