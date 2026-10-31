# SplatNative — How It Is Built

Builder: Freebuff. This file describes the build only. No game files, no keys,
no firmware are in this folder, and none are needed to read this file.

## What is being built
A static A64 -> C -> x86-64 recompilation of Splatoon 3 (title-specific),
with an NVN -> DX12 runtime boundary and Horizon services HLE'd at the loader.
Target: modern DX12 FL_11_1 GPUs (RX 5700 XT and newer). GTX 570 is out.
This is NOT a general Switch emulator, NOT a launcher shell, and NOT a game
download. You must obtain your own files. See README.md for Gate status.

## Tree layout (on the build PC)
- `E:\SplatNative\` — build tree. This folder in git is a snapshot of its
  source, tools, and evidence only, under `docs/native-gate1/`.
- `src/recomp/a64.py` — A64 to C static recompiler.
- `src/runtime/` — host runtime: main.c, nso_loader.c, a64_ctx.h, a64_helpers.c.
  Vendor LZ4 (`src/runtime/vendor/lz4.c` + `lz4.h`) is a third-party
  dependency and is NOT committed here.
- `tools/` — generator, build, scan, and diagnostic tools. See FILES.md.
- `build/gen/` — generated C sources (recomp_meta.c, *_gen_*.c). Generated,
  NOT committed. See FILES.md.
- `build/obj/` — MSVC object files. Generated, NOT committed.
- `build/splatnative.exe` — linked first runnable unit. Generated, NOT committed.
- `boot.log` — run log from the last run. Summarised in evidence notes, not
  committed as a raw binary log in this folder (see evidence/).

## Build steps, in order
1. **Scan (done once, Gate 1 module scan)**
   `tools/nso_scan.py` — scans NSOs + NPDM, NVN surface, service ACL.
   Output: `GATE1_MODULE_SCAN.md`, `evidence/gate1_scan.json`,
   `evidence/gate1_nvn_surface.json`, `evidence/gate1_module_scan.txt`.
   Do NOT re-run Gate 0. Do NOT use hactool or SwitchExtractorTool for ExeFS.

2. **Generate**
   `tools/gen.py` — reads the module scan + NSO metadata, emits
   `build/gen/*.c` (recomp_meta.c + main/sdk/rtld shards) and
   `build/gen/gen_report.json`.
   Jump-table / cross-range seeding lives here: `scan_jump_tables`,
   `scan_crossers.py`, and the fixpoint scan that seeds computed-branch
   (BR/BLR) targets, not just BL targets and eh_frame FDEs.
   If gen.py changes, re-run gen.py before building. A stale gen output
   will produce a stale fn table (see the 07:33 build/boot.log trap in
   evidence/piece_a_ecccc48_fix_2026-10-31.md).

3. **Build**
   Requires: Windows, Microsoft Visual Studio 2022 BuildTools, vcvars64.
   - `tools/build.bat` — simple MSVC build: `cl /nologo /O2 /bigobj /MD`
     with `/Isrc\runtime`, sources `src/runtime/main.c`,
     `src/runtime/a64_helpers.c`, `src/runtime/nso_loader.c`,
     `src/runtime/vendor/lz4.c`, plus `build/gen/*.c`,
     output `build/splatnative.exe`, `/SUBSYSTEM:CONSOLE`.
   - `tools/build.py [--jobs N]` — parallel MSVC build, default `--jobs 4`.
     Freebuff runs use `--jobs 6`. Captures vcvars64 env once, compiles
     every runtime + generated TU with a bounded pool, then links.
   - Smoke test: `tools/build.bat smoke` compiles only sdk_gen_000.c.
   - Do NOT commit `build/obj/`, `build/gen/*.c`, or `build/splatnative.exe`.

4. **Run**
   Run `build/splatnative.exe`. It writes `boot.log` in the tree root.
   A run is measured by: fn table count, `[init] call` count, STOP /
   crash address verbatim, svc_count, undef count, and whether nnMain +
   GetService log appear. Link success or a running loop alone is NOT
   a Gate 1 pass. See README.md for the current run.

5. **Long builds**
   Long gen / build / run jobs MUST run as a Windows Scheduled Task,
   not as a child of an SSH session (SSH children are killed on
   disconnect). Check with Get-ScheduledTaskInfo: 267009 = still running,
   0 = success. The SplatNative pipeline task is `SplatNative_Pipeline`.

## How to verify a build (what Muse does)
- Check file sizes + timestamps from disk, not from memory.
- Check `boot.log` in the tree root, NOT `build/boot.log` (that file is
  stale, 07:33, pre-REIr, fn table 314,166, STOP 5ec1ac).
- Check `g_fn_count` in `build/gen/recomp_meta.c` against the run log's
  fn table count. A mismatch means a stale gen output was linked.
- Check for a new STOP address, new init count, and nnMain / GetService.
  Report numbers only, with file paths + sizes + timestamps.

## What is NOT built here
- No online play, no anticheat, no consensus / turn-hash work.
- No game files, keys, firmware, titlekeys, NSO bytes, or decrypted ExeFS.
- No circumvention wording, no Nintendo art, no character screenshots.
- No binary release. A release comes only after Gate 1 (nnMain +
  GetService), Gate 2 (title/plaza draw + CSV), and Gate 3 (saves / DLC /
  launcher integration, tested clean), in that order.
