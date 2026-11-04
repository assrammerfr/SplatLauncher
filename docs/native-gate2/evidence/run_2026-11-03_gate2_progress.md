# Gate 2 progress — post-GetService BREAK cleared, new crash at e8d070c

Run: `boot_20261103_162739.log`, 428,872 B, 8,383 lines, 2026-11-03 16:27 PC time.
Executable: 465,902,592 B (binary itself is never committed; see `FILES.md`).
Runtime source on the PC at this run: `main.c` 19,789 B, `a64_helpers.c` 30,087 B.
The PC `main.c` copy still contains a builder name in a log string, so it is
NOT committed here — same standing rule as the Gate 1 pass push. Runtime
source snapshots in this repo therefore remain the older copies; only the
run numbers below are new here.

## Verified from the run log

- Init 6,861 (full), `[boot] calling entry nnMain at c934940`
- GetService lines, all guest-driven via `sm:`:
  - `GetService('fsp-srv') via 'sm:' -> h=1001`
  - `GetService('apm') via 'sm:' -> h=1002`
  - `GetService('appletOE') via 'sm:' -> h=1003`
- svc_count 25, undef 0
- Old post-GetService BREAK `e79bb18`: 0 occurrences in this run.
  That BREAK was the first Gate 2 blocker from the Gate 1 pass run
  (14:26, svc_count 11) and from every run up to 15:57 the same day
  (svc_count 14, fsp-srv only). It is cleared here.
- New stop, same run: `[crash] code=c0000005 fault=0 guest_pc=e8d070c
  sp=20ffedc0`, with `x30=e8d070c`. Two earlier vectored-exception
  entries at guest_pc `ca3e8a8` (addr 1be00000) did not stop the run;
  the run continued past them to the GetService calls above.
- No draw, no CSV in this run. Gate 2 is NOT passed.

## What changed vs the Gate 1 pass run

- svc_count 11 -> 25
- Services: fsp-srv only -> fsp-srv + apm + appletOE
- Post-GetService BREAK e79bb18: present -> absent
- New blocker: crash at e8d070c, not yet diagnosed

## Next step

Diagnose the e8d070c crash at its root cause (what the guest was
calling, what value / return it hit), fix it without breaking the
GetService path, and continue toward a title / plaza draw + CSV.
That draw + CSV, both in the same run, is the Gate 2 pass bar.
