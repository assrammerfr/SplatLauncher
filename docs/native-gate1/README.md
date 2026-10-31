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
