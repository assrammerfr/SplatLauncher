#!/usr/bin/env python3
"""Prototype: recover computed-branch (BR) jump-table targets.

For each non-PLT `br xT` in .text, trace xT backward:
  Case A (absolute):  ldr xT,[xTbl,xIdx,lsl#3]; br xT   -> target = base + u64_entry
  Case B (PC-relative): add xT,xBase,xOff,lsl#2 ; br xT  -> target = case_base + sextp(entry)*4
     where xOff comes from ldrsb/ldrsh/ldrsw (signed) or ldrb/ldrh/ldrw (unsigned)
     indexed load from a table, and xBase from adr / adrp+add.
Table bounds from the guard `subs`/`cmp` immediate when present, else validity-driven.
Returns image-relative target offsets. Validates on sdk: must include ec7d294.
"""
import sys, struct, array
sys.path.insert(0, "E:/SplatNative/tools")
from nso_scan import Nso


def is_br(w):   return (w & 0xFFFFFC1F) == 0xD61F0000


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


def resolve_addr(arr, idx, reg, pc_of):
    """Backward-trace reg to an ADR (direct) or ADRP+ADD (page+off). Image-relative."""
    for p in range(idx, max(idx - 8, 0) - 1, -1):
        w = arr[p]; rd = w & 31
        if rd != reg: continue
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
    if top in (0x38, 0x78, 0xB8, 0xF8, 0x39, 0x79, 0xB9, 0xF9):
        return (w & 0x00200000) != 0   # register-offset form
    return False


def load_fmt(w):
    """(size_bytes, signed, scale_shift_from_lsl)."""
    top = (w >> 24) & 0xFF
    size = {0x38: 1, 0x39: 1, 0x78: 2, 0x79: 2, 0xB8: 4, 0xB9: 4, 0xF8: 8, 0xF9: 8}[top]
    signed = top in (0x38, 0x39, 0x78, 0x79, 0xB8, 0xB9) and ((w >> 16) & 1) == 0  # opc bit for sb/sh/sw
    lsl = (w >> 13) & 3   # option/shift amount for the index register
    return size, signed, lsl


def guard_count(arr, i):
    """Find nearest preceding subs/cmp-immediate feeding a b.cond; return imm+1 or None."""
    for p in range(i - 1, max(i - 16, 0) - 1, -1):
        w = arr[p]
        if (w & 0xFF800000) == 0x71000000:        # SUBS (imm32), rd may be wzr (cmp)
            return ((w >> 10) & 0xFFF) + 1
        if (w & 0xFF800000) == 0x51000000:        # SUB (imm32) feeding a later subs
            continue
    return None


def scan(name, verbose=False):
    nso = Nso(rf"E:\S3_extract\{name}")
    t = nso.segs[0]; tlo, thi = t["mem_off"], t["mem_off"] + t["size"]
    text = bytes(nso.mem[t["mem_off"]])
    arr = array.array("I"); arr.frombytes(text[: len(text) & ~3])
    pc_of = lambda k: tlo + k * 4
    starts = set(); n_tbl = 0; n_plt = 0; n_br = 0
    for i in range(4, len(arr)):
        if not is_br(arr[i]): continue
        n_br += 1
        rt = (arr[i] >> 5) & 31   # BR/BLR target reg is at bits [9:5]
        # PLT stub (adrp;ldr;add;br)? skip
        if i >= 3:
            w0, w1, w2 = arr[i-3], arr[i-2], arr[i-1]
            if (w0 & 0x9F000000) == 0x90000000 and (w1 & 0xFFC00000) == 0xF9400000 \
               and (w2 & 0xFFC00000) == 0x91000000 and (w0 & 31) == ((w1 >> 5) & 31) \
               and (w1 & 31) == rt:
                n_plt += 1; continue
        # find last writer of rt in window
        last = None
        for p in range(i - 1, max(i - 6, 0) - 1, -1):
            if (arr[p] & 31) == rt and (arr[p] & 0x7F800000) not in ():
                last = p; break
        if last is None: continue
        wl = arr[last]
        # Case A: ldr xT,[xTbl,xIdx,lsl#?] ; br xT
        if is_indexed_load(wl) and (wl & 31) == rt and ((wl >> 30) & 3) == 3:
            rn = (wl >> 5) & 31
            tbl = resolve_addr(arr, last, rn, pc_of)
            if tbl is None or nso.v2file(tbl) is None: continue
            size, signed, _ = load_fmt(wl)
            if size != 8: continue
            cnt = guard_count(arr, last) or 512
            cnt = max(1, min(cnt, 4096))
            for k in range(cnt):
                o = tbl + k * 8
                fo = nso.v2file(o)
                if fo is None: break
                v = struct.unpack_from("<Q", nso.image, fo)[0]
                tgt = v & 0xFFFFFFFF
                if tlo <= tgt < thi - 4 and (tgt & 3) == 0:
                    starts.add(tgt)
            n_tbl += 1; continue
        # Case B: add xT,xBase,xOff,lsl#2 ; br xT
        if (wl & 0xFFA00000) == 0x8B000000 and ((wl >> 10) & 3) == 2:   # add reg with lsl#2
            rn = (wl >> 5) & 31; rm = (wl >> 16) & 31
            base = resolve_addr(arr, last, rn, pc_of)
            if base is None or not (tlo <= base < thi): continue
            # find indexed load writing rm
            lseek = None
            for p in range(last - 1, max(last - 8, 0) - 1, -1):
                if (arr[p] & 31) == rm and is_indexed_load(arr[p]):
                    lseek = p; break
            if lseek is None: continue
            size, signed, _ = load_fmt(arr[lseek])
            ltbl_rn = (arr[lseek] >> 5) & 31
            tbl = resolve_addr(arr, lseek, ltbl_rn, pc_of)
            if tbl is None or nso.v2file(tbl) is None: continue
            cnt = guard_count(arr, last) or 512
            cnt = max(1, min(cnt, 4096))
            for k in range(cnt):
                o = tbl + k * size
                fo = nso.v2file(o)
                if fo is None: break
                if size == 1:
                    v = nso.image[fo]
                    if signed and v & 0x80: v -= 0x100
                elif size == 2:
                    v = struct.unpack_from("<H", nso.image, fo)[0]
                    if signed and v & 0x8000: v -= 0x10000
                elif size == 4:
                    v = struct.unpack_from("<I", nso.image, fo)[0]
                    if signed and v & 0x80000000: v -= 0x100000000
                else:
                    break
                tgt = base + v * 4
                if tlo <= tgt < thi - 4 and (tgt & 3) == 0:
                    starts.add(tgt)
            n_tbl += 1; continue
    print(f"{name}: BR={n_br} BR-jump-tables={n_tbl} plt-skipped={n_plt} targets={len(starts)}")
    if name == "sdk":
        base = 0x0E6FE000
        print(f"   contains image-rel ec7d294 (0x57f294)? {0x57f294 in starts}")
    return starts


if __name__ == "__main__":
    for n in ("sdk", "main", "rtld"):
        s = scan(n)
