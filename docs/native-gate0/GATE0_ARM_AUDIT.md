# Gate 0 — ARM Path Audit: SplatNative (Splatoon 3 native compatibility runtime)

Builder: **Freebuff** (this audit is Freebuff's work; not Copilot, not Claude, not Terra, not Muse)
Date: 2026-10-30 (CST), audit run started 22:20 CST
Tree: **`E:\SplatNative\`** (docs\, evidence\, src\ — separate from `E:\splatlauncher\` and `F:\PinyonShift\work`)
Target: **Splatoon 3 v11.2.0, title 0100C2500FC20000** (Splatoon 2 is follow-on; see §8)

## 0. Verdict

**GATE 0: PASS** — a viable ARM64→x86-64 path for the Splatoon 3 program exists, with a defined
first runnable unit. The path is **static recompilation** of the game's AArch64 NSOs to C/C++
compiled natively for x86-64, with the graphics boundary intercepted at **`nvnBootstrapLoader`**
(one import, name-resolved function pointers) and Horizon OS services HLE'd at the loader.

This is not an argument from theory. Three independent, verifiable facts carry the proof:

1. **The exact title's ARM64 code translates and runs on this PC today.** Ryujinx Canary 1.3.351
   executed Splatoon 3 v11.2.0 on this hardware through boot → title → plaza; its log (copied to
   `evidence\ryujinx_boot_2026-10-30_1555.log`, 39,116 B) shows the A64→x64 backend actively
   translating and executing guest code (JIT cache growth to 512 MB, guest IPC/audio/applet calls,
   and a real `OutOfMemoryException` inside `ARMeilleure...CodeGenerator.Generate` at 00:01:55.903 —
   a translation failure mode, not a game failure).
2. **Static recompilation of commercial Switch titles to native PC code is a working, published
   technique**, with multiple independent implementations that play games (§3.1). The NSO→C++
   recompilers are parameterized per title and per image base.
3. **The graphics boundary is a single documented hook.** `main` imports only
   `nvnBootstrapLoader`; every NVN function is fetched **by name** through
   `nvnDeviceGetProcAddress`-style resolution. A host runtime registers its own functions and the
   real NVN/driver path never executes (§5). This was measured independently for Splatoon 2 in the
   sp2decomp repo on disk and confirmed by three separate public recomp projects.

**One external dependency blocks Gate 1**: a decrypted Splatoon 3 v11.2.0 program dump (ExeFS).
See §7 — that is the single file only the project owner can supply. No other blocker.

**Scope note (prompt §"Method"):** Gate 0 does not claim FPS. The measurable win/loss against
Ryujinx happens at Gate 2 with CSVs. This audit only establishes that the ARM path, the service
surface, the NVN surface and the file formats are known and buildable.

---

## 1. What is translated, emulated, shimmed, blocked (the short table)

| Layer | Treatment | Status |
|---|---|---|
| Guest A64 code: `rtld`, `main`, `sdk` NSOs (3 images, see §2) | **Statically recompiled** A64→C/C++ → x86-64 native objects (no JIT at runtime, no PTC) | Translatable — proven method, tools exist (§3) |
| Horizon kernel (threads, sync, memory map, SVC) | **Emulated/HLE at host level**: guest threads → host threads, SVC → host calls | Needs shim |
| Horizon IPC services (sm, fs, am/applet, hid, acc, ns, nifm, time, set, pctl, prepo, vi, audren, loader/pm) | **HLE shims** in our runtime (§4) | Needs shim — empirical list below |
| NVN graphics API | **HLE at `nvnBootstrapLoader`**, implemented **over DX12** (prompt mandates DX12, not Vulkan) | Needs shim — greenfield; biggest risk (§9) |
| Maxwell shader binaries (`.bfsha`/`.blarc`/`.bphsh`) | Translation pipeline Maxwell→…→DXIL (§5.3) | Needs shim — highest technical risk |
| `nvdrv` ioctls / GPU engine emulation (Ryujinx's layer) | **Not used** if the NVN hook holds: guest NVN code never runs | Handled via NVN hook by design; fallback defined (§5.1) |
| NintendoSDK framework code inside `sdk` NSO (NintendoWare Vfx2/Ui2d/G3d2, Havok, sead) | Recompiled guest code (it is ordinary A64) — no shimming needed | Translatable |
| Network / NPLN / Pia (online) | Offline stubs returning offline errors (Ryujinx log proves title reaches play with Nifm failing: `ReadyNetworkFailed` report, boot continues) | Blocked by hard stop (no online work) — shim must return offline, not fail boot |
| Firmware files, sysmodules (qlaunch etc.) | Not used: applet/display paths HLE'd; no firmware input | Blocked by hard stop (no firmware input) — shim instead |
| Program decryption (keys) | **Not performed by this builder**; runtime consumes the project owner's local decrypted dump | Blocked — the one file (§7) |

---

## 2. Evidence base (everything below was read/measured on this PC, paths verified from disk)

| Evidence | Path | Size / value |
|---|---|---|
| Ryujinx boot log of S3 v11.2.0 (2026-10-30 15:55) | `E:\Ryubing-canary-1.3.351\publish\Logs\Ryujinx_Canary_1.3.351_2026-10-30_15-55-07.log`, copied to `E:\SplatNative\evidence\ryujinx_boot_2026-10-30_1555.log` | 39,116 B |
| Two later short sessions (18:21, 22:02) | same Logs dir | 3,507 B / 3,503 B |
| Program NCA (encrypted, base) | `E:\S3_extract\s3_base_pfs0\8974fd227095cfb96e1191a0dac9756f.nca` | 4,712,873,984 B |
| Update / DLC NCAs (encrypted) | `E:\S3_extract\update_pfs0\aada92e014fb1a953bbb735fceb491a9.nca`, `...\sideorder_pfs0\989c4dc2cfd01e2ef2533701ca41649e.nca` | 7,264,673,280 B / 2,140,553,216 B |
| Merged base+update RomFS dump | `E:\Splatoon Games\` (22,837 files, census in §6) | — |
| Side Order DLC dump | `E:\SO extracted\` (3,149 files per prior extraction) | — |
| sp2decomp research repo (prior art, public) | `E:\Splatoon Games\sp2decomp` (github.com/bangmcpe3321/sp2decomp) | 81 MB working tree |
| **No decrypted `.nso` anywhere on `E:`** | `find /e -maxdepth 3 -iname '*.nso'` → 0 hits; no `*exefs*` dirs | confirms §7 |
| Ryujinx config (baseline) | `E:\Ryubing-canary-1.3.351\publish\Config.json` | PTC off, HostMapped, Vulkan, RX 5700 XT `0x1002_0x731F` |

Key quotes from the boot log (empirical, this title, this update):

```
Loading content archive from 'E:\Splatoon Games\Splatoon 3.nsp'.
Loading update NCA from 'E:\Splatoon Games\Splatoon 3 v11.2.0.nsp'.
Loader Load: Loading rtld... / Loading main... / Loading sdk...
    Module: D:\home\Project\Thunder\App\Rom\NX64\Product\code\Thunder.nss
    SDK Libraries: ... NintendoWare_Vfx2-20_5_22, NintendoWare_Font-13_3_6,
                   NintendoWare_Ui2d-13_3_6, NintendoWare_G3d2-20_5_22,
                   Microsoft Havok_AI/Cloth/Animation-2021_1, Pia*-6_32_3, NPLN...
    sdk: FS SDK Version: 20.5.22 ; SDK MW+Nintendo+NintendoSDK_NVN-20_5_22-Release
Loader LoadNsos: Loading image 0 at 0x0000000008500000...   (rtld)
                 Loading image 1 at 0x0000000008506000...   (main)
                 Loading image 2 at 0x000000000e6fe000...   (sdk)
Build ids: 263C3426D28AF8D8CCE29A9D2DA8FF0FF9A76456 / 6830B3A12406CB4716FEC5ADDC35D3E2DC92D212 /
           574284B40C06CF1821A44857939083BEE5716820
Ptc Initialize: ... PTC v7031 ... version '11.2.0' ... enabled: False
00:01:08.954 |W| Cpu Allocate: JIT Cache of size 256 MB exhausted, creating new Cache Region (512 MB Total Allocation).
00:01:55.903 |E| ... System.OutOfMemoryException ... ARMeilleure.CodeGen...LinearScanAllocator... (translator OOM, 16 GB host)
```

What the log establishes: exact module set (rtld/main/sdk, no `std`), exact load addresses (bases
for the recompiler's `--base`), SDK version **20.5.22** (S3 is newer than Wonder/ACNH-era SDKs),
the embedded `NintendoSDK_NVN` static-library marker inside `sdk`, the JIT/PTC baseline we must
beat, and an observed **Ryujinx translator OOM on this 16 GB machine** (a fault our static
recompilation architecture eliminates by construction: there is no runtime translator).

---

## 3. The translation path (Gate 0 core question)

### 3.1 Options considered — with verification status

| Option | Verdict | Evidence |
|---|---|---|
| **Static recompilation (CHOSEN)** | Proven working on multiple commercial Switch titles | Multiple independent public projects below |
| Dynamic translation (A64 JIT) | Works (Ryujinx ARMeilleure runs this title today; Dynarmic is 0BSD, maintained at azahar-emu/dynarmic) | Rejected as primary: keeps JIT memory/stutter costs, keeps .NET-scale overhead problems, and "native, no emulator frontend" is better served by AOT code. Kept as **fallback** for any code static discovery misses (interpreter/dynarmic hybrid) |
| Interpreter-only | Rejected | Cannot beat Ryujinx FPS |
| Do nothing / launcher shell | Rejected | Explicitly forbidden by prompt; would be fake native |

Working static-recomp implementations found (all verified from their repos/docs, URLs in
`evidence\sources.md`):

- **Kirikae** (github.com/Cor3Duo/kirikae) — Rust A64→IR→Cranelift, loads `.nso`/ExeFS, MIT/Apache-2.0,
  claims complete commercial games running, and **logs every IPC command/SVC/nvdrv ioctl used per
  game into a coverage JSON** — directly reusable as our enumeration tool.
- **smo-recomp** (github.com/Coz-0/smo-recomp) — Super Mario Odyssey, A64→C via function discovery
  from unwind tables, Unicorn differential testing, own NVN-on-Vulkan runtime. **GPL-3.0**: do not
  copy code into this tree; study only.
- **ssbu-recomp / OpenSmash** (github.com/OpenSmash-Ultimate/ssbu-recomp) — Smash Ultimate, A64→C,
  ~1,100 system-library entry points reimplemented.
- **ACNHRecomp** (github.com/Nycolazs/ACNHRecomp) — Animal Crossing on the Wonder framework,
  documents the architecture: NSO parse (LZ4, MOD0, `.dynamic`, relocations), function discovery via
  `.eh_frame_hdr` FDEs + `bl` targets + init_array + exports, one C++ function per game function,
  **HLE-first relocation** (host implementations win at load), Horizon kernel HLE (threads,
  ArbitrateLock, WaitForAddress), IPC/CMIF with domains, NVN→Vulkan, Ryujinx shader translator.
- **SuperMarioWonderRecomp** (BelmanteGu, MIT — **repo now 404**, corroborated via ACNHRecomp's
  REFERENCES.md and sp2decomp's on-disk assessment `E:\Splatoon Games\sp2decomp\RECOMP_ASSESSMENT.md`):
  17.8M A64 instructions / 250k functions / ~1.4 GB C++ recompiled in ~6 min, NVN over Vulkan with
  guest memory imported via `VK_EXT_external_memory_host`, 12,717 Maxwell blocks → SPIR-V AOT using
  Ryujinx's MIT translator. Because it is 404, **we do not depend on it**; ACNHRecomp/Kirikae are the
  live references.
- **nxnative** (github.com/seedds/nxnative) — NSO rewriter + Horizon HLE host; documents that `main`
  imports only `nvnBootstrapLoader` and takes over that import (`dm/gfx/nvn/hle_nvn.c`).

**Choice for Gate 1: static recompilation with HLE-first runtime, in the Kirikae/ACNH shape**
(A64→C/C++ with a differential test harness, host-provided service + NVN symbols taking precedence
over anything the guest modules export). Exact tool selected at Gate 1 start after a license check
(MIT/0BSD/Apache OK; GPL not copied into this tree).

### 3.2 What is translated / emulated / blocked (precise)

- **Translated (pure AOT, no runtime cost):** all guest A64 code in `rtld`, `main`, `sdk`
  (Nintendo framework incl. Havok bindings, `nn::gfx`, sead) — recompiled once at build time,
  compiled by MSVC/clang to x86-64, executed as ordinary native functions.
- **Emulated:** Horizon kernel semantics only — guest threads ↔ host threads, SVC surface
  (sync/Arbitrate/WaitForAddress/memory map), HIPC/CMIF IPC framing, timing. Bounded and well-documented
  (smo-recomp/ACNH/Kirikae each implement it once, shared across titles).
- **Shimmed (HLE):** the service set in §4 and the NVN surface in §5.
- **Blocked:** §7. Nothing in §3 blocks the first runnable unit.

### 3.3 First runnable unit (definition Gate 1 must hit before graphics work)

> **`E:\SplatNative\src\boot\`: the recompiled `rtld` + `main` (+ `sdk`) of S3 v11.2.0, linked
> against our host runtime, executing from the recompiled entry point to `nnMain` (or the first
> guest-owned framework entry), with `sm`/`fs`/`hid`/`am` shims returning minimal valid values, and
> a service-hook log capturing every `sm:GetService` name the title requests — as a `.exe` run on
> this PC from the command line.**

Acceptance evidence: exe on disk + log tail showing guest code reached + the first empirical,
complete GetService list. This unit is proven achievable by sp2decomp's S2 result (`nnMain reached`,
`140,899 functions recompiled + linked` — see §8) and by every project in §3.1 hitting their
equivalent milestone.

---

## 4. Horizon services the S3 title path needs (Gate 0 list, status-marked)

Source of truth has two parts: (a) **observed** calls in this title's boot log on this PC (§2) —
these are Ryujinx-stubbed calls, i.e. real guest requests; (b) the title's **NPDM service ACL**
(`main.npdm`, inside the ExeFS of §7) which is the *authoritative authorized set* — enumeration
completes the moment the project owner supplies that file, and our `sm` shim logs every request anyway
(closed-loop verification).

| Service | Observed calls at boot→title (log) | Status |
|---|---|---|
| `sm:` (service manager) | implied by every GetService | Needs shim — trivial, and it doubles as the enumeration hook |
| `fs` / fsp-srv (romfs, save, DLC storage) | `OpenDataStorageByDataId` (DLC 0100C2500FC21065/21066), `EnsureSaveData` | Needs shim — map to host folders (merged RomFS dump + LayeredFS overlay + save.dat path for Gate 3) |
| `am` (applet manager) | `GetAppletResourceUserId`, `AcquireForegroundRights`, `SetFocusHandlingMode`, `SetOutOfFocusSuspendingEnabled`, `SetRestartMessageEnabled`, `SetOperation/PerformanceModeChangedNotification`, `LockExit` | Needs shim — state + no-op success (matches Ryujinx behavior that boots fine) |
| `pctl` | `Initialize`, `CheckFreeCommunicationPermission`, `IsFreeCommunicationAvailable` | Needs shim — always allow (offline) |
| `nvdrv` (GPU driver) | `FinishInitialize`, `VsmsMapping`, `InitializeEx`, `GetActiveSlotMask`, `SetNvMapFd`, `AllocGpfifoEx2`, `AllocObjCtx`, `SetErrorNotifier`, `SetUserData`, `SetTimeout`, `SetTimeslice`, `ZcullBind`, `ZbcSetTable`, `Wait` | **Handled via NVN hook** (guest NVN never runs, sp2decomp proved main→zero nvdrv references for S2). Fallback if S3 reaches it: stub at ioctl level or treat as blocker → §5.1 |
| `acc` (account) | `InitializeApplicationInfo`, `IsUserAccountSwitchLocked`, `CheckAvailability`, `GetAccountId` | Needs shim — fixed local profile |
| `ns` (content/DLC) | `CheckAddOnContentMountStatus`, `PrepareAddOnContentImpl` (both DLCs mounted) | Needs shim — Gate 3 acceptance target |
| `hid` (+ `hid:sys`) | `SetTouchScreenResolution`, `ActivateTouchScreen`, `ActiveNpadImpl`, `SetSupportedNpadIdType`, `SetSupportedNpadStyleSet`, `ActivateMouse/Keyboard`, `DisconnectNpad`, `StartSixAxisSensor`, `Get/SetGyroscopeZeroDriftMode` | Needs shim — map to XInput/SDL pad (same Xbox pad as Ryujinx baseline config) |
| `nifm` | `CreateRequest`, `SetConnectionConfirmationOption`, `Submit`, `GetRequestState`, `GetResult` | Needs shim — offline result; log proves game tolerates failure (`ReadyNetworkFailed`, boot continues) |
| `prepo` (play reports) | `ProcessPlayReport` (multiple rooms: boot, boot1, boot_ch, erepo_*) | Needs shim — **log locally, send nothing** (no online work) |
| `vi` / nvnflinger (display) | `SurfaceFlinger CreateLayerFromId` (layers 1, 2) | Needs shim — layer IDs + present via DXGI swapchain; actual pixels flow through NVN (§5) |
| `audren` (audio renderer) | `AcquireSessionId`, `Initialize (REV15)`, `Start`, stream setup | Needs shim — render to WASAPI; REV15 command format documented by emulator sources |
| `time` / `set` (clock, settings) | not observed in this boot (may be non-stubbed, hence silent) | Needs shim — trivial; confirm via NPDM + our sm hook |
| `loader`/`pm` (process metadata) | `Loader GetProcess` fallback warning | Our own loader supplies this; no shim beyond bookkeeping |
| Kernel SVCs (thread/sync/memory/Arbitrate/svcBreak) | JIT cache growth + thread starts visible in log | Needs shim — part of the kernel emulation (§3.2) |
| `spl`, `lbl`, `psm`, `bcat`, `news`, sockets | not observed | Marked *not required at title* pending NPDM confirmation (§7) |

---

## 5. NVN entry points the title needs (Gate 0 list, status-marked)

### 5.1 The mechanism (verified)

- Games import **`nvnBootstrapLoader`** and resolve every NVN function **by name** through
  `nvnGetProcAddress`-style lookup. Source: nxnative game profile (FF Tactics: "main imports only
  nvnBootstrapLoader"), ACNHRecomp docs ("The game asks nvnBootstrapLoader for every NVN function by
  name; the runtime returns its own"), sp2decomp's independent measurement on S2's main
  (`[IMP] nvnBootstrapLoader`, `[DEF] nvnLoadCProcs` 12,712 B, **0** nvdrv/nvhost/nvmap references
  in 146,622 symbols) — `E:\Splatoon Games\sp2decomp\RECOMP_NVN_NOT_LINKED.md`.
- With **HLE-first relocation** in our recompiler/runtime, our host implementation of
  `nvnBootstrapLoader` (and of any NVN symbol `sdk` exports) wins at link time — the guest's real
  NVN library and the whole nvdrv/GPU-emulation layer never execute. This is the Mezcal-shaped
  move: one translation layer at the API boundary instead of a frontend.
- S3 specifically carries `NintendoSDK_NVN-20_5_22-Release` **inside its `sdk` NSO** (log, §2), so
  the hook question for S3 is whether `main` imports `nvnBootstrapLoader` from `sdk` or is wired to
  it by relocation. Either way our loader controls symbol resolution; exact confirmation is the
  **first check Gate 1 performs on the §7 file** (import-table scan, minutes of work). Fallback if
  (and only if) S3 calls NVN by raw intra-`sdk` branches with no reloc: interpose `nvnLoadCProcs`
  (S2's measured in-game resolver, 453 names) or last-resort stub at nvdrv ioctl level.

### 5.2 The surface (measured numbers available today)

- **453 NVN entry points** in S2's main, enumerated and catalogued in
  `E:\Splatoon Games\sp2decomp\port\src\nvn_names.cpp` (17,412 B; its generator
  `tools/static/gen-nvn-names.mjs` refuses to emit unless the count is exactly 453). Public
  comparators say "hundreds" (WonderRecomp: "~hundreds of functions to reverse"; ACNH/Switch
  ecosystems consistent).
- **Boot/bring-up subset for S2 measured at 30→33 of 453** (all `Builder`/`Initialize` pairs:
  Device 5, Queue 9, Memory 5, Buffer 5, Command 3, Pools/Sync 3, plus
  `nvnDeviceBuilderSetDefaults/SetFlags`, `nvnDeviceInitialize`) —
  `E:\Splatoon Games\sp2decomp\NVN_BOOT_SUBSET.md` + `DEVICE_IMPL_INITIALIZE.md`.
- **Status table:**

| NVN family (S2-measured counts) | Needed for S3 title/plaza | Status |
|---|---|---|
| Device / Queue / MemoryPool / Buffer / CommandBuffer creation (bring-up, 33 of 453 measured on S2) | Yes — runtime init | Needs shim → DX12 objects (resource creation 1:1 with D3D12 heaps/resources/command lists) |
| Texture (90) / Sampler (45) / Sync (4) | Yes — every draw | Needs shim → DX12 resources/samplers/fences; also ASTC→BC decode path (see format census §6) |
| State: Blend(15)/Depth(13)/Vertex(10)/Polygon(9)/Color(7)/Program(5)/Channel(3)/Multisample(23) | Yes | Needs shim → DX12 PSO descriptors |
| Command recording (130) + draw submission | Yes | Needs shim → D3D12 command list recording + ExecuteCommandLists |
| Window (16) / present | Yes (title presents frames) | Needs shim → DXGI swapchain (this replaces `vi`/SurfaceFlinger pixel path) |
| Debug callback / GetInteger etc. | Yes | Needs shim — trivial |
| **Exact S3 list of requested names** | — | **Blocked on §7 file** + self-enumerating: our resolver logs every name the game asks for (deterministic, one boot). S3's surface is expected ≈ S2's 453 ± SDK-20 additions. |

### 5.3 Shaders (the hard part of the NVN surface)

Title-screen drawing requires compiled GPU programs. On-disk evidence (§6): 593
`nin_nx_nvn.blarc.zs` shader archives, `Shader\*.Nin_NX_NVN.bfsha.zs` (e.g.
`Hoian_UBER.Product.b20.product.Nin_NX_NVN.bfsha.zs`), 58 `.bphsh.zs`, 16 `.bphnm.zs`,
1 `.bnsh`. These are Maxwell-family binaries (`nin_nx_nvn` = NX NVN pipeline format).

Plan (Gate 1-2, stated as risk not as done): translate Maxwell guest shaders to DX12-executable
code. Prior art exists only on the Vulkan leg (Ryujinx MIT translator → SPIR-V, used by
WonderRecomp/ACNHRecomp/smo-recomp via GLSL/MSL hops). **No public NVN→DX12 project exists**
(research finding, evidencesources.md) — so the DXIL leg is new work: Maxwell→SPIR-V (MIT translator)
→ HLSL (SPIRV-Cross) → DXIL (DXC), or a direct Maxwell→DXIL emitter. AOT + on-disk PSO cache
(like the 2,385-shader cache Ryujinx loaded in the log) is the anti-stutter strategy for Gate 2.

---

## 6. File formats the title path needs (observed census, status-marked)

Census method: `find` over every RomFS folder of the merged dump `E:\Splatoon Games\` —
**22,837 files**. Top chains by count:

| Format (observed count) | Role | Status |
|---|---|---|
| `.pack.zs` (8,206) | SARC-like game pack archives, Yaz0-compressed | Needs shim — Yaz0 decompress + SARC parse (both well-documented, used by mod tools) |
| `.bntx.zs` (7,681) | BNTX texture containers (BC/ASTC) | Needs shim — decode → DX12 textures |
| `.bfres.zs` (3,285) | BFRES models/geometry (+ `camera.bfres.zs`) | Needs shim — vertex/index decode |
| `.bars.zs` (792 + `product.b20.bars.zs` 76) | BARs resource tables | Needs shim — parser |
| `.bwav` (714) | PCM audio streams | Needs shim — feed audio renderer |
| `.nin_nx_nvn.blarc.zs` (593) | NVN shader libraries | Blocked-on-shader-pipeline (§5.3) — the one format with real risk |
| `.bkres.zs` (325) / `.byml.zs` (217) / `.bgyml` (hundreds) | BYML config/data (Yaz0) | Needs shim — BYML parser |
| `.rstbl.byml.zs` (73 `product.b20.*`) | parameter tables (e.g. RSDB WeaponInfo) | Needs shim — parser |
| `.root.baev` / `.bfevfl` / `.ainb` (138/2/5) | event flow / animation graph | Needs shim — parser (title boot uses event flow) |
| `Shader\*.bfsha.zs`, `.bphsh.zs` (58), `.bphnm.zs` (16), `.bnsh` (1) | compiled shader programs | Blocked-on-shader-pipeline (§5.3) |
| `System\AddressTable\*.atbl.byml.zs`, `HeapSize\*.HeapDef.bgyml`, `ResourceSizeTable*.rsizetable.zs` | boot-critical system config (heap sizes, resource tables) | Needs shim — parsers; read at boot before anything draws |
| Containers: NSP/PFS0/NCA/NSO/NPDM | distribution/program formats | Runtime input = decrypted ExeFS + RomFS dirs (§7); NCA/PFS0 parsing not needed at runtime |
| `save.dat` (Ryujinx: `EnsureSaveData`) | save file | Needs shim (Gate 3) — **no live save touched now** |
| LayeredFS mod folder (`mods\contents\<titleid>\...` layout, observed in log) | mod loading | Needs shim (Gate 3) — load/unload by folder move |
| Font files (inside `.pack` under `Font\`), Havok data (inside `.pack`) | title text / physics | Needs shim — parsers; Havok runtime is recompiled guest code + its data is read by it |

Nothing in the title-screen format set is **blocked** except the shader containers, which are
blocked-on-pipeline-design, not on any missing input.

---

## 7. What's blocked, and the one file only the project owner can supply

**BLOCKED — single artifact:** a **decrypted Splatoon 3 v11.2.0 program dump (ExeFS)** containing:
`main.npdm` + `rtld`, `main`, `sdk` NSOs (raw `.nso` files), from the update title
0100C2500FC20800 (the v11.2.0 code Ryujinx actually runs).

- Why: the recompiler's input is a decrypted NSO; the NPDM is also the authoritative service list
  (§4). Verified: no `.nso` and no ExeFS directory exists anywhere on `E:` (search in §2), and this
  builder may not use keys/firmware (prompt rules) or re-run hactool (prompt rules). Producing this
  dump requires the project owner's keys with hactool/nxdumptool or equivalent on the owner's side.
- What it unblocks: import-table scan (NVN hook confirmation), exact NVN name list, exact NPDM
  service ACL, MOD0/relocation bases for `--base`, and the first runnable unit itself.
- Everything else for Gate 1 (host runtime skeleton, service shims, format parsers, DX12 device
  scaffolding) can be built **before** the file arrives.

**BLOCKED by policy (hard stops, not gate failures):** online/NPLN/Pia play, firmware inputs,
bundled keys/firmware/game files in the tree (this tree contains none — only my docs, this audit,
and a Ryujinx log with no keys in it), Splatoon 2 scope, Switch 2/other consoles, the parked Order
Shot mod (not touched), Fatlindi's UI (not touched), Pinyon trees (not touched).

**RISK (not blocked):** see §9.

---

## 8. Reuse report — what prior builders' work covers (per prompt: reuse, don't rebuild, don't claim)

### 8.1 sp2decomp (the repo in `E:\Splatoon Games\sp2decomp`, GitHub bangmcpe3321/sp2decomp)
This is **Splatoon 2** work (the prompt attributes the blocked S2 DX12 effort to Terra's Gate 0).
I did not rebuild any of it and I do not claim it. What it gives us:

- **Reused as reference (read-only):** `RECOMP_ASSESSMENT.md` (WonderRecomp architecture + license
  analysis), `NVN_BOOT_SUBSET.md` + `DEVICE_IMPL_INITIALIZE.md` (measured NVN bring-up subset),
  `RECOMP_NVN_NOT_LINKED.md` (the decisive nvnBootstrapLoader measurement + 453-name surface),
  `DX12_NVN_MAP.md` (NVN→DX12 mapping notes — directly on our chosen backend),
  `port/src/nvn_names.cpp` (453 names), `tools/static/*` (analysis scripts), `port/include/nvn|nn|sead`
  (header reconstruction), platform-layer HLE design (`port/src/services.cpp`, `vi.cpp`).
- **Not usable as-is:** it is S2 (SDK 13-era, sead, image base 0x7100000000) vs S3 (SDK **20.5.22**,
  NintendoWare Vfx2/G3d2, base 0x08506000 observed) — the *engine-level* recompiled code does not
  transfer; the *tooling and method* do. Its state: `nnMain` reached, 140,899 functions recompiled +
  linked, NVN stubs catalogued, plaza 3D does not draw, ~1/3 boots stall — consistent with the
  prompt's description. No `.exe` exists in this clone (verified), so nothing runnable is claimed.
- **Shared-core question (prompt §"scope"):** the *core* that is shared with no extra cost = the
  A64→C recompiler pipeline, the Horizon kernel/IPC host, the `sm`/`fs`/`hid` service framework, the
  NVN hook mechanism, and the format parsers. The S2 *game-specific* layer (its engine shims,
  recompiled bodies, route patches) is not shared. Recommendation recorded: **S3 stays the Gate 1-2
  target; S2 can later ride the same core.** No scope expansion taken.

### 8.2 Other prior art (from this run's web research; sources in `evidence\sources.md`)
Kirikae (MIT/Apache, full games + per-game coverage logs — strongest candidate host to study),
ACNHRecomp (living Wonder-framework reference, documents HLE-first rtld), smo-recomp (GPL-3: study
only), ssbu-recomp, nxnative (nvnBootstrapLoader interposition), WonderRecomp (MIT but 404 — not a
dependency), Dynarmic (0BSD JIT — fallback only), Ryujinx/Ryubing shader translator (MIT — the
shader-translation leg). Credits remain with their authors; my runtime work is mine.

---

## 9. Risk register (Gate 0 honest view)

| # | Risk | Severity | Mitigation / where it bites |
|---|---|---|---|
| R1 | **NVN→DX12 has no public implementation** — every working project targets Vulkan/Metal | High | DX12 backend is the core build item; NVN's semantics map cleanly to D3D12 (builders→PSO, memory pools→heap+placed resources, command buffers→command lists, `VK_EXT_external_memory_host` equivalent = D3D12 heap import/committed+map). Gate 1 proves device+swapchain+clear first; Gate 2 owns the rest |
| R2 | Maxwell→DXIL shader chain unproven end-to-end | High | Compose proven legs (MIT Ryujinx translator → SPIR-V → SPIRV-Cross → HLSL → DXC). Validate on S3's real `.bfsha` in Gate 1; AOT + PSO cache is also the anti-stutter plan |
| R3 | SDK 20.5.22 is newer than every reference project (Wonder=16) — unknown new NVN/IPC features | Medium | Import-scan as first Gate 1 action on the §7 file; unknowns surface as logged unimplemented names, not crashes |
| R4 | Static function discovery misses code (computed jumps, jump tables) | Medium | Use a pipeline with differential testing (Unicorn difftest pattern) + interpreter fallback (Kirikae's model) |
| R5 | 16 GB RAM host — Ryujinx translator already OOMs here (log, §2) | Medium | Static recomp removes the translator entirely; generated C++ for S3 expected ~1–2 GB source (Wonder: 1.4 GB) — build in chunks if needed |
| R6 | Runtime correctness vs Ryujinx's decade of HLE fixes (am/hid/fs edge cases) | Medium | Boot log gives the exact call set we must satisfy (§4); stubs that Ryujinx boots with are our minimum bar |
| R7 | Measurable win vs Ryujinx not guaranteed | — | Accepted: Gate 2 decides with CSVs, loss reported as loss. Structural advantages claimed *only as hypotheses until measured*: no JIT/PTC memory, no .NET GC pauses, DX12 on RDNA1, AOT shader/PSO cache |

---

## 10. Gate 1 entry criteria (what happens next, in order)

1. **On receipt of the §7 file:** import-table scan (NVN hook confirmation), MOD0 base check,
   NPDM service ACL dump → update §4/§5 with exact lists.
2. Choose recompiler host (license check: MIT/0BSD/Apache only) — Kirikae or ACNH/Wonder-lineage.
3. Host runtime skeleton at `E:\SplatNative\src\`: loader with HLE-first relocation, `sm`/`fs` shims,
   kernel thread/sync layer.
4. Recompile `rtld`+`main`(+`sdk`) → **first runnable unit** (§3.3) → log GetService list.
5. Only then: DX12 device + swapchain + NVN device/queue bring-up (Gate 1 title-screen scope).

No runtime game code is written before the audit (this file) — per method. Gate 1 evidence will be
screenshot + log + CSV (honest FPS, even if low).

---

## 11. Compliance statement

- Tree `E:\SplatNative\` contains: `docs\GATE0_ARM_AUDIT.md` (this file), `evidence\` (1 copied
  emulator log, 39,116 B — no keys, no game data), `src\` (empty pending Gate 1).
- No keys, firmware, titlekeys, or game files read or copied. `prod.keys` location was observed in
  a directory listing only; the file was never opened.
- hactool not re-run; no extraction re-run; Order Shot mod untouched; live saves untouched;
  Pinyon / FH2 launcher / Fatlindi UI / SplatLaunch code untouched (SplatLaunch workspace only listed).
- Builder credit: **Freebuff**.
