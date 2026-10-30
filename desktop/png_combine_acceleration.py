"""Exact host masked row combine for the bundled libpng 1.2 png_combine_row.

Thumb entry: r0 png_ptr, r1 row, r2 mask. A mask of exactly 0xff is a single
memcpy and stays in original ARM. Otherwise, for byte-aligned depths, the
original loads row_buf (+0x1b0; source = row_buf + 1), the image width
(+0x18c, not row_info.width) and pixel_depth (row_info +0xb = +0x1cf) once,
then for each pixel i < width with bit 0x80 >> (i & 7) set in mask copies
depth >> 3 bytes from source to row with memcpy, one import call per pixel.

Only depths 8..64 in whole bytes are hosted. Packed depths, other depths,
overlapping source/destination spans, unreadable spans and rows with fewer
than MIN_PIXELS selected pixels continue in original ARM.
"""
import hashlib

from native_platform import HEAP_SIZE
from smoke_original import BASE, uc

BODY_SHA256 = '9367ed8c32582289e3c7322168feec24b65b4e60519bc347e48a2124f03f538a'
BODY_SIZE = 628
ROW_BUF, WIDTH, PIXEL_DEPTH = 0x1b0, 0x18c, 0x1cf
HOSTED_DEPTHS = frozenset((8, 16, 24, 32, 48, 64))
# Measured with copy_acceleration(small=False): original costs about 10 us per
# selected pixel and ties the host hook at 2 pixels.
MIN_PIXELS = 3


def selected(width, mask):
    """Pixels the original copies: bit 0x80 >> (i & 7) of mask, i < width."""
    full, rest = divmod(width, 8)
    bits = [(mask >> (7 - k)) & 1 for k in range(8)]
    return full * sum(bits) + sum(bits[:rest])


def combine(destination, source, pixel_bytes, mask):
    """Return destination with the masked pixels taken from source."""
    out = bytearray(destination)
    stride = 8 * pixel_bytes
    for k in range(8):
        if mask & (0x80 >> k):
            for byte in range(k*pixel_bytes, (k + 1)*pixel_bytes):
                out[byte::stride] = source[byte::stride]
    return out


def body(elf, symbol):
    address = symbol['address'] & ~1
    for segment in elf.segments:
        if segment['type'] == 1 and segment['address'] <= address < segment['address'] + segment['file_size']:
            start = segment['offset'] + address - segment['address']
            return elf.data[start:start + symbol['size']]
    return b''


def install(runtime):
    if hasattr(runtime, 'png_combine_hooks'):
        return
    runtime.png_combine_hooks = []
    runtime.png_combine_acceleration = False
    runtime.png_combine_stats = {'calls': 0, 'pixels': 0, 'fallback': 0}
    try:
        symbol = runtime.elf.symbol('png_combine_row')
    except (KeyError, StopIteration, ValueError):
        return
    if (symbol['size'] != BODY_SIZE or not symbol['address'] & 1
            or hashlib.sha256(body(runtime.elf, symbol)).hexdigest() != BODY_SHA256):
        return
    runtime.png_combine_acceleration = True

    def dispatch(machine, address, size, _):
        if not runtime.png_combine_acceleration:
            return
        png, row, mask = runtime.imported_args(3)
        try:
            if mask == 0xff:
                raise ValueError
            depth = machine.mem_read(png + PIXEL_DEPTH, 1)[0]
            if depth not in HOSTED_DEPTHS:
                raise ValueError
            source = (int.from_bytes(machine.mem_read(png + ROW_BUF, 4), 'little') + 1) & 0xffffffff
            width = int.from_bytes(machine.mem_read(png + WIDTH, 4), 'little')
            pixel_bytes = depth >> 3
            count = width * pixel_bytes
            pixels = selected(width, mask)
            if (pixels < MIN_PIXELS or count > HEAP_SIZE or row + count > 1 << 32 or source + count > 1 << 32
                    or (row < source + count and source < row + count)):
                raise ValueError
            # Both whole spans readable implies every selected pixel is, and
            # (with full-permission pages) that the destination is writable.
            data = machine.mem_read(source, count)
            current = machine.mem_read(row, count)
        except (ValueError, uc.UcError):
            runtime.png_combine_stats['fallback'] += 1
            return
        machine.mem_write(row, bytes(combine(current, data, pixel_bytes, mask)))
        runtime.png_combine_stats['calls'] += 1
        runtime.png_combine_stats['pixels'] += pixels
        runtime.return_value(png)

    entry = BASE + (symbol['address'] & ~1)
    runtime.png_combine_hooks.append(runtime.machine.hook_add(uc.UC_HOOK_CODE, dispatch, begin=entry, end=entry))
