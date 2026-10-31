# Gate 0 — external sources consulted (web research, 2026-10-30)

Kept separate from the audit so every claim in GATE0_ARM_AUDIT.md can be traced.

## Static recompilation pipelines (Switch / NSO)
- https://github.com/Cor3Duo/kirikae — Rust A64→IR→Cranelift recompiler; loads .nso/ExeFS/.dnsp;
  per-game coverage JSON logging every IPC/SVC/nvdrv ioctl; MIT/Apache-2.0; Windows x64/Linux; Vulkan 1.3.
- https://github.com/Coz-0/smo-recomp — Super Mario Odyssey 1.4.1; A64→C lifter, Unicorn difftest
  (23,194/23,194), own NVN→Vulkan runtime; **GPL-3.0** (study only, no code copy).
- https://github.com/OpenSmash-Ultimate/ssbu-recomp — Smash Ultimate; A64→C, ~1,100 nn::* entry
  points reimplemented, nvn_vk*.c / nvn_gl.c.
- https://github.com/Nycolazs/ACNHRecomp and docs/REFERENCES.md — Animal Crossing: New Horizons on
  the SuperMarioWonderRecomp framework; documents HLE-first rtld, Horizon kernel HLE, IPC/CMIF,
  NVN→Vulkan with Ryujinx MIT shader translator; confirms SuperMarioWonderRecomp = MIT.
- https://github.com/BelmanteGu/SuperMarioWonderRecomp — **404 as of 2026-10-30** (was public;
  indexed discussion confirms A64→C++ recompiler + NVN-on-Vulkan, macOS/Windows/Android).
- https://github.com/seedds/nxnative and https://nxnative.dev/tr/games/final-fantasy-tactics-the-ivalice-chronicles
  — NSO rewriter + Horizon HLE + NVN→Metal; states "main imports only nvnBootstrapLoader".
- https://github.com/bgyss/SwitchRecomp — spec-only pipeline (does not run games).
- https://github.com/wangxiaoxiang5599/recompiled-games-list — index of 27 Switch recomp projects
  (of 311 total) as of 2026-10-30.

## Dynamic translation (fallback path)
- https://github.com/azahar-emu/dynarmic — AArch64 dynamic recompiler, 0BSD, maintained (Azahar).
- https://docs.ryujinx.app/ — Ryujinx discontinued 2024-10-01; Ryubing continues docs; ARMeilleure
  = A64→IR→x86 with PPTC (MIT). Local proof of it running S3: evidence\ryujinx_boot_2026-10-30_1555.log.

## NVN / Horizon service references
- https://switchbrew.org/wiki/Services_API — sm mechanism; per-title ACID/ACI0 service lists live in
  the title's NPDM; full service-name table.
- https://switchbrew.org/wiki/NV_services — nvdrv ioctl surface (the layer *below* NVN).
- https://switchbrew.org/wiki/Nvnflinger_services — vi/nvnflinger/dispdrv display path.
- https://switchbrew.org/wiki/Audio_services — audren/audout API.
- https://github.com/Cor3Duo/kirikae — its coverage output is the closest public per-game empirical
  manifest pattern (method we mirror with our own sm-hook logging).

## NVN-over-D3D12
- No public project found (searched NVN+D3D12/Direct3D12/DX12). Every verified Switch runtime targets
  Vulkan/MoltenVK/Metal. Treat "NVN→DX12" as greenfield — consistent with the prompt's mandate
  (DX12, NOT Vulkan). Local prior-art note: E:\Splatoon Games\sp2decomp\DX12_NVN_MAP.md.
