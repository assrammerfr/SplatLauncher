# Gate 1 — boot progress and the current applet blocker

Working tree: `E:\SplatNative`. Nothing here is committed (the tree is not a git repo).
Run used for every claim below:

```
SN_ENTRY=osinit timeout 240 ./build/splatnative.exe > run_osinit.txt 2>&1
f=$(ls -t boot_*.log | head -1)
```

## 1. Where the guest gets to

`rtld` entry -> `nn::os::Initialize` returns 0 -> **6858 static initializers run** -> `nn::nnMain`
(0xc934940) is entered -> `nn::oe::Initialize` (0xeb05968) -> `nn::oe::InitializeApplet`
(0xeb0629c) -> `nn::am::InitializeApplicationInterfaces` (0xe8d0550) -> loops forever retrying
`appletOE` IPC. Log rate is ~90k messages/s; the run never returns.

## 2. Root causes found and fixed on the way there (all in this tree)

1. **Recompiler: `str wzr, [x, #imm]` was emitted as a 64-bit store.** `is_w()` in
   `src/recomp/a64.py` excluded `wzr`, so any 32-bit zero store also wiped the following four
   bytes. That corrupted `nn::os::Initialize`'s saved callee registers on the stack (the saved
   `x20` of `ThreadManagerImplByHorizon`'s caller). Fixed and all shards regenerated
   (`python tools/gen.py`, rtld 35 fns / sdk 65953 / main 372309).
2. **svc table**: `0x1e` GetSystemTick, `0x24` GetProcessId, `0x25` GetThreadId, `0x0c`
   GetThreadPriority, `0x1a`/`0x1b` ArbitrateLock/Unlock (real packed-word semantics). Before
   this the thread ctor aborted, and the guest spun 4.6e9 times at `lr=e847964`.
3. **Thread id / mutex tag**: `nn::os::Initialize(thread_id, arg1)` — thread_id must be a small
   30-bit value (0 self-deadlocks `SdkMutexType::TryLock`; `0xffff8001` sets bit 30). arg1 = 0 so
   `QueryMemory(0)` reports type 0 and the loader-argv parser is skipped.
4. **`svcQueryMemory`** must report the whole stack-guard span as type 0. The "is this range
   free" predicate at 0xe847884 rejects any candidate whose QueryMemory type != 0 and whose
   extent exceeds the reported span, so `MapAliasStack` aborted every time.
5. **Stack-guard host reservation** moved to `0x60000000..0x70000000` (`SR_BASE`/`SR_SIZE` in
   `src/runtime/nso_loader.c`, plus GetInfo 0xe and QueryMemory). `0x70000000..0x80000000`
   fails with err 487 because the host process already owns the top 2 MB of that span.

## 3. HIPC / CMIF wire format (measured from the SDK's own parser and writer)

The SDK ships both sides of the message codec, so this is not guesswork:

- `nn::sf::hipc::detail::HipcMessageBufferAccessor::ParseHeader(this, out, msg)` at **0xe8513f8**
- `nn::sf::hipc::detail::HipcMessageBufferAccessor::SetupHeader(this, msg, size, info)` at
  **0xe851234**
- `nn::sf::hipc::server2::v1::HipcServerSessionBase::ReplyImpl/EnsureReply` at 0xe8517e8/0xe851870

Message header (at `msg + 0`, 16 bytes) and the `HipcMessageHeaderInfo` it maps to:

```
word0: [15:0] tag        [16:19] send_statics  [20:23] send_buffers
       [24:27] recv_buffers [28:31] exchange_buffers
word1: [9:0]  num_data_words   [13:10] message type (0 = request, 1 = reply)
       [31]   has pointer descriptors / extra header words
word2: handle counts (copy/move)      word3: handle / pid
info struct: b[3]=statics b[4]=send_buffers b[5]=recv_buffers b[6]=exchange_buffers
             h[8] = data bytes (num_data_words << 2)
             b[0xa] = type tag (0..13)   b[0xb] = type index (0/1/2)
```

The CMIF packet ("SFCI"/"SFCO") sits inside the data words; its offset is **not** fixed: it is
`msg+0x10` for plain requests and `msg+0x30` when the request carries pointer descriptors
(seen on `appletOE` after the domain conversion). `src/runtime/a64_helpers.c` locates it by
scanning for `0x49434653`.

