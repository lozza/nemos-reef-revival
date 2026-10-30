"""Exact host Adam7 row expansion for the bundled libpng 1.2 png_do_read_interlace.

Thumb entry, one argument r0 = png_ptr. The original reads row_buf (+0x1b0;
row = row_buf + 1), pass (byte, +0x1e8) and the embedded row_info (+0x1c4:
width +0, rowbytes +4, pixel_depth +0xb). For byte-aligned depths it repeats
each of the width pixels png_pass_inc[pass] times in place, from the end of
the row, through an 8-byte stack temporary and memcpy. It then stores
width *= inc and rowbytes = (depth >> 3) * width, modulo 2**32.

Only depths 8..64 in whole bytes are hosted; transformations affect only the
packed 1/2/4-bit paths, which stay in original ARM with every other unknown
case: other depths, pass > 6, a NULL row, overflowing sizes and rows that
overlap the png_struct fields the original rereads.
"""
import hashlib

from native_platform import HEAP_SIZE
from smoke_original import BASE, uc

BODY_SHA256 = '274eda796ffb909a60f1ea7082d180c5b4a8751511f4dce93a9792d149e21c77'
BODY_SIZE = 1012
ROW_BUF, TRANSFORMATIONS, PASS, ROW_INFO = 0x1b0, 0x134, 0x1e8, 0x1c4
PASS_INCREMENT = (8, 8, 4, 4, 2, 2, 1)
HOSTED_DEPTHS = frozenset((8, 16, 24, 32, 48, 64))
STRUCT_EXTENT = 0x1f0  # png_struct bytes read by the original (through the pass byte)
# The original performs width * (inc + 1) small memcpy calls (about 0.14 us
# each through the guest copy shim); the host hook costs about 14 us more
# than a one-pixel original call, so tiny rows stay in ARM.
MIN_COPIES = 128


def expand(data, pixel_bytes, increment):
    """Repeat each pixel_bytes-wide pixel increment times."""
    if increment == 1:
        return bytes(data)
    out = bytearray(len(data) * increment)
    stride = pixel_bytes * increment
    for byte in range(pixel_bytes):
        lane = data[byte::pixel_bytes]
        for copy in range(increment):
            out[copy*pixel_bytes + byte::stride] = lane
    return out


def body(elf, symbol):
    address = symbol['address'] & ~1
    for segment in elf.segments:
        if segment['type'] == 1 and segment['address'] <= address < segment['address'] + segment['file_size']:
            start = segment['offset'] + address - segment['address']
            return elf.data[start:start + symbol['size']]
    return b''


def install(runtime):
    if hasattr(runtime, 'png_interlace_hooks'):
        return
    runtime.png_interlace_hooks = []
    runtime.png_interlace_acceleration = False
    runtime.png_interlace_stats = {'calls': 0, 'bytes': 0, 'fallback': 0}
    try:
        symbol = runtime.elf.symbol('png_do_read_interlace')
    except (KeyError, StopIteration, ValueError):
        return
    if (symbol['size'] != BODY_SIZE or not symbol['address'] & 1
            or hashlib.sha256(body(runtime.elf, symbol)).hexdigest() != BODY_SHA256):
        return
    runtime.png_interlace_acceleration = True

    def dispatch(machine, address, size, _):
        if not runtime.png_interlace_acceleration:
            return
        png = runtime.imported_args(1)[0]
        try:
            fields = machine.mem_read(png, STRUCT_EXTENT)
            row = (int.from_bytes(fields[ROW_BUF:ROW_BUF + 4], 'little') + 1) & 0xffffffff
            width = int.from_bytes(fields[ROW_INFO:ROW_INFO + 4], 'little')
            depth, step = fields[ROW_INFO + 11], fields[PASS]
            if not row or depth not in HOSTED_DEPTHS or step >= len(PASS_INCREMENT):
                raise ValueError
            increment, pixel_bytes = PASS_INCREMENT[step], depth >> 3
            if width * (increment + 1) < MIN_COPIES:
                raise ValueError
            final_width = width * increment
            count = final_width * pixel_bytes
            if final_width > 0xffffffff or count > HEAP_SIZE or (count and row < png + STRUCT_EXTENT and png < row + count):
                raise ValueError
            data = machine.mem_read(row, width * pixel_bytes) if width else b''
            if count:
                machine.mem_write(row, bytes(expand(data, pixel_bytes, increment)))
        except (ValueError, uc.UcError):
            runtime.png_interlace_stats['fallback'] += 1
            return
        machine.mem_write(png + ROW_INFO, final_width.to_bytes(4, 'little')
                          + ((pixel_bytes * final_width) & 0xffffffff).to_bytes(4, 'little'))
        runtime.png_interlace_stats['calls'] += 1
        runtime.png_interlace_stats['bytes'] += count
        runtime.return_value(png)

    entry = BASE + (symbol['address'] & ~1)
    runtime.png_interlace_hooks.append(runtime.machine.hook_add(uc.UC_HOOK_CODE, dispatch, begin=entry, end=entry))
