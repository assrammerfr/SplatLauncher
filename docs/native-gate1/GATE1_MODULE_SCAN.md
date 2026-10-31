# Gate 1 — Module Scan: Splatoon 3 v11.2.0 program dump (measured, not assumed)

Builder: Freebuff. Date: 2026-10-30 (CST), ~22:50.
Inputs: `E:\S3_extract\` — `rtld` (10,109 B), `main` (51,866,583 B), `sdk` (6,204,231 B),
`main.npdm` (1,636 B), extracted via **Ryujinx Extract Data → ExeFS** (the V2-sanctioned path;
hactool NOT used; no keys read by me — Ryujinx used its own keyset internally).
Tool: `E:\SplatNative\tools\nso_scan.py`. Raw outputs: `evidence\gate1_module_scan.txt`,
`evidence\gate1_scan.json`, `evidence\gate1_nvn_surface.json` (sizes on disk, see §6).

This document UPDATES the Gate 0 tables with S3-measured values (V2 Caveat 2: the ~453 number was
S2-measured; the S3 numbers below are measured on S3 itself).

## 1. Identity: the dump is the running v11.2.0 build

| Module | Build id (NSO header @0x40, 20 B) | In Ryujinx boot log? |
|---|---|---|
| rtld | `263C3426D28AF8D8CCE29A9D2DA8FF0FF9A76456` | ✓ id 1 of 3 |
| main | `6830B3A12406CB4716FEC5ADDC35D3E2DC92D212` | ✓ id 2 of 3 |
| sdk  | `574284B40C06CF1821A44857939083BEE5716820` | ✓ id 3 of 3 |

`main.npdm` ProgramId = `0x0100C2500FC20000` ✓ (at META+0x480, inside ACI0).

## 2. The NVN hook — CONFIRMED ON S3 (Gate 0 mechanism, now S3-verified)

- **`main` IMPORTS**: `nvnBootstrapLoader`, `nvnPrivate0` (of 1,210 total imports).
- **`sdk` EXPORTS**: `nvnBootstrapLoader`, `nvnBootstrapLoaderInternal`, `nvnDevtoolsBootstrap`,
  `nvnInstallGlobalDebugCallback`, `nvnPrivate0` (of 23,057 total exports).
- Therefore in our loader, resolving `main`'s `nvnBootstrapLoader` symbol HLE-first routes every
  NVN call the game makes to our DX12 backend; the guest's NVN body (inside `sdk`) never executes
  and `nvdrv` is never reached. This is the Mezcal-shaped single boundary, verified on S3 — no
  fallback to nvdrv ioctl stubbing is currently expected (re-verify at first boot).

## 3. S3 NVN surface — MEASURED (replaces S2's 453 where S3 numbers apply)

- **571 distinct `nvn*` identifiers** exist as resolver strings across `main` (536) + `sdk` (38)
  — `evidence\gate1_nvn_surface.json`. This is the upper bound of the name-resolved surface.
- The **exact requested subset** is still runtime-defined (the game asks by name at init); our
  resolver will log every requested name on first boot and produce the exact list. Gate 0's
  S2-measured bring-up subset (30–33 builder/initialize functions) remains the prior for the
  first milestone; S3 is expected to be similar with SDK-20 additions.
- Status per family: unchanged from Gate 0 §5.2 (all "needs shim → DX12"; shader families are the
  high-risk item).

## 4. Horizon service ACL — EXACT LIST (49 services, ACI0 of main.npdm)

Parsed from ACI0 @0x470 (service list @0x4F0, size 0x144, length-prefixed entries, exact region
cover). Title's authorized set:

```
acc:u0  aoc:u  apm  appletOE  audin:u  audout:u  audren:u  banana
bcat:u  bsd:u  bsdcfg  caps:su  caps:u  csrng  ectx:aw  friend:u
fsp-srv  hid  htc  htc:tenv  htcs  hwopus  irs  ldn:u
ldr:ro  lm  mii:u  mm:u  mnpp:app  nfc:mf:u  nfc:user  nfp:user
ngc:u  ngct:u  nifm:u  nim:eca  notif:a  nsd:u  ntc  nvdrv
pcm  pctl  pl:u  prepo:u  set  sfdnsres  ssl  time:u  vi:u
```

### Updated Gate 0 §4 status table (Gate 1 observations marked)

| Service (ACL) | Gate 0 boot-log observation | Gate 1 status |
|---|---|---|
| (sm — not in ACL, always available) | all GetService traffic | **to shim** (enumeration hook) |
| `fsp-srv` | OpenDataStorageByDataId, EnsureSaveData | to shim (host FS + LayeredFS) |
| `appletOE` (am/applet) | GetAppletResourceUserId, AcquireForegroundRights, SetFocusHandlingMode, LockExit… | to shim |
| `hid` | Npad/touch/mouse/keyboard/sixaxis calls | to shim |
| `acc:u0` | InitializeApplicationInfo, CheckAvailability, GetAccountId | to shim |
| `aoc:u` | (Ryujinx logs these as ServiceNs PrepareAddOnContent / CheckAddOnContentMountStatus) | to shim — Gate 3 DLC target |
| `nvdrv` | full ioctl list in boot log | **expected handled via** §2 hook (re-verify at boot) |
| `vi:u` | SurfaceFlinger CreateLayerFromId | to shim (layer bookkeeping; pixels via NVN/DXGI) |
| `audren:u` (+ audin/audout) | Initialize REV15, Start | to shim (WASAPI) |
| `nifm:u` | CreateRequest/Submit/GetResult | to shim — offline result (log proves boot tolerates failure) |
| `pctl` | Initialize, IsFreeCommunicationAvailable | to shim (allow) |
| `prepo:u` | ProcessPlayReport | to shim — local log only, send nothing |
| `set`, `time:u` | not observed in log | to shim (trivial) |
| `pl:u`, `caps:*`, `ngc:*`, `mii:u`, `nfp/nfc:*`, `irs`, `lm`, `ldn:u` | not observed | to stub lazily (implement on first request; log name) |
| `bsd:u`, `bsdcfg`, `sfdnsres`, `ssl`, `friend:u`, `bcat:u`, `nim:eca`, `nsd:u`, `notif:a`, `ntc`, `htc*`, `hwopus`, `pcm`, `ectx:aw`, `csrng`, `ldr:ro`, `mm:u`, `mnpp:app`, `banana`, `apm`, `aoc:u` | not observed (or offline-failing) | offline/network families → offline stubs; rest → lazy stubs; **nothing in this list is blocked** |
| Kernel SVCs (not in ACL — kernel surface) | thread/sync/mmap in log | to shim (host threads) |

No ACL service is marked blocked. Hard stop: network services stay offline stubs.

## 5. Code inventory (recompiler inputs)

| Module | .text | FDE functions (eh_frame_hdr) | BL call targets | dynsyms |
|---|---:|---:|---:|---:|
| rtld | 9,536 B | 33 | 23 | 2 imports |
| main | 82,373,904 B | **271,718** | 57,133 | 1,210 imports / 82 exports |
| sdk | 6,698,752 B | **32,673** | 9,903 | 8 imports / 23,057 exports |

- Function discovery = FDE table (MetaWorks `eh_frame_hdr` quirk decoded: u32 ptr, u32 count,
  s32 datarel pairs; validated rtld count 33 ≥ 23 BL targets, main sample entries well-formed).
- NSO header model decoded empirically (fileOff @0x10/0x20/0x30, memOff/memSize @+4/+8 per
  segment, csize @0x60/0x64/0x68 — three-way exact arithmetic fit on all three files;
  LZ4 block decompress OK on all 3 × 3 segments).
- SDK markers in dump: `NintendoSDK_NVN-20_5_22`, `NintendoWare_Vfx2/Ui2d/G3d2-20_5_22`,
  Havok 2021, Pia/NPLN — matches boot log (update build confirmed).

## 6. Evidence (sizes verified from disk)

| File | Size |
|---|---:|
| `E:\SplatNative\docs\GATE1_MODULE_SCAN.md` (this file) | see `stat` in report |
| `E:\SplatNative\evidence\gate1_module_scan.txt` | 30,941 B |
| `E:\SplatNative\evidence\gate1_scan.json` | 14,459 B |
| `E:\SplatNative\evidence\gate1_nvn_surface.json` | 20,283 B |
| `E:\SplatNative\tools\nso_scan.py` | scanner (rerunnable) |

## 7. What this does NOT establish

- Nothing has executed yet: no recompiled code has run. First runnable unit still pending.
- The exact NVN *requested* subset and the exact runtime GetService order are boot-time outputs.
- Shader pipeline (Maxwell→DXIL) untouched.
- FDE `(addr,size)` parse is validated on samples, not yet on all 304,424 entries end-to-end —
  the recompiler build will surface any outliers as logged decode gaps.