Debug helpers added for this (all gated to the first few messages per service):
`a64_dbg_parse_hipc()` (calls the guest's own `ParseHeader` on the request and on the reply),
`gh_call3()`, `g_ipc_dump_count()`. Requests parse as type 0 and our replies as type 0 as well;
`fsp-srv` and `sm:` clients accept those replies.

## 4. Current blocker, precisely

`nn::oe::InitializeApplet` (0xeb062e0..0xeb06328) calls a virtual on the am singleton and then
accepts only `[sp+8] == 0` or `0xf`; on failure it asks 0xed58910 whether to retry and loops.
The failing call chain is

```
nn::oe::InitializeApplet+0x38     0xeb06238
nn::am::InitializeApplicationInterfaces+0xcc  0xe8d061c   (calls 0xe8df544)
0xe8df544  (unsymbolized am helper) -> thunk 0xed47f00
thunk 0xed47f00 -> GOT 0xf26d8c0 -> nn::sf::hipc::client::HipcClientSessionManager::ConvertToDomainImpl (0xe8501c8)
```

Resolved at runtime, not guessed: `tools/got.py`-style RELA lookup cannot see that slot (it has
no relocation; RTld fills it), so the runtime logs the two GOT words on the first `appletOE`
message: `[ipc] am thunk slots -> e8501c8 e83d878`.

`ConvertToDomainImpl(out, handle, pid)` does:

```
f(&local_u16, pid)        ; 0xed4af30 -> GOT-filled
if (w0) return w0
out->u16_at_8 = local_u16
return g(handle, pid)     ; 0xed4af40 -> GOT-filled, performs the conversion
```

so either the small query fails or the conversion itself fails. The guest already consumed the
domain object id from our first reply (its next request carries it), so the failure is in the
follow-up: with the id the client issues a *domain* request whose CMIF packet we place at the
`SFCI` offset while the client leaves a pre-written `SFCO` header at `msg+0x10`. That reply is
rejected and the whole `am` init is retried.

## 5. Next concrete steps

1. Log the two remaining GOT slots of `0xed4af30`/`0xed4af40` the same way (`a64_dbg` prints
   `0xf26d8c0`/`0xf26d8c8`) and read their results (`w0`) for the `appletOE` conversion.
2. Build the reply with the SDK's own writer instead of echoing the request:
   `SetupHeader(accessor, msg, size, info)` (0xe851234) after
   `HipcMessageBufferAccessor::SetHeadPointers` (0xe85110c), then parse it back with
   `ParseHeader` and assert `type == 1`, `h[8]` == expected bytes and the counts match. That
   removes the whole class of "reply layout" failures for every service at once.
3. Only then continue: `am`/`applet` command set, `svcMapMemory`/`UnmapMemory`, `svcCreateThread`
   and the event/wait primitives, then `fsp-srv` romfs -> `E:\Splatoon Games\`.
4. Gate 2 (title/plaza draw + CSV) additionally needs the two greenfield layers named in
   `docs/GATE0_ARM_AUDIT.md` §9: the NVN->DX12 HLE (nothing in `src/` today) and the
   Maxwell->DXIL shader chain.

## 6. FIXED: the applet/am retry loop is gone (reply-construction rule)

The loop was caused by reply *framing*, not by applet command semantics. For a message whose
`word1` has bit 31 set (pointer/send-static descriptors present), the request layout is
`header(0x10) | descriptor list(0x10) | CMIF packet`. The HLE was echoing the request packet in
place, so the reply (a) still declared the request's descriptor list, (b) kept the request's
message type, and (c) therefore parsed as `type=0` (a request). `ConvertToDomainImpl`
(0xe8501c8) rejected it, `nn::am::InitializeApplicationInterfaces` (0xe8d0550) returned failure,
and `nn::oe::InitializeApplet` retried forever.

**Rule now implemented in `a64_helpers.c` (SendSyncRequest, imm 0x21):**

1. Locate the CMIF packet by scanning the message for `'SFCI'` (offset varies: `msg+0x10` for
   plain messages, `msg+0x20` when a descriptor list precedes it).
2. If the message header's `word1` bit 31 is set (descriptor-carrying), the reply must **not**
   echo that list. Rebuild instead:
   - `memset msg+0x10 .. 0x70` to zero (drops the descriptor list and any request echo),
   - copy the 16-byte CMIF header built earlier to `msg+0x10` (so the packet sits directly after
     the 16-byte message header),
   - write the returned domain object id at `msg+0x20` when there is one,
   - `word0 = tag` only (no send/recv/exchange buffer counts),
   - `word1 = (num_data_words & 0x3ff) | (1 << 10)` with bit 31 clear: message type 1 (reply),
     and `num_data_words = 4 + (oid ? 1 : 0)` = the CMIF reply's own size.
3. Messages without descriptors keep the in-place packet overwrite (that path already worked for
   `sm:` and `fsp-srv`), so the existing services are unaffected.

**Verification in the same handler:** after sealing, the HLE calls the guest's own
`nn::sf::hipc::detail::HipcMessageBufferAccessor::ParseHeader` (0xe8513f8) on the finished reply
and logs the result. A sealed reply reports `type=01` (previously `type=00`, i.e. a request):
`[hipc]rep2 fsp-srv parse=1 type=01 tag=00 bytes=0010 ...`.

## 7. Run evidence (after the fix)

```
SN_ENTRY=osinit timeout 15 ./build/splatnative.exe    -> exit 0
boot_20261104_004123.log                              -> 9301 lines (was 1.7M+ and timed out)
```

- The `appletOE` 0x105 retry loop is **absent**: no repeated `SendSyncRequest(h=1003 ...)` burst,
  log terminates at `[done] exit` after `svc_count=31`.
- `nn::am::InitializeApplicationInterfaces` no longer fails, so `nn::oe::Initialize` returns and
  `nnMain` advances.
- New stop, from the abort backtrace (frames `0xc9349dc`, `0xca31d9c`, `0xca2c97c`,
  `0xe7e1d34`): **`nn::fs::QueryMountRomCacheSize` (0xe7e1d34 = +0xc4) aborts inside `nnMain`**.
  That is the romfs step: its `R_ABORT_UNLESS`-style check rejects the reply our HLE returns for
  the fsp-srv mount-cache query (we return a well-formed success with zeroed output, i.e. a cache
  size of 0).

**Next:** service `fsp-srv` with real mounting semantics (`OpenSdCardFileSystem` /
`MountContent` style replies plus a real cache size) so `QueryMountRomCacheSize` and the following
`MountRom` succeed against `E:\Splatoon Games\` (`E:\Splatoon Games\`), then continue down the
`nnMain` init list the same way - one abort at a time, each with a build + run proof.

## 8. Romfs step: what the guest actually does (measured, not resolved yet)

The request that follows am init on `fsp-srv` is descriptor-carrying and now gets a type=1 reply
(framing rule of section 6 applies unchanged):

```
msg+0x00: 00000006 8000000e 00000001 00001001   header: tag 6, 14 data words, descriptors
msg+0x20: 00180001 00000000 00000000 00000000   descriptor: size 0x18, index 1
msg+0x30: 49434653 00000001 00000001 00000000   CMIF: 'SFCI', v1, command 1
```
`[hipc]rep2 fsp-srv parse=1 type=01 ...` confirms our reply framing is accepted, so the remaining
failure is **not** framing and **not** the cache-size value.

`nn::fs::QueryMountRomCacheSize` (0xe7e1c70) opens with two thunked state checks:

```
0xe7e1c88  bl 0xed47210            ; service/state check -> tbz w0,#0 -> error path
0xe7e1c94  bl 0xed477e0 (x0 = 0)   ; proxy-object check -> tbz w0,#0 -> error path
0xe7e1c9c/0xe7e1cb4  ...  bl 0xe7e2c20(out, 0, &cache_size)   ; the real fsp-srv call
0xe7e1d00  tbnz w0,#0 -> 0xe7e1d48
0xe7e1d04  cbz w19, 0xe7e1d60      ; result 0 -> use the returned size, else return error
```

Our abort frame is `0xe7e1d34`, which sits in the **error path** taken when either state check
returns false (`tbz w0,#0` -> `0xe7e1d20`). So the guest is failing before it ever asks for a cache
size: the `fsp-srv`/fs proxy state is not initialised from its point of view, and the caller in
`nnMain` (`0xca2c97c` -> `0xca31d9c` -> `0xc9349dc`) aborts on that result.

**Consequence for the next step:** romfs cannot be "answered" by returning a nonzero size. The fs
service object has to be brought up first - `nn::fs::Initialize` and the proxy acquisition must
succeed (`OpenSdCardFileSystem`/`MountContent` style replies served from real files under the game
directory), and only then does `0xe7e2c20` get reached. That is the exact code target for the next
pass, with the two thunks `0xed47210` / `0xed477e0` resolved the same way as `0xed47f00`
(runtime GOT dump: log the slot words on the first `fsp-srv` message).

## 9. fsp-srv / romfs: measured protocol and the remaining cause (this pass)

### 9.1 Reply-construction rules now in the HLE

| case | rule |
|---|---|
| descriptor-carrying msg (`word1` bit31) | drop the descriptor list, place the CMIF packet at `msg+0x10`, `word0` = tag only, `word1 = nw | (1<<10)` (type 1), `nw` = the request's declared data words for fsp-srv |
| descriptor-free CMIF msg | overwrite the packet in place, then retype the header: `word0` = tag, `word1 = nw | (1<<10)` |
| domain object id | only for `appletOE` opens and for `fsp-srv` **cmd 0** (`ConvertSessionToDomain`); written at `packet+0x10` |
| `fsp-srv` **cmd 3** (`QueryPointerBufferSize`) | returns `u16 0x1000` at `packet+0x10` |
| payload bytes | never invented. If a command's semantics are unknown the reply is a well-formed success with zeroed output |
| reply packet duplication | removed. Writing a second `SFCO` header at the decoder's guessed slots landed inside the client's output area and corrupted out-params |
| verification | every produced reply is parsed by the guest's own `HipcMessageBufferAccessor::ParseHeader` (0xe8513f8); log shows `[hipc]rep3 … type=01` / `[hipc]rep2 … type=01` |

`sm:` traffic (non-CMIF, no `SFCI` packet) and applet descriptor-free traffic are untouched by these
rules and still succeed.

### 9.2 What the fsp-srv session handshake actually is (resolved thunks, not guessed)

The session-control commands arrive as ordinary CMIF commands. Resolving the thunks by dumping
their GOT slots at runtime gave:

```
0xed4af30 -> nn::sf::hipc::client::QueryPointerBufferSize(u16*, Handle)      0xe850290
0xed4af40 -> nn::sf::hipc::client::ConvertSessionToDomain(CmifDomainObjectId*, Handle) 0xe8502a0
0xed46830 -> nn::sf::hipc::ConnectToHipcService(Handle*, const char*)        0xe856fa0
0xed48010 -> nn::fs::detail::InitializeFileSystemCreatorInterfaces()        0xe7f7d30 (a 4-byte stub)
0xed47ef0 -> nn::fs::detail::InitializeFileSystemLibrary()                  0xe7dbf70
0xed46820 -> nn::sf::hipc::InitializeHipcServiceResolution()                0xe856e20
0xed4b190 -> nn::sm::Initialize()                                           0xe857010
```

so the observed `fsp-srv` sequence is: **cmd 3** = `QueryPointerBufferSize`, **cmd 0** =
`ConvertSessionToDomain` (this is what `ConvertToDomainImpl` actually calls), then the first real
fsp-srv command (a descriptor-carrying message).

### 9.3 Why `nn::fs::QueryMountRomCacheSize` still aborts

`nn::fs::detail::GetFileSystemProxyServiceObject` (0xe7c0310) reads the cached proxy from
`0xf2883b8`; when it is null it takes a *create* path (0xe7c0418) that begins with

```
0xe7c0428  strb w8, [0xf2883a4] = 1      ; mark "creating"
0xe7c042c  ldarb w8, [0xf288400]         ; fs-init Once
0xe7c0430  tbz w8, #0 -> 0xe7c0640       ; abort
```

Measured at the first `fsp-srv` message: `0xf288400 = 1`, i.e. the Once is **in progress** (a
completed Once reads 2, whose bit0 is 0 and aborts). So this create path is only legal *while
`nn::fs` is initializing*. At the mount-cache call the Once has completed, the slot `0xf2883b8`
was never populated, the create path runs and its assert fires, reaching
`nn::diag::detail::AbortImpl(Result const*)` (0xe799ee0) from the error block at 0xe7c063c.

**Conclusion:** romfs cannot be answered by returning a cache size; the file-system proxy object
must be created *during* fs init so that `0xf2883b8` is populated. That creation is the
`QueryPointerBufferSize` / `ConvertSessionToDomain` / first-fsp-srv-command chain above, and the
remaining unknown is the exact contract of that first command's reply (the object/handle the client
wraps into the proxy). Returning a guessed payload there is what section 9.1 forbids.

