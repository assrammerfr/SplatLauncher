#!/usr/bin/env python3
"""SplatNative Gate 1 module scanner.

Reads the Ryujinx-extracted Splatoon 3 v11.2.0 program dump (E:\\S3_extract) and reports:
  - NSO header/segment facts, build ids (cross-checked vs the Ryujinx boot log)
  - MOD0 + .dynamic -> dynamic imports/exports (does main import nvnBootstrapLoader?)
  - the S3 NVN surface: every nvn* API name present as a string in the modules
  - main.npdm ACI0 service access list (the authoritative Horizon service ACL)
  - eh_frame_hdr FDE count (function-count estimate for the recompiler)

Input files stay outside the runtime tree; output goes to stdout + a text file in evidence\\.
No keys are read by this tool.
"""
import json
import os
import re
import struct
import sys

import lz4.block

EXTRACT = r"E:\S3_extract"
OUT_PATH = r"E:\SplatNative\evidence\gate1_module_scan.txt"
BOOT_LOG_BUILD_IDS = [
    "263C3426D28AF8D8CCE29A9D2DA8FF0FF9A76456000000000000000000000000",
    "6830B3A12406CB4716FEC5ADDC35D3E2DC92D212000000000000000000000000",
    "574284B40C06CF1821A44857939083BEE5716820000000000000000000000000",
]

out_lines = []


