#!/usr/bin/env python3
"""SplatNative static recompiler driver.

Splits each program NSO (sdk, main) into functions via eh_frame_hdr FDEs,
recompiles each function to C with the capstone-driven emitter, and writes
sharded C files plus build/gen/recomp_meta.c (dispatch table, reloc patches,
init arrays, entry point).

Address model: NSO headers are image-relative. The runtime loads each module
at the same absolute base Ryujinx used in the verified boot log, and the
generator bakes those bases into every runtime-facing address (disassembly
base, reloc patches, symbols, dispatch table).

Game bytes stay on disk (E:\\S3_extract); the runtime loader reads the NSOs
directly. No keys are read here.
"""
import argparse
import glob
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "recomp"))

import nso_scan  # noqa: E402  (parsers reused from the module scanner)
from a64 import FnEmitter  # noqa: E402

EXTRACT = r"E:\S3_extract"
BUILD = os.path.join(os.path.dirname(HERE), "build", "gen")
os.makedirs(BUILD, exist_ok=True)

MODULES = ["rtld", "sdk", "main"]

# Absolute runtime load bases, identical to the Ryujinx Canary 1.3.351 boot
# log for Splatoon 3 v11.2.0 (evidence/ryujinx_boot_2026-10-30_1555.log).
LOAD_BASE = {"rtld": 0x08500000, "main": 0x08506000, "sdk": 0x0E6FE000}

SHARD = 6000


def load_module(name):
    nso = nso_scan.Nso(os.path.join(EXTRACT, name))
    meta, dyn = nso_scan.parse_mod0_dynamic(nso)
    syms, smeta, err = nso_scan.parse_dynsyms(nso, dyn)
    if err:
        syms = []
    fdes = nso_scan.eh_frame_table(nso, meta)
    return {"name": name, "nso": nso, "meta": meta, "dyn": dyn,
            "syms": syms or [], "fdes": fdes, "base": LOAD_BASE[name]}


def functions_of(mod, with_extra=True):
    """FDE ranges clipped to .text, deduped, no merging (each FDE = one emitted
    function so a gap inside one function cannot suppress its neighbours).
    with_extra adds PLT stubs and indirect-jump targets discovered by scan_plt."""
    nso = mod["nso"]
    t = nso.segs[0]
    tlo, thi = t["mem_off"], t["mem_off"] + t["size"]
    out = {}
    for loc, size in mod["fdes"]:
        s, e = loc, loc + size
        if e <= tlo or s >= thi:
            continue
        s, e = max(s, tlo), min(e, thi)
        if e - s >= 4:  # single-instruction trampolines are valid functions
            out[s] = e
    if with_extra:
        for s in sorted(mod.get("extra_starts", ())):
            if tlo <= s < thi - 4 and s not in out:
                out[s] = thi
    starts = sorted(out)
    fns = []
    for idx, s in enumerate(starts):
        e = out[s]
        if idx + 1 < len(starts):
            e = min(e, starts[idx + 1])
        if e - s >= 4:
            fns.append((s, e))
    return fns


def scan_bl(mod):
    """Direct BL call targets. FDEs miss hand-written/asm callees; every BL
    target is a function entry by definition, so add them as starts."""
    import array
    nso = mod["nso"]
    t = nso.segs[0]
    text = bytes(nso.mem[t["mem_off"]])
    arr = array.array("I")
    arr.frombytes(text[: len(text) & ~3])
    out = set()
    for i, w in enumerate(arr):
        if w & 0xFC000000 == 0x94000000:  # BL imm26
            off = (w & 0x03FFFFFF) << 2
            if off & 0x08000000:
                off -= 0x10000000
            tgt = i * 4 + off
            if 0 <= tgt < len(arr) * 4 - 4:
                out.add(t["mem_off"] + tgt)
    return out