### 9.4 Run evidence (final state of this pass)

```
python tools/build.py                         -> exit 0
SN_ENTRY=osinit timeout 20 ./build/splatnative.exe
log boot_20261104_010739.log                  -> 9310 lines, exit 0, no timeout, "[done] exit"
```

* No `appletOE` retry loop, no fsp retry loop (`nn::am::InitializeApplicationInterfaces` returns).
* Services exercised: `sm:` x2 (GetService "fsp-srv"), `fsp-srv` x3 (cmd 3, cmd 0, cmd 1).
* Reply framing: 6 replies parse as `type=01`, 5 requests as `type=00`.
* Stop point: `nn::fs::QueryMountRomCacheSize` -> `GetFileSystemProxyServiceObject` assert as
  analysed in 9.3 (frames `0xc9349dc`, `0xca31d9c`, `0xca2c97c`, `0xe7e1d34`, `0xe7c0378`,
  `0xe7c0640`, `0xe799d78`).

### 9.5 Next step (precise)

Populate the fs proxy during init: drive `nn::fs::detail::GetFileSystemProxyServiceObject`'s create
path once while the Once is in progress and return exactly what the client wraps - the object id /
handle produced by the first descriptor-carrying fsp-srv command - then the mount-cache query,
`MountRom` and the romfs header/partition reads can be served from real bytes under the game
folder. Instrumenting that command's client-side parse is the next concrete task.