def log(s=""):
    print(s)
    out_lines.append(s)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def s32(b, o):
    return struct.unpack_from("<i", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


class Nso:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.raw = f.read()
        h = self.raw
        assert h[0:4] == b"NSO0", f"{path}: not NSO0"
        self.version = u32(h, 0x04)
        self.flags = u32(h, 0x0C)
        # Header model validated empirically on the three S3 v11.2.0 NSOs
        # (file arithmetic fits exactly; see GATE1_MODULE_SCAN notes):
        #   per segment i: file_off @0x10+0x10i, mem_off @0x14+0x10i,
        #                  mem_size @0x18+0x10i; compressed sizes @0x60+4i
        #   bss @0x3C, build id @0x40 (20 bytes)
        segs = []
        for i, name in enumerate(("text", "ro", "data")):
            base = 0x10 + i * 0x10
            segs.append({
                "name": name,
                "file_off": u32(h, base + 0x0),
                "mem_off": u32(h, base + 0x4),
                "size": u32(h, base + 0x8),
                "csize": u32(h, 0x60 + i * 4),
            })
        self.segs = segs
        self.bss_size = u32(h, 0x3C)
        self.build_id = h[0x40:0x54].hex().upper()
        self.mod0_field = 0

        self.mem = {}  # mem_off -> bytes (decompressed)
        for i, s in enumerate(segs):
            if s["size"] == 0:
                continue
            chunk = self.raw[s["file_off"]: s["file_off"] + s["csize"]]
            compressed = (self.flags >> i) & 1
            if compressed:
                try:
                    data = lz4.block.decompress(chunk, uncompressed_size=s["size"])
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError(f"{path}: LZ4 fail on {s['name']}: {e}")
            else:
                data = chunk
            if len(data) != s["size"]:
                raise RuntimeError(f"{path}: {s['name']} size {len(data)} != {s['size']}")
            self.mem[s["mem_off"]] = data

        # flat image for string scans: [min_off, max_end)
        lo = min(self.mem)
        hi = max(o + len(d) for o, d in self.mem.items())
        self.base = lo
        self.image = bytearray(hi - lo)
        for o, d in self.mem.items():
            self.image[o - lo: o - lo + len(d)] = d

    def v2file(self, vaddr):
        """Map image vaddr -> offset in self.image (None if hole/bss)."""
        for o, d in self.mem.items():
            if o <= vaddr < o + len(d):
                return vaddr - self.base
        return None

    def read_cstr_at_vaddr(self, vaddr):
        off = self.v2file(vaddr)
        if off is None:
            return None
        end = self.image.find(b"\0", off)
        return self.image[off:end].decode("latin1")


def parse_mod0_dynamic(nso):
    """Locate MOD0 (scan all decompressed segments), return (mod0_vaddr, dynamic, meta)."""
    for mem_off in sorted(nso.mem):
        blob = bytes(nso.mem[mem_off])
        rel = blob.find(b"MOD0")
        if rel >= 0:
            return parse_mod0_at(nso, mem_off + rel)
    raise RuntimeError("MOD0 not found in any segment")


def parse_mod0_at(nso, mod0_v):
    fo = nso.v2file(mod0_v)
    assert fo is not None and nso.image[fo:fo + 4] == b"MOD0", "MOD0 magic missing at vaddr"
    dyn_rel = s32(nso.image, fo + 0x4)
    bss_start = s32(nso.image, fo + 0x8)
    bss_end = s32(nso.image, fo + 0xC)
    eh_start = s32(nso.image, fo + 0x10)
    eh_end = s32(nso.image, fo + 0x14)
    meta = {
        "mod0_vaddr": hex(mod0_v),
        "dynamic_rel": hex(dyn_rel),
        "bss_start_rel": hex(bss_start),
        "bss_end_rel": hex(bss_end),
        "eh_frame_hdr_start_rel": hex(eh_start),
        "eh_frame_hdr_end_rel": hex(eh_end),
    }
    entries = []
    dyn_v = mod0_v + dyn_rel
    dfo = nso.v2file(dyn_v)
    if dfo is None:
        return meta, entries
    o = dfo
    while o + 16 <= len(nso.image):
        tag = u64(nso.image, o)
        val = u64(nso.image, o + 8)
        entries.append((tag, val))
        o += 16
        if tag == 0 and len(entries) > 1:
            break
        if len(entries) > 4096:
            break
    return meta, entries


DT = {4: "DT_HASH", 5: "DT_STRTAB", 6: "DT_SYMTAB", 7: "DT_RELA", 8: "DT_RELASZ",
      9: "DT_RELAENT", 10: "DT_STRSZ", 11: "DT_SYMENT", 12: "DT_INIT", 13: "DT_FINI",
      20: "DT_PLTRELSZ", 21: "DT_PLTGOT", 23: "DT_JMPREL", 25: "DT_INIT_ARRAY",
      26: "DT_FINI_ARRAY", 27: "DT_INIT_ARRAYSZ", 28: "DT_FINI_ARRAYSZ",
      0x6ffffef5: "DT_GNU_HASH", 0x6ffffff0: "DT_VERSYM", 0x6ffffffe: "DT_VERNEED",
      0x6fffffff: "DT_VERNEEDNUM", 0x6ffffff9: "DT_RELACOUNT", 0x1e: "DT_FLAGS"}


def parse_dynsyms(nso, dyn):
    d = dict(dyn)
    strtab_v = d.get(5)
    symtab_v = d.get(6)
    strsz = d.get(10, 0)
    syment = d.get(11, 24)
    if not strtab_v or not symtab_v:
        return None, None, "missing DT_STRTAB/DT_SYMTAB"
    s_off = nso.v2file(strtab_v)
    y_off = nso.v2file(symtab_v)
    if s_off is None or y_off is None:
        return None, None, "strtab/symtab in hole"
    # determine symbol count from DT_HASH nchain if present, else scan
    count = None
    if 4 in d:
        h_off = nso.v2file(d[4])
        if h_off is not None:
            count = u32(nso.image, h_off + 4)  # nchain
    syms = []
    o = y_off
    limit = count if count else 1_000_000
    for i in range(limit):
        if o + 24 > len(nso.image):
            break
        st_name = u32(nso.image, o)
        st_info = nso.image[o + 4]
        st_other = nso.image[o + 5]
        st_shndx = struct.unpack_from("<H", nso.image, o + 6)[0]
        st_value = u64(nso.image, o + 8)
        st_size = u64(nso.image, o + 16)
        if count is None:
            if st_name >= strsz or (st_name == 0 and st_shndx == 0 and st_value == 0 and i > 0):
                break
        name = None
        if st_name < strsz:
            end = nso.image.find(b"\0", s_off + st_name)
            if end >= 0 and end - (s_off + st_name) < 512:
                name = nso.image[s_off + st_name:end].decode("latin1")
        syms.append({"name": name, "info": st_info, "shndx": st_shndx,
                     "value": st_value, "size": st_size})
        o += syment
    return syms, {"strtab": hex(strtab_v), "symtab": hex(symtab_v),
                  "strsz": hex(strsz), "count": len(syms)}, None


def eh_frame_fde_count(nso, dyn_meta):
    """FDE count + (addr,size) table from eh_frame_hdr.

    MetaWorks/SDK quirk observed on S3 (rtld hdr = 01 1b 03 3b ...): the stored
    encodings claim pcrel|udata8 for eh_frame_ptr, but the data is laid out as
    u32 eh_frame_ptr, u32 fde_count, then pairs of s32 (datarel) table entries.
    Validated on rtld: count=33 vs 23 BL targets (consistent).
    """
    try:
        rel = int(dyn_meta["eh_frame_hdr_start_rel"], 16)
        mod0_v = int(dyn_meta["mod0_vaddr"], 16)
        hdr_v = mod0_v + rel
        fo = nso.v2file(hdr_v)
        if fo is None:
            return None
        if nso.image[fo] != 1:
            return None
        count = u32(nso.image, fo + 8)
        if count == 0 or count > 5_000_000:
            return None
        # validate table entries resolve into the image
        good = 0
        for i in range(min(count, 64)):
            loc = s32(nso.image, fo + 12 + i * 8)
            if nso.v2file(hdr_v + loc) is not None:
                good += 1
        if good < min(count, 64) * 0.9:
            return None
        return count
    except Exception:  # noqa: BLE001
        return None


def eh_frame_table(nso, dyn_meta, limit=None):
    """Return [(func_vaddr, size)] parsed from eh_frame_hdr + FDEs."""
    rel = int(dyn_meta["eh_frame_hdr_start_rel"], 16)
    mod0_v = int(dyn_meta["mod0_vaddr"], 16)
    hdr_v = mod0_v + rel
    fo = nso.v2file(hdr_v)
    if fo is None or nso.image[fo] != 1:
        return []
    count = u32(nso.image, fo + 8)
    out = []
    for i in range(count if limit is None else min(count, limit)):
        loc_rel = s32(nso.image, fo + 12 + i * 8)
        fde_rel = s32(nso.image, fo + 12 + i * 8 + 4)
        loc = hdr_v + loc_rel
        fde_v = hdr_v + fde_rel
        fde_fo = nso.v2file(fde_v)
        if fde_fo is None:
            continue
        # FDE: u32 length, u32 cie_ptr, u32 pc_begin(pcrel sdata4?), u32 pc_range
        flen = u32(nso.image, fde_fo)
        pc_begin = s32(nso.image, fde_fo + 8)
        size = u32(nso.image, fde_fo + 12)
        # pc_begin should point back at loc when interpreted as pcrel sdata4
        begin_v = fde_v + 8 + pc_begin if False else None  # probing below
        if size == 0 or size > 0x100000:
            # try alternate layout (pc_begin udata4 at +8, range at +12 failed)
            continue
        out.append((loc, size))
    return out


def scan_nvn_strings(nso):
    """Every nvn* API-style identifier present in the module's bytes."""
    pat = re.compile(rb"nvn[A-Za-z][A-Za-z0-9_]{2,60}")
    found = set()
    for m in pat.finditer(bytes(nso.image)):
        found.add(m.group().decode("ascii"))
    return found


def count_bl_targets(nso):
    """Distinct AArch64 BL immediate targets in .text (function-count lower bound)."""
    import array
    t = nso.mem.get(nso.segs[0]["mem_off"])
    if not t:
        return None
    arr = array.array("I")
    arr.frombytes(t[: len(t) & ~3])
    targets = set()
    for i, w in enumerate(arr):
        if (w & 0xFC000000) == 0x94000000:  # BL imm26
            off = ((w & 0x03FFFFFF) << 2)
            if off & 0x08000000:
                off -= 0x10000000
            targets.add(i * 4 + off)
    return len(targets)


def parse_npdm(path):
    with open(path, "rb") as f:
        b = f.read()
    assert b[0:4] == b"META", "not META"
    res = {"size": len(b)}
    want = 0x0100C2500FC20000
    res["program_id_at"] = None
    for off in range(0, len(b) - 8, 4):
        if u64(b, off) == want:
            res["program_id_at"] = hex(off)
            break
    res["program_id"] = hex(u64(b, int(res["program_id_at"], 16))) if res["program_id_at"] else None
    aci0_off = u32(b, 0x70)
    aci0_size = u32(b, 0x74)
    acid_off = u32(b, 0x78)
    acid_size = u32(b, 0x7C)
    res["aci0"] = (hex(aci0_off), hex(aci0_size))
    res["acid"] = (hex(acid_off), hex(acid_size))
    res["aci0_magic"] = b[aci0_off:aci0_off + 4].decode("latin1", "replace")
    res["acid_magic"] = b[acid_off:acid_off + 4].decode("latin1", "replace")
    services = []
    # probe (offset,size) pairs inside the ACI0 section; the service list parses
    # as consecutive u8-length-prefixed printable ASCII strings covering the region.
    for fld in (0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C):
        if aci0_off + fld + 8 > len(b):
            break
        rel = u32(b, aci0_off + fld)
        size = u32(b, aci0_off + fld + 4)
        if size == 0 or size > 0x400:
            continue
        base = aci0_off + rel
        if base + size > len(b):
            continue
        o = base
        names = []
        ok = True
        while o < base + size:
            n = b[o]
            if n == 0:
                break
            # Empirical: length byte == strlen-1 (verified: exact region cover)
            ln = n + 1
            if o + 1 + ln > base + size:
                ok = False
                break
            tok = b[o + 1:o + 1 + ln]
            if not all(0x20 <= c < 0x7F for c in tok):
                ok = False
                break
            names.append(tok.decode("ascii"))
            o += 1 + ln
        if ok and names and o == base + size:
            services = names
            res["service_list"] = {"field": hex(aci0_off + fld), "rel": hex(rel),
                                   "size": hex(size), "count": len(names)}
            break
    res["services"] = services
    return res


def main():
    names = ["rtld", "main", "sdk"]
    report = {}
    nvn_union = set()
    for n in names:
        p = os.path.join(EXTRACT, n)
        if not os.path.exists(p):
            log(f"!! missing {p}")
            continue
        log(f"=== {n} ({os.path.getsize(p)} B) ===")
        nso = Nso(p)
        info = {
            "size": len(nso.raw),
            "flags": hex(nso.flags),
            "build_id": nso.build_id,
            "build_id_matches_bootlog": (nso.build_id + "0" * 24) in BOOT_LOG_BUILD_IDS or nso.build_id in BOOT_LOG_BUILD_IDS,
            "segments": [{"name": s["name"], "mem": hex(s["mem_off"]),
                          "size": hex(s["size"]), "csize": hex(s["csize"]),
                          "compressed": bool((nso.flags >> i) & 1)}
                         for i, s in enumerate(nso.segs)],
            "bss_size": hex(nso.bss_size),
        }
        try:
            meta, dyn = parse_mod0_dynamic(nso)
            info["mod0"] = meta
            info["dynamic"] = [{"tag": DT.get(t, hex(t)), "val": hex(v)} for t, v in dyn if t != 0]
            syms, smeta, err = parse_dynsyms(nso, dyn)
            if err:
                info["dynsyms_error"] = err
            else:
                info["dynsyms"] = smeta
                imp = [s["name"] for s in syms if s["shndx"] == 0 and s["name"]]
                exp = [s["name"] for s in syms if s["shndx"] != 0 and s["name"]]
                info["imports"] = len(imp)
                info["exports"] = len(exp)
                info["nvn_imports"] = sorted(x for x in imp if "nvn" in x.lower())
                info["nvn_exports"] = sorted(x for x in exp if "nvn" in x.lower())
                info["sample_imports"] = sorted(imp)[:40]
                info["sample_exports"] = sorted(exp)[:40]
            fdes = eh_frame_fde_count(nso, meta)
            info["eh_frame_hdr_fde_count"] = fdes
            info["bl_target_count"] = count_bl_targets(nso)
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{type(e).__name__}: {e}"
        nvn = scan_nvn_strings(nso)
        nvn_union |= nvn
        info["nvn_strings_count"] = len(nvn)
        report[n] = info
        log(json.dumps(info, indent=1)[:4000])
        log()

    # SDK version strings
    main_nso = Nso(os.path.join(EXTRACT, "main"))
    sdk_nso = Nso(os.path.join(EXTRACT, "sdk"))
    blob = bytes(main_nso.image) + bytes(sdk_nso.image)
    sdk_vers = sorted(set(re.findall(rb"SDK MW\+[A-Za-z0-9_+\-]+", blob)))
    log("=== SDK library markers ===")
    for v in sdk_vers[:40]:
        log("  " + v.decode("latin1"))
    nvn205 = b"20_5_22" in blob
    log(f"contains '20_5_22': {nvn205}")

    # NPDM
    npdm = parse_npdm(os.path.join(EXTRACT, "main.npdm"))
    log("=== main.npdm ===")
    log(json.dumps(npdm, indent=1))

    # NVN surface union
    nvn_sorted = sorted(nvn_union)
    log(f"=== nvn* string surface across modules: {len(nvn_sorted)} names ===")
    for i in range(0, len(nvn_sorted), 6):
        log("  " + ", ".join(nvn_sorted[i:i + 6]))

    # save machine-readable artifacts
    ev = os.path.dirname(OUT_PATH)
    with open(os.path.join(ev, "gate1_nvn_surface.json"), "w") as f:
        json.dump(nvn_sorted, f, indent=1)
    with open(os.path.join(ev, "gate1_scan.json"), "w") as f:
        json.dump({"modules": report, "npdm": npdm}, f, indent=1)

    with open(OUT_PATH, "w") as f:
        f.write("\n".join(out_lines))
    log(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
