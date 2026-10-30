"""Exact host reconstruction for the bundled libpng 1.2 png_read_filter_row.

Thumb entry: r0 png_ptr, r1 row_info, r2 row, r3 prev_row, [sp] filter. The
routine is void and only rewrites row[0:rowbytes]. Only Sub on wide rows was
measured faster than translated ARM (Up is already word-parallel in ARM; Avg
and Paeth are serial and slower in Python). Other filters, bad filter values,
empty pixels, short rows and overlapping buffers continue in original ARM.
"""
import hashlib

from native_platform import HEAP_SIZE
from smoke_original import BASE, uc

BODY_SHA256 = '799d2b9607bf388c631e50f86e0d5f314e495307e4159ad1f090129f7527dcfc'
BODY_SIZE = 796
HOSTED_FILTERS = frozenset((1,))
# Enable only the row size with a measured clear win in Claude's handoff.
MIN_HOSTED_BYTES = 4096


def lanes(count):
    low = int.from_bytes(b'\x7f' * count, 'little')
    return low, low ^ int.from_bytes(b'\xff' * count, 'little')


def add_bytes(x, y, low, high):
    """Bytewise addition modulo 256 within one little-endian integer."""
    return ((x & low) + (y & low)) ^ ((x ^ y) & high)


def unfilter(kind, row, prev, bpp):
    """Return the reconstructed row bytes for Sub (1) or Up (2)."""
    count = len(row)
    low, high = lanes(count)
    value = int.from_bytes(row, 'little')
    if kind == 2:
        value = add_bytes(value, int.from_bytes(prev, 'little'), low, high)
    else:
        # Prefix sums with stride bpp by doubling; byte additions do not carry.
        mask = (1 << 8*count) - 1
        step = bpp
        while step < count:
            value = add_bytes(value, (value << 8*step) & mask, low, high)
            step *= 2
    return value.to_bytes(count, 'little')


def body(elf, symbol):
    address = symbol['address'] & ~1
    for segment in elf.segments:
        if segment['type'] == 1 and segment['address'] <= address < segment['address'] + segment['file_size']:
            start = segment['offset'] + address - segment['address']
            return elf.data[start:start + symbol['size']]
    return b''


def install(runtime):
    if hasattr(runtime, 'png_filter_hooks'):
        return
    runtime.png_filter_hooks = []
    runtime.png_filter_acceleration = False
    runtime.png_filter_stats = {kind: {'calls': 0, 'bytes': 0} for kind in HOSTED_FILTERS}
    runtime.png_filter_stats['fallback'] = 0
    try:
        symbol = runtime.elf.symbol('png_read_filter_row')
    except (KeyError, StopIteration, ValueError):
        return
    if (symbol['size'] != BODY_SIZE or not symbol['address'] & 1
            or hashlib.sha256(body(runtime.elf, symbol)).hexdigest() != BODY_SHA256):
        return
    runtime.png_filter_acceleration = True
    machine = runtime.machine

    def dispatch(machine, address, size, _):
        if not runtime.png_filter_acceleration:
            return
        _, info, row, prev, kind = runtime.imported_args(5)
        try:
            if kind not in HOSTED_FILTERS or not row or not prev:
                raise ValueError
            header = machine.mem_read(info, 12)
            count = int.from_bytes(header[4:8], 'little')
            bpp = (header[11] + 7) >> 3
            if (not bpp or count <= bpp or count < MIN_HOSTED_BYTES or count > HEAP_SIZE
                    or (row < prev + count and prev < row + count)):
                raise ValueError
            data = machine.mem_read(row, count)
            above = machine.mem_read(prev, count) if kind == 2 else b''
        except (ValueError, uc.UcError):
            runtime.png_filter_stats['fallback'] += 1
            return
        machine.mem_write(row, unfilter(kind, data, above, bpp))
        runtime.png_filter_stats[kind]['calls'] += 1
        runtime.png_filter_stats[kind]['bytes'] += count
        runtime.return_value(runtime.imported_args(1)[0])

    entry = BASE + (symbol['address'] & ~1)
    runtime.png_filter_hooks.append(machine.hook_add(uc.UC_HOOK_CODE, dispatch, begin=entry, end=entry))