def scan_jump_tables(mod):
    """Computed-branch (BR) jump-table targets. A switch dispatcher loads a case
    target from a table then `br`s to it; those targets are reached only by the
    computed branch, never a BL, so scan_bl + eh_frame miss them (observed:
    [unmapped] pc=ec7d294 STOP). Two layouts:
      absolute: ldr xT,[tbl,xIdx,lsl#3]; br xT   -> target = table word (module offset)
      pcrel:    add xT,xBase,xOff,lsl#2; br xT   -> target = xBase + sext(off)*4
                where xOff comes from ldrsb/sh/sw [tbl + xIdx].
    Bounds from the guard `subs` immediate when present, else a capped
    validity-driven read; every target is checked to lie in .text and be
    instruction-aligned. Returns image-relative (vaddr) starts, same space as
    scan_bl. BLR (vtable) targets are data-driven via relocations and skipped."""
    import array
    nso = mod["nso"]
    t = nso.segs[0]
    tlo, thi = t["mem_off"], t["mem_off"] + t["size"]
    text = bytes(nso.mem[t["mem_off"]])
    arr = array.array("I")
    arr.frombytes(text[: len(text) & ~3])
    pc_of = lambda k: tlo + k * 4  # noqa: E731

    def adrp_page(pc, w):
        immlo = (w >> 29) & 3; immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & 0x100000: imm -= 0x200000
        return (pc & ~0xFFF) + (imm << 12)

    def adr_addr(pc, w):
        immlo = (w >> 29) & 3; immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & 0x100000: imm -= 0x200000
        return pc + imm

    def resolve_addr(idx, reg):
        for p in range(idx, max(idx - 8, 0) - 1, -1):
            w = arr[p]; rd = w & 31
            if rd != reg:
                continue
            if (w & 0x9F000000) == 0x90000000:      # ADRP
                page = adrp_page(pc_of(p), w)
                for q in range(p + 1, min(p + 4, idx + 1)):
                    w2 = arr[q]
                    if (w2 & 0xFFC00000) == 0x91000000 and (w2 & 31) == reg and ((w2 >> 5) & 31) == reg:
                        return page + ((w2 >> 10) & 0xFFF)
                return page
            if (w & 0xFF000000) == 0x10000000:      # ADR
                return adr_addr(pc_of(p), w)
        return None

    def is_indexed_load(w):
        top = (w >> 24) & 0xFF
        return top in (0x38, 0x78, 0xB8, 0xF8, 0x39, 0x79, 0xB9, 0xF9) and (w & 0x00200000) != 0

    def load_fmt(w):
        top = (w >> 24) & 0xFF
        size = {0x38: 1, 0x39: 1, 0x78: 2, 0x79: 2, 0xB8: 4, 0xB9: 4, 0xF8: 8, 0xF9: 8}[top]
        signed = top in (0x38, 0x39, 0x78, 0x79, 0xB8, 0xB9) and ((w >> 16) & 1) == 0
        return size, signed

    def guard_count(i):
        for p in range(i - 1, max(i - 16, 0) - 1, -1):
            w = arr[p]
            if (w & 0xFF800000) == 0x71000000:      # SUBS imm32 (cmp form)
                return ((w >> 10) & 0xFFF) + 1
        return None

    starts = set()
    for i in range(4, len(arr)):
        if (arr[i] & 0xFFFFFC1F) != 0xD61F0000:     # BR
            continue
        rt = (arr[i] >> 5) & 31                     # BR target reg at bits [9:5]
        # skip PLT resolver stubs (adrp;ldr;add;br) — scan_plt already seeds those
        if i >= 3:
            w0, w1, w2 = arr[i - 3], arr[i - 2], arr[i - 1]
            if (w0 & 0x9F000000) == 0x90000000 and (w1 & 0xFFC00000) == 0xF9400000 \
               and (w2 & 0xFFC00000) == 0x91000000 and (w0 & 31) == ((w1 >> 5) & 31) \
               and (w1 & 31) == rt:
                continue
        last = None
        for p in range(i - 1, max(i - 6, 0) - 1, -1):
            if (arr[p] & 31) == rt:
                last = p; break
        if last is None:
            continue
        wl = arr[last]
        # Case A: 64-bit register-offset ldr writes rt, then br rt
        if is_indexed_load(wl) and (wl & 31) == rt and ((wl >> 30) & 3) == 3:
            rn = (wl >> 5) & 31
            tbl = resolve_addr(last, rn)
            if tbl is None or nso.v2file(tbl) is None:
                continue
            size, _signed = load_fmt(wl)
            if size != 8:
                continue
            cnt = max(1, min(guard_count(last) or 512, 4096))
            fo = nso.v2file(tbl)
            for k in range(cnt):
                o = fo + k * 8
                if o + 8 > len(nso.image):
                    break
                v = struct.unpack_from("<Q", nso.image, o)[0] & 0xFFFFFFFF
                if tlo <= v < thi - 4 and (v & 3) == 0:
                    starts.add(v)
            continue
        # Case B: add xT,xBase,xOff,lsl#2 writes rt, then br rt
        if (wl & 0xFFA00000) == 0x8B000000 and ((wl >> 10) & 3) == 2:
            rn = (wl >> 5) & 31; rm = (wl >> 16) & 31
            base = resolve_addr(last, rn)
            if base is None or not (tlo <= base < thi):
                continue
            lseek = None
            for p in range(last - 1, max(last - 8, 0) - 1, -1):
                if (arr[p] & 31) == rm and is_indexed_load(arr[p]):
                    lseek = p; break
            if lseek is None:
                continue
            size, signed = load_fmt(arr[lseek])
            tbl = resolve_addr(lseek, (arr[lseek] >> 5) & 31)
            if tbl is None or nso.v2file(tbl) is None:
                continue
            cnt = max(1, min(guard_count(last) or 512, 4096))
            fo = nso.v2file(tbl)
            for k in range(cnt):
                o = fo + k * size
                if o + size > len(nso.image):
                    break
                if size == 1:
                    v = nso.image[o]
                    if signed and v & 0x80: v -= 0x100
                elif size == 2:
                    v = struct.unpack_from("<H", nso.image, o)[0]
                    if signed and v & 0x8000: v -= 0x10000
                elif size == 4:
                    v = struct.unpack_from("<I", nso.image, o)[0]
                    if signed and v & 0x80000000: v -= 0x100000000
                else:
                    break
                tgt = base + v * 4
                if tlo <= tgt < thi - 4 and (tgt & 3) == 0:
                    starts.add(tgt)
            continue
    return starts