### 9.6 Measured fs state at the mount-cache call (added after 9.5)

Log line emitted at every `sm:`/`fsp-srv` request:

```
[fsstate] fsp-srv once=01 appinit=00000000 lib=01 dfc=0 live=0
[fsstate] fsp-srv once=01 appinit=00000000 lib=01 dfc=0 live=0
[fsstate] fsp-srv once=01 appinit=00000000 lib=01 dfc=0 live=f288410
```

* `once` (`0xf288400`) = 1 -> the fs-init Once is *in progress* (a finished Once reads 2, whose
  bit 0 is clear and aborts).
* `lib` (`0xf288980`) = 1 -> `InitializeFileSystemLibrary` ran.
* `appinit` (`0xf2883b0`) = 0 -> `nn::fs::InitializeForApplication` (0xe7c13a0) never ran.
* `live` (`0xf2883c0`) = `0xf288410` -> a proxy object **was** created and stored by the normal path.
* `dfc` (`0xf2883b8`) = **0** -> the slot `nn::fs::detail::InitializeDfcFileSystemProxyServiceObject`
  (0xe7c02b0) is supposed to fill was never filled.

`GetFileSystemProxyServiceObject` consults only `0xf2883b8`. Because it is empty it clears `live`
(0xe7c03d4) and enters the create path, which asserts once init has finished. So the blocker is
precise: **the DFC file-system proxy is never created**, and it is that object the romfs
mount-cache path needs. No `sm:` request for a `dfc`-style service appears in the log at all, so
`InitializeDfcFileSystemProxyServiceObject` is never reached - it is gated on an fs initialisation
variant the guest does not take here (`appinit` stays 0, and
`InitializeFileSystemCreatorInterfaces` is a 4-byte `ret` stub in this SDK).

