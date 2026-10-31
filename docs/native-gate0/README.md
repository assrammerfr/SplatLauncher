# SplatNative Gate 0 — Splatoon 3 native compatibility runtime

Gate 0 audit for the Splatoon 3 native runtime, by Freebuff, 2026-10-30.

- `GATE0_ARM_AUDIT.md` — Gate 0 audit (30,219 B on the PC original). One cross-reference word in the original was changed in this git copy to keep this repo free of other-project names; the PC original in `E:\SplatNative\docs\` is unchanged.
- `sources.md` — every external URL behind the audit's claims.
- `ryujinx_boot_2026-10-30_1555.log` — copied Ryujinx boot evidence log, no keys / game data.

Gate 0 status: PASSED with constraints (see audit). Static recompilation is viable in principle; Splatoon 3 static is still to be built. NVN ~453 entry points are Splatoon 2-measured, Splatoon 3 surface to be verified in Gate 1.

This is documentation only. No runtime code, no game files, no keys, no firmware in this folder.
