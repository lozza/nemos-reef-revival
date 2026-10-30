#!/usr/bin/env python3
"""Inventory the original client for a desktop port, using only Python's stdlib.

Never modifies the APK or opens the server database. Addresses are ELF virtual
addresses, not file offsets. The low bit of ARM function pointers denotes Thumb.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

from desktop_paths import project_root
ROOT = project_root()
CLIENT = "lib/armeabi/libsdr.so"


def cstring(data, offset):
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError("unterminated string")
    return data[offset:end].decode("utf-8", "replace")


class Elf32:
    """Small ELF32/ARM reader shared with the execution probe."""

    def __init__(self, data):
        self.data = data
        if data[:7] != b"\x7fELF\x01\x01\x01":
            raise ValueError("expected little-endian ELF32")
        h = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
        if h[2] != 40:
            raise ValueError("expected ARM e_machine=40")
        self.flags = h[7]
        self.segments = []
        for i in range(h[10]):
            p = struct.unpack_from("<IIIIIIII", data, h[5] + i * h[9])
            self.segments.append(dict(zip(
                ("type", "offset", "address", "physical", "file_size", "memory_size", "flags", "align"), p)))
        self.sections = []
        for i in range(h[12]):
            p = struct.unpack_from("<IIIIIIIIII", data, h[6] + i * h[11])
            self.sections.append(dict(zip(
                ("name_offset", "type", "flags", "address", "offset", "size", "link", "info", "align", "entry_size"), p)))
        names = self.section_data(self.sections[h[13]])
        for s in self.sections:
            s["name"] = cstring(names, s["name_offset"])
        self.symbols = []
        self.needed = []
        self.relocations = collections.Counter()
        for s in self.sections:
            if s["type"] == 11:  # .dynsym, in its original index order
                strings = self.section_data(self.sections[s["link"]])
                for at in range(s["offset"], s["offset"] + s["size"], s["entry_size"]):
                    n, v, size, info, other, idx = struct.unpack_from("<IIIBBH", data, at)
                    self.symbols.append({"name": cstring(strings, n), "address": v,
                                         "size": size, "type": info & 15, "binding": info >> 4,
                                         "visibility": other & 3, "section": idx})
            elif s["type"] == 6:
                strings = self.section_data(self.sections[s["link"]])
                for at in range(s["offset"], s["offset"] + s["size"], 8):
                    tag, value = struct.unpack_from("<iI", data, at)
                    if tag == 1:
                        self.needed.append(cstring(strings, value))
            elif s["type"] in (9, 4):
                step = s["entry_size"] or (8 if s["type"] == 9 else 12)
                for at in range(s["offset"], s["offset"] + s["size"], step):
                    info = struct.unpack_from("<I", data, at + 4)[0]
                    self.relocations[info & 255] += 1

    def section_data(self, section):
        return self.data[section["offset"]:section["offset"] + section["size"]]

    def file_offset(self, address):
        for s in self.segments:
            if s["type"] == 1 and s["address"] <= address < s["address"] + s["file_size"]:
                return s["offset"] + address - s["address"]
        raise ValueError(f"address {address:#x} has no file backing")

    def symbol(self, name):
        return next(s for s in self.symbols if s["name"] == name and s["section"])


def uleb(data, offset):
    value = 0
    for shift in range(0, 35, 7):
        b = data[offset]
        offset += 1
        value |= (b & 127) << shift
        if not b & 128:
            return value, offset
    raise ValueError("invalid ULEB128")


def dex_native_methods(data):
    """Read actual native declarations, including argument/return descriptors."""
    if data[:4] != b"dex\n":
        raise ValueError("expected DEX")
    string_count, string_off, type_count, type_off, proto_count, proto_off, _, _, method_count, method_off, class_count, class_off = struct.unpack_from("<12I", data, 56)
    strings = []
    for i in range(string_count):
        offset = struct.unpack_from("<I", data, string_off + i * 4)[0]
        _, offset = uleb(data, offset)
        strings.append(cstring(data, offset))
    types = [strings[struct.unpack_from("<I", data, type_off + i * 4)[0]] for i in range(type_count)]
    prototypes = []
    for i in range(proto_count):
        _, ret, offset = struct.unpack_from("<III", data, proto_off + i * 12)
        args = []
        if offset:
            count = struct.unpack_from("<I", data, offset)[0]
            args = [types[struct.unpack_from("<H", data, offset + 4 + j * 2)[0]] for j in range(count)]
        prototypes.append("(" + "".join(args) + ")" + types[ret])
    methods = []
    for i in range(method_count):
        cls, proto, name = struct.unpack_from("<HHI", data, method_off + i * 8)
        methods.append({"class": types[cls], "method": strings[name], "descriptor": prototypes[proto]})
    result = []
    for i in range(class_count):
        offset = struct.unpack_from("<I", data, class_off + i * 32 + 24)[0]
        if not offset:
            continue
        counts = []
        for _ in range(4):
            count, offset = uleb(data, offset)
            counts.append(count)
        for _ in range(counts[0] + counts[1]):
            _, offset = uleb(data, offset)
            _, offset = uleb(data, offset)
        for count in counts[2:]:
            index = 0
            for _ in range(count):
                delta, offset = uleb(data, offset)
                flags, offset = uleb(data, offset)
                _, offset = uleb(data, offset)
                index += delta
                if flags & 0x100:  # ACC_NATIVE
                    result.append({**methods[index], "static": bool(flags & 8)})
    return sorted(result, key=lambda m: (m["class"], m["method"], m["descriptor"]))


def import_group(name):
    if name.startswith("gl") and len(name) > 2 and name[2].isupper():
        return "gles"
    if name.startswith("__android_log_"):
        return "android_log"
    if name.startswith("pthread_"):
        return "threads"
    return "libc_math_loader_unwind"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def audit(apk, output):
    output.mkdir(parents=True, exist_ok=True)
    with apk.open("rb") as source:
        apk_hash = hashlib.file_digest(source, "sha256").hexdigest()
    with zipfile.ZipFile(apk) as z:
        lib = z.read(CLIENT)
        elf = Elf32(lib)
        assets = [n for n in z.namelist() if n.startswith("assets/") and not n.endswith("/")]
        libraries = [n for n in z.namelist() if n.startswith("lib/") and n.endswith(".so")]
        for n in libraries:
            target = output / "lib" / Path(n).name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(z.read(n))
        methods = dex_native_methods(z.read("classes.dex"))
        sys.path.insert(0, str(ROOT / "server"))
        from iscn import entities
        from ipro import read_attributes
        scene = entities(z.read("assets/level.iscn"))
        prototypes = {n: read_attributes(z.read(n)) for n in assets if n.endswith(".ipro")}
    imports = sorted(s["name"] for s in elf.symbols if s["name"] and not s["section"])
    functions = [s for s in elf.symbols if s["section"] and s["type"] == 2]
    jni = [s for s in functions if s["name"].startswith("Java_") or s["name"] == "JNI_OnLoad"]
    groups = {k: [n for n in imports if import_group(n) == k] for k in sorted({import_group(n) for n in imports})}
    summary = {"apk": apk.name, "apk_sha256": apk_hash,
               "client_sha256": hashlib.sha256(lib).hexdigest(), "architecture": "ARM32 little-endian, ARM/Thumb",
               "client_bytes": len(lib), "defined_dynamic_functions": len(functions),
               "unique_function_addresses": len({s["address"] & ~1 for s in functions}),
               "needed": elf.needed, "imports": groups, "jni_exports": jni,
               "dex_native_methods": methods, "relocations_by_arm_type": dict(sorted(elf.relocations.items())),
               "asset_extensions": dict(sorted(collections.Counter(Path(n).suffix for n in assets).items())),
               "starter_entities": len(scene), "prototypes": len(prototypes)}
    write_json(output / "audit.json", summary)
    write_json(output / "starter-scene.json", scene)
    write_json(output / "prototype-attributes.json", prototypes)
    write_json(output / "native-methods.json", methods)
    with (output / "symbols.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "elf_address", "code_address", "file_offset", "size", "kind", "instruction_set"])
        for s in elf.symbols:
            if not s["name"]:
                continue
            code = s["address"] & ~1 if s["type"] == 2 else s["address"]
            try:
                offset = hex(elf.file_offset(code)) if s["section"] else ""
            except ValueError:
                offset = ""  # BSS has a virtual address but no bytes in the file.
            writer.writerow([s["name"], hex(s["address"]), hex(code), offset, s["size"],
                             "import" if not s["section"] else "function" if s["type"] == 2 else "data",
                             ("Thumb" if s["address"] & 1 else "ARM") if s["type"] == 2 and s["section"] else ""])
    report = ["# Original client desktop-port audit", "", f"Input: `{apk.name}`", "",
              f"SHA-256: `{apk_hash}`", "", "## Verified from this APK", "",
              f"- Client: `{CLIENT}`, {len(lib):,} bytes; ARM32 little-endian.",
              f"- {len(functions):,} named dynamic function symbols ({summary['unique_function_addresses']:,} distinct code addresses; includes constructors, aliases, thunks and bundled libraries).",
              f"- {len(imports)} imported symbols; {len(jni)} JNI entry points including JNI_OnLoad.",
              f"- {len(methods)} native method declarations in classes.dex.",
              f"- {len(scene)} starter-scene entities; {len(prototypes)} prototype files parsed by the existing readers.",
              "- Dependencies: " + ", ".join(f"`{n}`" for n in elf.needed) + ".", "",
              "## Platform imports", ""]
    for group, names in groups.items():
        report.extend([f"### {group} ({len(names)})", "", ", ".join(f"`{n}`" for n in names), ""])
    report.extend(["## Original native entry points", "", "| Symbol | ELF function pointer | Bytes |", "|---|---:|---:|"])
    report.extend(f"| `{s['name']}` | `{s['address']:#x}` | {s['size']} |" for s in jni)
    report.extend(["", "## Asset inventory", "", "| Extension | Files |", "|---|---:|"])
    report.extend(f"| `{ext}` | {count} |" for ext, count in summary["asset_extensions"].items())
    report.extend(["", "## Interpretation and limits", "",
                   "The original named code and resources are available for reconstruction. An APK is not C++ source, and a decompiler output is not automatically buildable desktop source.", "",
                   "A fidelity-first investigation can test an ARM execution backend plus a desktop platform bridge. This avoids booting Android, but still translates ARM instructions on an x86 PC. Feasibility, performance, threading and rendering remain unproven until the original client boots.", "",
                   "A native x86/ARM64 rebuild instead requires lifting/reconstructing the client and replacing platform services. Either approach must handle JNI callbacks, startup constructors, relocations, Bionic ABI details, C++ unwinding, GLES 1.x extensions, audio and asset paths.", "",
                   "The imported-symbol list does not enumerate JNI calls made through function pointers or runtime-resolved functions. Native method declarations describe Java-to-C++ entry points, not the complete platform interface.", "",
                   "See ../README.md for the staged plan. This inventory is not a playable desktop client.", ""])
    (output / "AUDIT.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path, nargs="?", default=ROOT / "nemo.apk")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "work")
    args = parser.parse_args()
    summary = audit(args.apk.resolve(), args.output.resolve())
    print(f"Audited {summary['apk']}: {summary['defined_dynamic_functions']:,} named function symbols")
    print(f"Reports and extracted libraries: {args.output.resolve()}")


if __name__ == "__main__":
    main()