Next concrete task: find the caller of `InitializeDfcFileSystemProxyServiceObject` (it is reached
through a GOT thunk, so resolve thunk slots the same way as 9.2) and satisfy whatever it needs;
that populates `0xf2883b8`, the create path stops being taken, the assert disappears and
`QueryMountRomCacheSize` reaches its real fsp-srv call.

### 9.7 Complete call history (new diagnostic) and what it settles

`g_trace` was 1,024 entries; it is now 262,144 and the full ring is dumped when
`SN_TRACE_ALL=1` is set (default stays 1,024 lines so normal logs stay small). A whole boot is
only ~148k function entries, so `SN_TRACE_ALL=1` gives the *entire* call history:

```
SN_ENTRY=osinit SN_TRACE_ALL=1 ./build/splatnative.exe
-> boot_20261104_011511.log, 147,946 traced entries
```

Findings from that trace:

* `nn::fs::detail::InitializeDfcFileSystemProxyServiceObject` (0xe7c02b0) is **never called**
  (`grep -c e7c02b0` = 0). The slot it fills (`0xf2883b8`) therefore stays 0.
* `nn::fs::detail::GetFileSystemProxyServiceObject` (0xe7c0310) is first reached from
  `nn::fs::QueryMountRomCacheSize` itself:

  ```
  ca42df0 ... e7e1c70 (QueryMountRomCacheSize)
      ed47210 -> e7f2c30 (IsEnabledAccessLog)
      ed47d10 -> e7f2910
      e7e2c20 -> e7e2d90
          ed47800 -> e7c0310 (GetFileSystemProxyServiceObject)   <- first call ever
  ```

  so nothing initialised the fs proxy earlier; the mount-cache call itself is the first user.
