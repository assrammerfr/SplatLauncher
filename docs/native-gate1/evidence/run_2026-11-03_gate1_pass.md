# Gate 1 pass — 2026-11-03 14:26 PC time

Verified from disk on the PC, not from a chat claim.

- Run log: `boot_20261103_142644.log`, 403,031 B, 8,171 lines
- Executable: `build\splatnative.exe`, 463,009,792 B, 2026-11-03 14:26 PC time
  (binary itself is never committed; see `FILES.md`)
- Init: 6,858, full
- `[boot] calling entry nnMain at c934940`
- `[svc] GetService('fsp-srv') via 'sm:' -> h=1001`
- svc_count: 11, histogram #00d:1, #00f:1, #010:1, #01f:1, #021:5,
  #026:1, #029:1
- crashes: 0, undef: 0, `svcCreateThread (#8)`: 0

Gate 1 pass bar — recompiled rtld, main, and SDK run to `nnMain`
with a `GetService` enumeration log — is met: both `nnMain` and
the `GetService('fsp-srv')` line are in the same run log.

Note: the run still ends at `[svc] BREAK x0=0 x1=20ffed98 x2=4
x3=0 x4=20ffed98 x30=e79bb18`, then
`[done] pc=dead0000dead0000 svc_count=11`. That post-GetService
BREAK does not undo the Gate 1 pass; it is the first Gate 2
blocker. Next gate: title / plaza draw + CSV.

Prior run for comparison: 2026-11-03 08:44 PC,
`boot_20261103_084427.log`, 398,972 B, svc_count 9, GetService 0,
same BREAK address `e79bb18` with x1 u32 `0001a80a`. The
passing run's sm: logging already showed the guest reaching
the service manager and asking for fsp-srv; this run is the
first with an explicit `GetService('fsp-srv')` line and a
returned handle.
