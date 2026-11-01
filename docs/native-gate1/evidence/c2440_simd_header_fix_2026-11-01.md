# C2440 SIMD header fix, 2026-11-01 — no new run yet

Prior run: 2026-11-01 01:33, boot.log 53,609 B, 2,071 lines, init 438,
STOP 90763a0 ([undef] mn=dup), exe 394,501,120 B, no nnMain, no GetService.

Build failure after that run: C2440 compile errors, no link, no exe.
The 09:03 watch check verified build/splatnative.exe ABSENT on the PC.
No new boot.log was produced by the failed build.

Fix work, verified from disk at push time (Freebuff, MiMo v2.6 Flash Fast):
- 5 defect classes mapped to exact emitter lines.
- Read before editing: VTERN/VRED macro definitions (mla / umaxv / uaddlv
  header fixes), bitwise header block, bitwise emission region 2604-2680
  (every site suffixing v_eor / v_and / v_orn).
- Analysis: src_esz * 2 is strictly correct for the mla fix — no narrowing
  works with * 1, since the header defines no equal-width pairs.
- Applied: two a64.py emitter fixes, then missing header definitions in
  a64_ctx.h for three classes the emitter calls but the header never
  defined: integer mla / mls, lane-suffixed umaxv, and 2-arg uaddlv.
  Single-line anchors used to match the CRLF file safely.
- SIMD gap census reported by Freebuff: classes cleared 1,168 -> 90 total.
- Sizes: src/recomp/a64.py 135,621 B at 09:05, src/runtime/a64_ctx.h
  84,698 B at 09:11. gen.py unchanged 26,426 B. boot.log unchanged
  53,609 B (01:33 run). exe absent at last check.

Gate 1 still NOT passed. Next step is a full regen + build + run that
clears C2440, gets init past 438, with no [unmapped] on jump-table
targets, judged by root boot.log only.