* The create path then runs `InitializeFileSystemLibrary` (0xe7dbf70) and
  `InitializeFileSystemCreatorInterfaces` (0xe7f7d30, a `ret` stub) and dies before reaching
  `InitializeHipcServiceResolution` (0xe856e20) / `ConnectToHipcService` (0xe856fa0) - neither
  appears in the trace after the create path starts, so no `sm:` request is issued and the
  failure is inside the create path's own state machine, not in our reply to the first fsp-srv
  command.

Reusable commands:

```
SN_ENTRY=osinit ./build/splatnative.exe              # 9.3k lines, fast
SN_ENTRY=osinit SN_TRACE_ALL=1 ./build/splatnative.exe  # full 148k-entry call history
grep -n "\[trace\]   <pc>" <log>                      # where a function is entered, in order
```

### 9.8 FIXED: the CMIF reply position (this was the whole `0x1a80a` class of failure)

`0x1a80a` (nn::sf "invalid response") was never a service-semantics problem. It is produced by
`nn::sf` when the reply magic is not `SFCO` at the offset the client decodes. Two facts pinned it:

1. The failing call chain was `nn::fs::QueryMountRomCacheSize` -> `GetFileSystemProxyServiceObject`
   create path -> `0xe7c441c(proxy+8, 0, 1)`, whose only `0x1a80a` assignment is the
   "reply magic mismatch" arm at `0xe7c4518`. The BREAK payload recorded in the log
   (`x1 u32=0001a80a`) is that same Result.
2. The client does NOT read the magic where we wrote it. It re-parses the echoed hipc header with
   sdk `0xe799a5c`, which stores a packed HeaderInfo at struct+0x1a; the top byte of that word
   (struct+0x21) is a word count, and the client reads the magic at
   `align(msg + count*4) + 0x10`.

The reply builder used the *request's* packet offset for the reply. For a descriptor-carrying
request the packet sits at `msg+0x30`, so `SFCO` landed at `msg+0x40` while the client looked at
`msg+0x20`. For the fs proxy's own domain message (packet at `msg+0x20`) it landed at `0x30`
instead of `0x20`. Observed shapes and the rule now implemented:

| request packet | descriptors | reply magic goes at |
|---|---|---|
| `msg+0x10` (plain cmif, e.g. fsp-srv cmd 3) | no | `msg+0x10` |
| `msg+0x10` (plain cmif, e.g. fsp-srv cmd 0 ConvertToDomain) | no | `msg+0x10` |
| `msg+0x30` (packet after a 0x20-byte descriptor list) | yes | `msg+0x20` |
| `msg+0x20` (factory/domain message) | no | `msg+0x20` |