def scan_plt(mod, patch_map, host_offs):
    """PLT resolver stubs: adrp xN; ldr xM,[xN,#imm]; add xN,xN,#imm; br xM.
    Reached only by indirect branch, so they carry no FDE and no BL caller.
    Returns (image-relative stub starts, absolute jump targets)."""
    nso = mod["nso"]
    t = nso.segs[0]
    text = bytes(nso.mem[t["mem_off"]])
    base = mod["base"]
    stubs = []
    targets = set()
    for off in range(0, len(text) - 16, 4):
        w0, w1, w2, w3 = struct.unpack_from("<IIII", text, off)
        if w3 & 0xFFFFFC1F != 0xD61F0000:  # br xm
            continue
        if w0 & 0x9F000000 != 0x90000000:  # adrp
            continue
        if w1 & 0xFFC00000 != 0xF9400000:  # ldr xt,[xn,#u12*8]
            continue
        if w2 & 0xFFC00000 != 0x91000000:  # add xn,xn,#u12
            continue
        if (w0 & 0x1F) != ((w1 >> 5) & 0x1F):
            continue
        if (w1 & 0x1F) != ((w3 >> 5) & 0x1F):
            continue
        rel = t["mem_off"] + off
        stubs.append(rel)
        va = base + rel
        imm21 = (((w0 >> 5) & 0x7FFFF) << 2) | ((w0 >> 29) & 0x3)
        if imm21 & 0x100000:
            imm21 -= 0x200000
        slot = (va & ~0xFFF) + (imm21 << 12) + (((w1 >> 10) & 0xFFF) << 3)
        if slot in host_offs:
            continue  # host-bound: host_dispatch handles it
        tgt = patch_map.get(slot)
        if tgt is not None:
            targets.add(tgt)
    return stubs, targets


def emit_module(mod, fns, prefix):
    nso = mod["nso"]
    base = mod["base"]
    toff = nso.segs[0]["mem_off"]
    text = nso.mem[toff]
    bodies = []
    gaps = []
    total_bytes = 0
    for s, e in fns:
        em = FnEmitter(base + s, base + e)
        body = em.run(text[s - toff: e - toff], base + s)
        total_bytes += (e - s)
        bodies.append((s, e, body, em.gaps))
        gaps.extend(em.gaps)
    written = []
    for shard_i in range(0, len(bodies), SHARD):
        chunk = bodies[shard_i: shard_i + SHARD]
        path = os.path.join(BUILD, f"{prefix}_{shard_i // SHARD:03d}.c")
        with open(path, "w") as f:
            f.write(f"/* generated by SplatNative gen.py from {mod['name']} NSO - do not edit */\n")
            f.write('#include "a64_ctx.h"\n\n')
            for s, e, body, g in chunk:
                f.write(f"void fn_{base + s:x}(A64Ctx* c);\n")
            f.write("\n")
            for s, e, body, g in chunk:
                f.write(f"void fn_{base + s:x}(A64Ctx* c) {{\n")
                f.write(body)
                f.write("\n}\n\n")
        written.append((path, os.path.getsize(path)))
    return {"fns": bodies, "gaps": gaps, "ranges": fns,
            "total_bytes": total_bytes, "written": written}


