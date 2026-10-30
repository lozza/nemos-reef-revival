"""Exact desktop checksum fast paths, with legacy Adler edge cases retained.

The bundled Adler implementation reduces noncanonical seed halves even for an
empty input, unlike current zlib. Those seeds continue through original ARM.
"""
import zlib

from native_platform import HEAP_SIZE
from smoke_original import BASE, uc


def install(runtime):
    if hasattr(runtime, "checksum_hooks"):
        return
    runtime.checksum_acceleration = True
    runtime.checksum_stats = {name: {"calls": 0, "bytes": 0} for name in ("adler32", "crc32")}
    runtime.checksum_hooks = []

    def dispatch(machine, address, size, name):
        if not runtime.checksum_acceleration:
            return
        seed, pointer, count, _ = runtime.imported_args()
        if name == "adler32" and not pointer and count == 1:
            return  # The bundled single-byte path dereferences before its NULL check.
        if pointer and name == "adler32" and ((seed & 65535) >= 65521 or seed >> 16 >= 65521):
            return
        if count > HEAP_SIZE:
            raise RuntimeError("checksum input exceeds guest memory budget")
        if not pointer:
            result = 1 if name == "adler32" else 0
        else:
            data = bytes(machine.mem_read(pointer, count)) if count else b""
            result = getattr(zlib, name)(data, seed)
        runtime.checksum_stats[name]["calls"] += 1
        runtime.checksum_stats[name]["bytes"] += count if pointer else 0
        runtime.return_value(result & 0xffffffff)

    for name in ("adler32", "crc32"):
        address = BASE + (runtime.elf.symbol(name)["address"] & ~1)
        runtime.checksum_hooks.append(runtime.machine.hook_add(uc.UC_HOOK_CODE, dispatch,
                                                             user_data=name, begin=address, end=address))