i.e. the magic goes where the request packet was, minus one 16-byte block when the reply drops the
descriptor list. The descriptor data area is zeroed, the echoed header is resealed as a reply
(`word1 = (nw & 0x3ff) | (1<<10)`, bit 31 clear) *before* the offset is derived, and the parser's
own answer (`gh_hipc_reply_base`, sdk `0xe799a5c`) is logged next to it.

Result on this boot (plain `SN_ENTRY=osinit`, log `boot_20261104_014905.log`):

```
[svc]   reply base from req off: off=10 desc=0 base=0  parsed=0
[svc]   cmif reply on 'fsp-srv': req pkt tls+0x10 desc=0 nw=2 base=tls+0x0  oid=0
[svc]   cmif reply on 'fsp-srv': req pkt tls+0x10 desc=0 nw=3 base=tls+0x0  oid=100   (ConvertToDomain)
[svc]   cmif reply on 'fsp-srv': req pkt tls+0x30 desc=1 nw=e base=tls+0x10 oid=0
[svc]   cmif reply on 'fsp-srv': req pkt tls+0x20 desc=0 nw=2 base=tls+0x10 oid=0     (cmd 0xc8)
```

4 CMIF replies, all parsing `type=01`, no retry loops, and **`0x1a80a` is gone from the log**.
`nn::fs::QueryMountRomCacheSize` now returns from its proxy call instead of aborting.

### 9.9 New stop point: fs still needs a real romfs (this is the gate blocker)

With the reply protocol correct, the very next step dereferences data the romfs reply should have
produced. The run now stops on a genuine guest null dereference, reported precisely and cleanly:

```
[mem] guest fault: unmapped rd a=0 n=8 pc=e7b7f44 lr=e7f6e74
[fp] chain ... lr=ca2c97c (main) ...
[done] exit
```

`0xe7b7f44` is a forwarding stub (`impl = [this+8]; impl->vtable[0x20](...)`) and `this+8` is null:
the object the fs layer built from the (correctly) zeroed cache-size reply has no implementation
attached. That is the honest consequence of returning a well-formed success with zeroed output for
a query whose real answer lives in the game's romfs.

`a64_small_addr_dbg` now converts an unmapped low-address access into that logged guest fault and a
clean `exit 0` with the trace dumped, instead of letting the host take an access violation (the
previous behaviour segfaulted here with exit code 139).

