# Emitter coverage work, 2026-10-31 evening — no new run yet

Baseline: 18:30 run, exe 406,729,216 B, boot.log 73,548 B, 3,108 lines,
fn table 438,383, init 438 of 6,858, STOP [undef] pc=90763a0 then
[unmapped] pc=90763a0, FAILED rc=-1, svc_count=0, undef=2, no nnMain,
no GetService.

Classification (Freebuff, 18:48, from the 18:30 boot.log numbers):
90763a0 is `dup v0.2s, w9` — NEON SIMD, #1 in the main gap list
(dup x1,083 there). The emitter hit an unsupported mnemonic, emitted
`a64_undef`, and mid-function resume dispatched to nothing. Stop class
changed: missing-function discovery -> emitter instruction coverage.
Quantification: ~75 SIMD/FP mnemonics, 14,917 sites (main: fsub x3,860,
fmul x2,778, dup x1,850 total sites). Context already has full
Q-register storage.

Work after 18:30, verified from disk on the PC at push time:
- tools/diag_9076.py, 2,520 B, 18:39
- tools/diag_forms.py, 2,446 B, 20:20
- src/recomp/a64.py, 69,757 B -> 81,494 B at 21:39 -> 82,107 B at 23:39
- tools/census_forms.py, 4,503 B, 21:45
- tools/gen.py unchanged, 26,426 B, 17:31
- boot.log unchanged, 73,548 B, 18:30; exe unchanged, 406,729,216 B, 18:30

Gate 1 still NOT passed. Next step is a regen + rebuild + re-run from
the new a64.py, judged by root boot.log only (init count, STOP address,
svc_count, nnMain + GetService), never by stale build/boot.log.
