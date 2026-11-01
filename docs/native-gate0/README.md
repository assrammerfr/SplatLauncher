# SplatNative Gate 0

Gate 0 is the feasibility audit for the Splatoon 3 native runtime, completed 2026-10-30.

## Status

Gate 0 passed with constraints. Static recompilation (A64 to C to x86-64, no JIT) is viable in principle for Splatoon 3. The runtime itself was not yet built at Gate 0; that work is Gate 1, documented in `docs/native-gate1/`.

## Findings

- The ARM execution path is viable as static recompilation, with graphics at a single `nvnBootstrapLoader` import and Horizon services handled at the loader.
- The NVN surface count of ~453 entry points in the audit is Splatoon 2-measured. The Splatoon 3 surface is to be verified in Gate 1.
- Public static recompilation projects are title-specific, not generic. SplatNative follows that pattern: it targets Splatoon 3 specifically, not Switch games in general.

## Contents

- `GATE0_ARM_AUDIT.md` — Gate 0 audit. One cross-reference word in the original was changed in this repository copy to keep this repository free of other-project names; the original on the build PC is unchanged.
- `sources.md` — every external source behind the audit's claims.
- `ryujinx_boot_2026-10-30_1555.log` — copied emulator boot evidence log. No keys or game data are included.

This folder is documentation only. No runtime code, no game files, no keys, and no firmware are included here.