To go further the runtime has to serve the real content: mount `rom:` from the game folder
(`E:\Splatoon Games\`), answer the mount-cache query with the real cache size, and hand back real
file bytes for the first requested path. That is the deferred "step 2" and it is the current gate
blocker on the guest side.

### 9.10 Offline GOT/thunk resolver (new tool)

`tools/slotaddr.py` resolves a GOT slot to its linked target from the MOD0 relocation tables, which
finally names the whole thunk region (`0xed4....`) that dynsym cannot resolve:

```
python tools/slotaddr.py 0xf26d8f0 0xf26cd58 0xf26c708
f26d8f0: type=0x402 name=_ZN2nn2sf4hipc6client24HipcClientSessionManager16CreateNewSessionEPNS_3svc6HandleE target=0xe85022c
f26cd58: type=0x402 name=_ZN2nn2sf4hipc20ConnectToHipcServiceEPNS_3svc6HandleEPKc target=0xe856fa0
f26c708: type=0x402 name=_ZN2nn4diag6detail9AbortImplEPKNS_6ResultE target=0xe799ee0
```

This is what identified `0xed45320` as `__cxa_guard_acquire` (the "fs init Once" at `0xf288400` is
really a C++ static-init guard), `0xed47f60` as `HipcClientSessionManager::CreateNewSession`
(never called on this boot) and `0xed45b90` as `nn::diag::detail::AbortImpl`.

### 9.11 romfs content model wired in (Step 1 progress)

`gh_romfs_meta_size()` measures the local extracted tree once at first use
(`SN_ROMFS`, default `E:\Splatoon Games`) and answers fsp-srv cmd 0xc8 with a
real derived mount-cache size. Verified `boot_20261104_111637.log` (9332 lines,
422,896 B): files=23425 dirs=169 bytes=39,660,698,754 meta=753736, and
`[romfs] fsp-srv cmd 0xc8 -> mount cache size 753736`. The guest then creates a
second fs object (oid=101) instead of faulting at `e7b7f44` - that null-forwarder
stop is closed.

Next stop: the domain reply for that oid=101 message (request packet at
`msg+0x30`, no descriptors) still yields `0x1a80a` at `e7e1d84`
(QueryMountRomCacheSize). Using the header parser's own offset for that message
gets past it but sends the guest into an unbounded appletOE object-creation loop
(12.8M-line log, `boot_20261104_111356.log`, killed at 120 s), so the parser
offset alone is not the rule either - the domain reply needs its own
object-id/scaling semantics (likely the sf `Out` object table entry), which is
the next item.

## 10. NVN -> DX12 stack: D3D12 half built and verified (Step 3 started)

`src/runtime/nvn_d3d12.cpp` (new TU, wired into `tools/build.py` with
d3d12/dxgi/d3dcompiler/user32/gdi32/ole32) is the bottom half of the graphics
stack the NVN layer will drive: D3D12 device on the real adapter, a
flip-model DXGI swapchain on a real window, RTV heap, command allocator/list,
fence, and the per-frame CSV recorder.  The NVN API surface (device / window /
swapchain / command buffer / present) will be implemented on top of exactly
these calls, so anything reaching NVN presents through this file; that API
surface itself is NOT written yet - this commit is the D3D12 half only.

Verified on this machine (`SN_NVN_SMOKE=60`, log `boot_20261104_114647.log`):
```
[dx12] window 1280x720 hwnd=0000000001840DBE
[dx12] adapter: AMD Radeon RX 5700 XT
[dx12] device ready: queue=fence ok, swapchain 1280x720 x2
[dx12] init rc=0
[dx12] smoke frames=60 csv=bench_20261104_114648.csv
[dx12] CSV closed: bench_20261104_114648.csv (60 frames, 60 presents)
```
`bench_20261104_114648.csv`, 2025 B, 60 data rows, header
`frame,frame_time_us,cpu_time_us,draw_calls,presents,width,height`, first row
`1,58584.5,58584.5,0,1,1280,720`.  Every row is written after
ExecuteCommandLists+Present returned for that frame; the smoke colour is not
the title, so this is the pipeline proof, not a Gate 2 pass.

## 11. Diagnostic tools added (runtime only, no gen changes)

- `SN_MARK=<hex>`: log registers + the pc ring at one address and stop.  Only
  block-entry addresses fire (dispatch is per basic block), so mid-function
  probes miss.
- `SN_COV=<lo>-<hi>`: one `[cov]` line per distinct dispatched pc in a range.
- `SN_BIGTRACE=1`: a 65536-entry pc ring maintained in `host_dispatch`
  (runtime side; the generated ring is only 1024 and its dump mask was
  mismatched, so it printed zeros - fixed to read the same ring it writes).
- `a64_dump_trace` mask fix and `gh_hipc_magic_off`, which replays the client's
  own check (`sdk 0xe799a2c`/`0xe7c44d8`): magic must equal 0x4f434653 at
  `align16(parse_state.ptr + count*4) + 0x10`, and 0x1a80a is what the SDK
  returns when that compare fails.

## 12. Current stop (unchanged, now instrumented)

The no-`SFCI` fsp-srv message that follows `QueryMountRomCacheSize`'s size
query is answered at the offset the client-side lookup reports
(`[romfs] fsp-srv size query: SFCO at tls+%x sizes at tls+%x`), and that
lookup then reports the magic present and correct
(`[hipc] magic lookup: ... magic=4f434653 OK`), yet the call still returns
0x1a80a and `QueryMountRomCacheSize` aborts at `e7e1d84`.  Planting the magic
at 0x10/0x20/0x30/0x40 in one run changed nothing, so this message's rejection
is not the reply-magic compare; the bigtrace shows the failing work happening
after the reply parse (`e7d6170`, `e7d52e0`) inside the mount attempt, before
`e7f445c` aborts.  Next: identify what that mount call wants (its payload
carries the 753736 cache size twice) instead of the magic.
