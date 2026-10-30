#!/usr/bin/env python3
"""Execute three isolated functions from the original ARM client without Android.

This is an execution feasibility probe, not a game launcher. It deliberately
does not run global constructors, JNI_OnLoad or the application frame loop.
"""
import argparse
import hashlib
from pathlib import Path
import struct
import sys
import zipfile

from desktop_paths import desktop_directory
HERE = desktop_directory()
sys.path.insert(0, str(Path(__file__).resolve().parent / ".deps"))
try:
    import unicorn as uc
    from unicorn import arm_const as arm
except ImportError:
    raise SystemExit("Install the probe dependency: python -m pip install --target desktop/.deps -r desktop/requirements.txt")

from audit_apk import CLIENT, ROOT, Elf32, write_json

BASE = 0x01000000
STACK = 0x04000000
SCRATCH = 0x05000000
IMPORTS = 0x06000000
RETURN = 0x07000000
PAGE = 0x1000


class OriginalCodeProbe:
    def __init__(self, elf):
        self.elf = elf
        self.machine = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_ARM)
        self.bindings_called = set()
        for s in elf.segments:
            if s["type"] != 1:
                continue
            start = (BASE + s["address"]) & ~(PAGE - 1)
            end = (BASE + s["address"] + s["memory_size"] + PAGE - 1) & ~(PAGE - 1)
            self.machine.mem_map(start, end - start)
            self.machine.mem_write(BASE + s["address"], elf.data[s["offset"]:s["offset"] + s["file_size"]])
        for start, size in ((STACK, 0x100000), (SCRATCH, PAGE), (IMPORTS, 0x100000), (RETURN, PAGE)):
            self.machine.mem_map(start, size)
        # Unconditional self-branches keep the call PC stable if a time slice
        # ends at its hook boundary. Zero bytes decode as conditional ARM.
        self.machine.mem_write(IMPORTS, bytes.fromhex('feffffea') * (0x100000 // 4))
        self.import_names = {}
        self.relocate()
        self.import_hook = self.machine.hook_add(uc.UC_HOOK_CODE, self.import_call, begin=IMPORTS, end=IMPORTS + 0xfffff)

    def relocate(self):
        """Handle only the four relocation kinds found in this APK; fail on others."""
        for section in self.elf.sections:
            if section["type"] != 9:
                if section["type"] == 4:
                    raise ValueError("RELA relocations are not supported by this probe")
                continue
            symbols_section = self.elf.sections[section["link"]]
            if symbols_section["type"] != 11:
                raise ValueError("relocations must refer to the dynamic symbol table")
            for at in range(section["offset"], section["offset"] + section["size"], section["entry_size"] or 8):
                offset, info = struct.unpack_from("<II", self.elf.data, at)
                kind, index = info & 255, info >> 8
                target = BASE + offset
                addend = struct.unpack("<I", self.machine.mem_read(target, 4))[0]
                if kind == 0:
                    continue
                if kind == 23:  # R_ARM_RELATIVE
                    value = BASE + addend
                elif kind in (2, 21, 22):  # ABS32, GLOB_DAT, JUMP_SLOT
                    symbol = self.elf.symbols[index]
                    if symbol["section"]:
                        address = symbol["address"] + (0 if symbol["section"] == 0xfff1 else BASE)
                    else:
                        address = IMPORTS + index * 4
                        if address >= IMPORTS + 0x100000:
                            raise ValueError("import table too large")
                        self.import_names[address] = symbol["name"]
                    value = address + (addend if kind == 2 else 0)
                else:
                    raise ValueError(f"unsupported ARM relocation {kind}")
                self.machine.mem_write(target, struct.pack("<I", value & 0xffffffff))

    def import_call(self, machine, address, size, context):
        name = self.import_names.get(address)
        if name not in ("memcpy", "memmove"):
            raise RuntimeError(f"unimplemented platform import: {name or hex(address)}")
        dest = machine.reg_read(arm.UC_ARM_REG_R0)
        source = machine.reg_read(arm.UC_ARM_REG_R1)
        count = machine.reg_read(arm.UC_ARM_REG_R2)
        if count > 0x100000:
            raise RuntimeError("unexpected copy size in isolated probe")
        machine.mem_write(dest, bytes(machine.mem_read(source, count)))
        self.bindings_called.add(name)
        machine.reg_write(arm.UC_ARM_REG_PC, machine.reg_read(arm.UC_ARM_REG_LR))

    def call(self, name, *args):
        s = self.elf.symbol(name)
        return self.call_address(BASE + s["address"], *args)

    def call_address(self, address, *args, instruction_limit=100_000, timeout_us=2_000_000):
        """Call an absolute guest function pointer with ARM32 integer/pointer arguments."""
        self.prepare_call(*args)
        self.machine.emu_start(address, RETURN, timeout=timeout_us, count=instruction_limit)
        if self.machine.reg_read(arm.UC_ARM_REG_PC) != RETURN:
            raise RuntimeError(f"function at {address:#x} did not return within the execution limit")
        return self.machine.reg_read(arm.UC_ARM_REG_R0)

    def prepare_call(self, *args):
        """Set up ARM integer/pointer arguments and an aligned host-call stack."""
        for register in (arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1, arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3):
            self.machine.reg_write(register, 0)
        for register, value in zip((arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1, arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3), args):
            self.machine.reg_write(register, value)
        stack = STACK + 0xff000
        for index, value in enumerate(args[4:]):
            self.machine.mem_write(stack + index * 4, struct.pack("<I", value))
        self.machine.reg_write(arm.UC_ARM_REG_SP, stack)
        self.machine.reg_write(arm.UC_ARM_REG_LR, RETURN)


def require_equal(actual, expected, name):
    if actual != expected:
        raise RuntimeError(f"{name}: expected {expected!r}, got {actual!r}")


def smoke(apk):
    with zipfile.ZipFile(apk) as z:
        elf = Elf32(z.read(CLIENT))
    probe = OriginalCodeProbe(elf)
    shop = probe.call("Java_net_livingmobile_sdr_app_SdrRoot_getShopId")
    require_equal(shop, 1, "original shop identifier")
    probe.machine.mem_write(SCRATCH, b"\xa5" * 64)
    probe.call("_ZN3sdr8Matrix4f8identityEv", SCRATCH)
    identity = list(struct.unpack("<16f", probe.machine.mem_read(SCRATCH, 64)))
    require_equal(identity, [1.0 if i % 5 == 0 else 0.0 for i in range(16)], "original identity matrix")
    probe.machine.mem_write(SCRATCH, b"\xa5" * 64)
    probe.machine.mem_write(SCRATCH + 0x100, struct.pack("<3f", 2.0, 3.0, 4.0))
    probe.call("_ZN3sdr8Matrix4f11createScaleERKNS_8Vector3fE", SCRATCH, SCRATCH + 0x100)
    scale = list(struct.unpack("<16f", probe.machine.mem_read(SCRATCH, 64)))
    expected = [0.0] * 16
    expected[0], expected[5], expected[10], expected[15] = 2.0, 3.0, 4.0, 1.0
    require_equal(scale, expected, "original scale matrix")
    return {"status": "passed", "host": sys.platform, "unicorn": uc.__version__,
            "client_sha256": hashlib.sha256(elf.data).hexdigest(),
            "android_runtime": False, "global_constructors_executed": False,
            "original_functions_executed": 3, "shop_id": shop, "identity_matrix": identity,
            "scale_matrix": scale, "platform_bindings_called": sorted(probe.bindings_called),
            "scope": "Isolated ARM code execution only; client startup and gameplay are not implemented."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", type=Path, default=ROOT / "nemo.apk")
    parser.add_argument("--output", type=Path, default=HERE / "work" / "smoke-result.json")
    args = parser.parse_args()
    result = smoke(args.apk)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    print("PASS: 3 original ARM client functions executed without Android")
    print("Verified: shop identifier, identity matrix, scale matrix; platform binding: memcpy")
    print("This is an execution probe. Client startup and gameplay are not implemented.")


if __name__ == "__main__":
    main()
