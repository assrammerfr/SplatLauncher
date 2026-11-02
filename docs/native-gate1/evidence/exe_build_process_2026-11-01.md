# SplatNative exe build process — 2026-11-01 evening

Documented, NOT pushed to GitHub. The exe binary itself is never pushed.

## What was built
- File: `E:\SplatNative\build\splatnative.exe`
- Size: 460,216,832 B
- Timestamp: 2026-11-01 19:53 PC time
- Previous state: exe absent at every watch check since the 09:03 failed build (C2440 build failure, no linked exe). This 19:53 build is the first linked exe after that failure.

## Build inputs (verified from disk, PC time)
- `src/recomp/a64.py`: 136,812 B, 2026-11-01 18:43:22
- `src/runtime/a64_ctx.h`: 90,806 B, 2026-11-01 18:43:23
- `src/runtime/main.c`: 8,381 B, 2026-10-31 12:58:22 (unchanged)
- `src/runtime/nso_loader.c`: 11,952 B, 2026-10-31 11:15:48 (unchanged)
- `src/runtime/a64_helpers.c`: 8,628 B, 2026-10-31 05:47:26 (unchanged)
- `tools/gen.py`: 26,426 B, 2026-10-31 17:31 (watch baseline, unchanged at 21:03 check)
- Build pipeline log: `E:\SplatNative\pipeline_full.log`, 10,410 B, 2026-11-01 19:53:50
- Census before build: full MSVC census 4 TUs ok, 56 failing, ~2,291 errors, ~9 root causes (fcvtzs/scvff, faddp/fmaxp/fminp, fmul broadcast, shsub widths, frecpe/frsqrte/frint, sli/sri, fabd, `__fm` redeclaration). Census5 subset (14:11): 9 TUs, main_gen_012/013/014/015 FAIL.

## How it was built (Freebuff's pipeline, as observed)
1. Static recomp: A64 -> C via a64.py (emitter) + a64_ctx.h (runtime context / SIMD helpers).
2. Generated C shards under `build/gen/` — these are NOT pushed, NOT documented with content here, only their existence.
3. MSVC build of generated shards + runtime (main.c, nso_loader.c, a64_helpers.c) into `build/splatnative.exe`.
4. Previous blocker that stopped linking: C2440 SIMD header errors (09:03, no exe). Fixed by a64.py emitter fixes + missing header definitions (mla/mls, lane-suffixed umaxv, 2-arg uaddlv, etc.), then further SIMD emitter growth to 136,812 / 90,806 B.
5. Builder: Freebuff (MiMo v2.6 Flash Fast, local, Tab 4). Muse does not build; Muse writes prompts, Freebuff builds.

## Run that proved the exe
- Run log: `E:\SplatNative\boot.log`, 324,251 B, 10,983 lines, 2026-11-01 19:53:50
- Duplicate run output: `E:\SplatNative\run_out.txt`, same size/timestamp
- fn table: 438,297
- Init: 438 (01:33 run) -> 5,640 calls, then STOP at [unmapped] pc=0 after [init] call bee4480
- Previous blocker gone: 90763a0 [undef] mn=dup = 0, ecb0cd4 = 0, no [crash]
- nnMain: 0, GetService: 0 — Gate 1 still NOT passed
- Crash theory in progress (Claude + Freebuff, 2026-11-01 evening):
  - pc=0 and x30=0, sp=20fff000 (initial stack)
  - Trampoline: bl/blr set x30, ret = c->pc = x30; a blr to null would leave x30 = return address, not 0, so null-function-pointer ruled out
  - Remaining: ret with x30 already 0, or br through zeroed register
  - 85061e0 function: stores (x0,x1,x2) into 24-byte slot at begin+index*24, atomic ldaxr/stlxr increment at e50abc0, returns -1 on a legitimate branch — not a bug per se
  - Tail-branch alternative: last 85061e0 entry dispatched NOT via _call but via br xN / tail b trampoline `c->pc = 0x85061e0; return;` with x30 already 0; grep found tail branches in main_gen_000 without x30 set
  - Propagation: prologue saves x30, epilogue restores x30; if caller had x30=0 at entry (tail branch), 0 propagates through the chain
  - Next precise step (queued for Freebuff, after she is done): log x30 and sp at every [init] entry as `[init] call <addr> x30=<hex> sp=<hex>`, dump 32 qwords of guest stack from sp-0x100 to 20fff000 on any [unmapped]/[undef] stop, write output to a timestamped file, plus after relocation list every GOT/PLT slot still 0 with symbol name, and disassemble 85061e0..8506280 to check adrp/ldr/br stubs vs real functions

## What is NOT in this document / NOT pushed
- No exe binary, no obj files, no build/gen/*.c content, no vendor LZ4, no __pycache__
- No game files, keys, firmware, titlekeys, NSO bytes, or decrypted ExeFS
- No Nintendo art, no "bypass" wording
- This file lives only in Muse's workspace (`~/workspace/splatnative-exe-build-process-2026-11-01.md`), not in the SplatLauncher repo, and was not pushed to GitHub on 2026-11-01 per Seth's order.