def c_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="limit functions per module")
    ap.add_argument("--reuse", action="store_true",
                    help="reuse existing shard bodies, emit only new functions")
    args = ap.parse_args()

    mods = [load_module(n) for n in MODULES]
    report = {"modules": {}, "load_base": {k: hex(v) for k, v in LOAD_BASE.items()}}

    # symbol resolution map: name -> absolute runtime address of its export
    sym_addr = {}
    for mod in mods:
        for s in mod["syms"]:
            if s["name"] and s["shndx"] != 0:
                sym_addr.setdefault(s["name"], mod["base"] + s["value"])

    # relocations are computed before emission: PLT target resolution needs them
    # AArch64: RELATIVE(0x403), GLOB_DAT(0x401), JUMP_SLOT(0x402), ABS64(0x101)
    RT, GD, JS, ABS64 = 0x403, 0x401, 0x402, 0x101
    patches = []       # (runtime offset, value)  baked at gen time
    host_patches = []  # (runtime offset, name)   resolved by the loader
    for mod in mods:
        nso = mod["nso"]
        d = dict(mod["dyn"])
        relas = []
        for key_off, key_sz in ((7, 8), (23, 2)):   # DT_RELA/DT_RELASZ, DT_JMPREL/DT_PLTRELSZ
            v, sz = d.get(key_off), d.get(key_sz, 0)
            off = nso.v2file(v) if v else None
            if off is None:
                continue
            for o in range(off, off + sz, 24):
                r_off, r_info, r_add = struct.unpack_from("<QQq", nso.image, o)
                relas.append((r_off, r_info, r_add))
        n_rel = n_host = n_loc = 0
        for r_off, r_info, r_add in relas:
            typ = r_info & 0xFFFFFFFF
            symi = r_info >> 32
            rtoff = mod["base"] + r_off
            if typ == RT:
                patches.append((rtoff, (mod["base"] + r_add) & 0xFFFFFFFFFFFFFFFF))
                n_rel += 1
                continue
            if typ not in (GD, JS, ABS64):
                continue
            s = mod["syms"][symi] if symi < len(mod["syms"]) else None
            name = s["name"] if s else None
            if name and s["shndx"] != 0:
                patches.append((rtoff, (mod["base"] + s["value"] + r_add)
                                & 0xFFFFFFFFFFFFFFFF))
                n_loc += 1
            elif name and name in sym_addr:
                patches.append((rtoff, (sym_addr[name] + r_add)
                                & 0xFFFFFFFFFFFFFFFF))
                n_loc += 1
            elif name:
                host_patches.append((rtoff, name))
                n_host += 1
            else:
                # unnamed reloc: base-relative addend (ELF *slot = base + A)
                patches.append((rtoff, (mod["base"] + r_add) & 0xFFFFFFFFFFFFFFFF))
                n_loc += 1
        # DT_RELR compressed relative relocations (DT_RELR=0x24, DT_RELRSZ=0x23;
        # entries are BYTE offsets, odd entries are 63-bit run bitmaps). These
        # are applied by nso_loader.c at load time. This block previously baked
        # them with the wrong size tag (0x25 = DT_RELRENT) and word-index units,
        # which silently left ~544k pointer slots unrebased and made every
        # vtable call jump to a raw relative address ([unmapped] pc).
        # Count here (correctly) for the report only.
        n_relr = 0
        rv, rsz = d.get(0x24), d.get(0x23, 0)
        ro = nso.v2file(rv) if rv else None
        if ro is not None and rsz:
            where = None
            for o in range(ro, ro + rsz, 8):
                e = struct.unpack_from("<Q", nso.image, o)[0]
                if e & 1:
                    if where is None:
                        continue
                    n_relr += sum(1 for i in range(1, 64) if (e >> i) & 1)
                    where += 63 * 8
                else:
                    where = e
                    n_relr += 1
        report["modules"].setdefault(mod["name"], {})["relocs"] = {
            "total": len(relas), "baked": n_loc + n_rel, "host": n_host,
            "relr": n_relr}
        mod["host_patches"] = list(host_patches[len(host_patches) - n_host:]) if n_host else []
        # init_array: image-relative -> runtime.
        # rtld is excluded: we pre-applied every module's relocations, so the
        # loader's own init would only re-process them.
        mod["init_array"] = []
        if 25 in d and mod["name"] != "rtld":
            ia = nso.v2file(d[25])
            iasz = d.get(27, 0)
            if ia is not None:
                for o in range(ia, ia + iasz, 8):
                    v = struct.unpack_from("<Q", nso.image, o)[0]
                    if v:
                        mod["init_array"].append(mod["base"] + v)

    # ---- PLT / indirect-entry discovery ----
    patch_map = dict(patches)
    host_offs = {off for off, _ in host_patches}
    plt = {}
    all_targets = set()
    for mod in mods:
        stubs, tgts = scan_plt(mod, patch_map, host_offs)
        plt[mod["name"]] = stubs
        all_targets |= tgts
    for mod in mods:
        t = mod["nso"].segs[0]
        lo = mod["base"] + t["mem_off"]
        hi = lo + t["size"]
        extra = set(plt[mod["name"]])
        extra |= scan_bl(mod)
        njt = scan_jump_tables(mod)
        extra |= njt
        extra |= {int(tg) - mod["base"] for tg in all_targets if lo <= tg < hi}
        # Cross-range branch targets from tools/scan_crossers.py (fixpoint):
        # a direct branch that crosses a function boundary into an address
        # that is not a start dispatches to nothing and STOPs the run
        # (observed: b.ne ecccc24 -> ecccc48, split by a jump-table seed).
        # Seeding those targets keeps every cross-boundary branch resolvable.
        ncx = 0
        cxp = os.path.join(HERE, f"crossers_{mod['name']}.json")
        if os.path.exists(cxp):
            with open(cxp) as cf:
                cx = {int(v) for v in json.load(cf)}
            extra |= cx
            ncx = len(cx)
        mod["extra_starts"] = extra
        print(f"[gen] {mod['name']}: {len(plt[mod['name']])} PLT stubs, "
              f"{len(njt)} jump-table targets, {ncx} cross-range targets, "
              f"{len(extra)} extra starts total")

    # ---- emission ----
    all_fn_addrs = set()
    for mod in mods:
        print(f"[gen] {mod['name']}: {len(mod['fdes'])} FDEs, "
              f"{len(mod['syms'])} dynsyms, base {mod['base']:#x} ...")
        fns_new = functions_of(mod)
        mpath = os.path.join(BUILD, f"{mod['name']}_manifest.json")
        on_disk = {}
        if os.path.exists(mpath):
            try:
                with open(mpath) as mf:
                    on_disk = {int(k): int(v) for k, v in json.load(mf).items()}
            except Exception:
                on_disk = {}
        # reuse only when a manifest proves which bodies exist on disk
        full = (not args.reuse) or (not on_disk)
        prefix, fns_emit = f"{mod['name']}_gen", fns_new
        if not full:
            changed = [x for x in fns_new
                       if x[0] in on_disk and on_disk[x[0]] != x[1]]
            if changed:
                print(f"[gen] {mod['name']}: {len(changed)} ranges changed "
                      f"-> full re-emit")
                full = True
            else:
                fns_emit = [x for x in fns_new if x[0] not in on_disk]
                # never overwrite shards holding manifest-covered bodies
                idx = 0
                while glob.glob(os.path.join(BUILD,
                                             f"{mod['name']}_xtra{idx:02d}_*.c")):
                    idx += 1
                prefix = f"{mod['name']}_xtra{idx:02d}"
                print(f"[gen] {mod['name']}: manifest ok, emitting "
                      f"{len(fns_emit)} new functions as {prefix}_*")
        else:
            print(f"[gen] {mod['name']}: "
                  + ("no manifest -> " if args.reuse else "")
                  + f"full re-emit of {len(fns_new)} functions")
        if full:
            for p in glob.glob(os.path.join(BUILD, f"{mod['name']}_*.c")):
                os.remove(p)
            if os.path.exists(mpath):
                os.remove(mpath)
        r = emit_module(mod, fns_emit, prefix)
        whyc = {}
        for g in r["gaps"]:
            k = f"{g[2]}: {g[3]}"
            whyc[k] = whyc.get(k, 0) + 1
        report["modules"].setdefault(mod["name"], {}).update({
            "functions": len(fns_new),
            "emitted": len(fns_emit),
            "text_bytes_in_fns": r["total_bytes"],
            "gap_mnemonics": sorted({g[2] for g in r["gaps"]}),
            "gap_count": len(r["gaps"]),
            "gap_whys": [[k, v] for k, v in
                         sorted(whyc.items(), key=lambda kv: -kv[1])[:40]],
            "gen_files": len(r["written"]),
            "gen_bytes": sum(s for _, s in r["written"]),
        })
        all_fn_addrs.update(mod["base"] + s for s, e in fns_new)
        for path, size in r["written"]:
            print(f"[gen] wrote {path} ({size} B)")
        print(f"[gen] {mod['name']}: gaps={len(r['gaps'])}")
        mod["emit"] = r
        # record what is now on disk: reused bodies + newly emitted
        with open(mpath, "w") as mf:
            json.dump({str(s): e for s, e in fns_new}, mf)

    entry = sym_addr.get("nnMain")
    report["entry_nnMain"] = hex(entry) if entry else None
    host_names = sorted({n for _, n in host_patches})
    report["host_symbols"] = host_names

    # ---- recomp_meta.c ----
    path = os.path.join(BUILD, "recomp_meta.c")
    with open(path, "w") as f:
        f.write("/* generated - dispatch + load metadata */\n")
        f.write('#include "a64_ctx.h"\n\n')
        # forward declarations for every emitted function
        addrs = sorted(all_fn_addrs)
        decls = [f"void fn_{a:x}(A64Ctx* c);" for a in addrs]
        for i in range(0, len(decls), 6):
            f.write(" ".join(decls[i:i + 6]) + "\n")
        f.write("\nconst FnEntry g_fn_table[] = {\n")
        for a in addrs:
            f.write(f"    {{ {a:#x}ull, fn_{a:x} }},\n")
        f.write("};\n")
        f.write(f"const unsigned g_fn_count = {len(addrs)};\n\n")
        f.write("void fn_dispatch(A64Ctx* c) {\n")
        f.write("    if (host_dispatch(c)) return;\n")
        f.write("    g_trace[g_trace_i++ & 1023u] = c->pc;\n")
        f.write("    if (g_watch && c->pc == g_watch) a64_hit_watch(c);\n")
        f.write("    unsigned lo = 0, hi = g_fn_count;\n")
        f.write("    while (lo < hi) { unsigned mid = lo + (hi - lo) / 2;\n")
        f.write("        if (g_fn_table[mid].pc < c->pc) lo = mid + 1; else hi = mid; }\n")
        f.write("    if (lo < g_fn_count && g_fn_table[lo].pc == c->pc) {\n")
        f.write("        g_fn_table[lo].fn(c); return;\n    }\n")
        f.write("    a64_unmapped(c); return;\n}\n\n")
        # reloc patches
        f.write("const RecPatch g_patches[] = {\n")
        for off, val in patches:
            f.write(f"    {{ {off:#x}ull, {val:#x}ull }},\n")
        f.write("};\n")
        f.write(f"const unsigned g_patch_count = {len(patches)};\n\n")
        f.write("const HostPatch g_host_patches[] = {\n")
        for off, name in host_patches:
            f.write(f"    {{ {off:#x}ull, {c_str(name)} }},\n")
        f.write("};\n")
        f.write(f"const unsigned g_host_patch_count = {len(host_patches)};\n\n")
        f.write("const unsigned long long g_init_arrays[][2] = {\n")
        for mod in mods:
            f.write(f"    /* {mod['name']} */\n")
            for a in mod["init_array"]:
                f.write(f"    {{ 1, {a:#x}ull }},\n")
        f.write("    { 0, 0 }\n};\n")
        f.write(f"const unsigned long long g_entry = "
                f"{entry:#x}ull;\n" if entry else "const unsigned long long g_entry = 0;\n")

    size = os.path.getsize(path)
    print(f"[gen] wrote {path} ({size} B) patches={len(patches)} host={len(host_patches)}")
    report["meta_c"] = size

    rpath = os.path.join(BUILD, "gen_report.json")
    with open(rpath, "w") as f:
        json.dump(report, f, indent=1)
    print(f"[gen] report: {rpath}")
    for mn, info in report["modules"].items():
        print(f"  {mn}: fns={info['functions']} gaps={info['gap_count']} "
              f"files={info['gen_files']} bytes={info['gen_bytes']} "
              f"gap_mnemonics={info['gap_mnemonics'][:20]}")


if __name__ == "__main__":
    main()
