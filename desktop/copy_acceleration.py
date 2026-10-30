"""Copy small and 64-byte matrix blocks in guest ARM without a Python transition.

Aligned 64-byte matrices keep their ldm/stm path. Other copies of 1..128 bytes
run a word (both pointers 4-aligned) or byte loop when the buffers do not
overlap and each lies inside one 4 KiB page; a fault therefore occurs on the
first access, before any byte is written, like the host snapshot copy. Zero
sizes return the destination untouched. Larger, overlapping and page-crossing
copies retain the host binding. The input APK is
unchanged; only loaded memcpy relocations are adapted.
"""
import struct
from smoke_original import BASE

CODE = 0x05f00000
SMALL_LIMIT = 128

# cmp size,64; reject other sizes, unaligned addresses and overlap.
MATRIX = [0xe3520040, 0, 0xe1803001, 0xe3130003, 0,
          0xe0403001, 0xe3530040, 0, 0xe0413000, 0xe3530040, 0,
          0xe1a0c000, 0xe92d03f0, 0xe8b103fc, 0xe8ac03fc,
          0xe8b103fc, 0xe8ac03fc, 0xe8bd03f0, 0xe12fff1e]
# Inserted before the matrix ldm/stm (MATRIX[11:]): a 4-aligned 64-byte block
# crosses a page iff its page offset exceeds 0xfc0. Such copies go to the
# small path, which hands them to the host, so a fault cannot follow a write.
MATRIX_PAGE_CHECK = [
    (0xe1a03a00, 'lsl   r3, r0, #20'),
    (0xe1a03a23, 'lsr   r3, r3, #20'),
    (0xe3530d3f, 'cmp   r3, #0xfc0'),
    (0, 'bhi   small'),
    (0xe1a03a01, 'lsl   r3, r1, #20'),
    (0xe1a03a23, 'lsr   r3, r3, #20'),
    (0xe3530d3f, 'cmp   r3, #0xfc0'),
    (0, 'bhi   small'),
]
SMALL = CODE + 4*(len(MATRIX) + len(MATRIX_PAGE_CHECK))
# Assembled at SMALL (see work_scripts/assemble_copy.py); branches are relative.
SMALL_PATH = [
    (0xe3520000, 'cmp   r2, #0'),
    (0x012fff1e, 'bxeq  lr'),              # zero size: return dst, no access
    (0xe3520080, 'cmp   r2, #128'),        # SMALL_LIMIT
    (0x8a00001f, 'bhi   fallback'),
    (0xe0403001, 'sub   r3, r0, r1'),
    (0xe1530002, 'cmp   r3, r2'),
    (0x3a00001c, 'blo   fallback'),        # dst in [src, src+n), incl. aliasing
    (0xe0413000, 'sub   r3, r1, r0'),
    (0xe1530002, 'cmp   r3, r2'),
    (0x3a000019, 'blo   fallback'),        # src in [dst, dst+n)
    (0xe1a03a00, 'lsl   r3, r0, #20'),
    (0xe1a03a23, 'lsr   r3, r3, #20'),
    (0xe0833002, 'add   r3, r3, r2'),
    (0xe3530a01, 'cmp   r3, #0x1000'),
    (0x8a000014, 'bhi   fallback'),        # dst crosses a page
    (0xe1a03a01, 'lsl   r3, r1, #20'),
    (0xe1a03a23, 'lsr   r3, r3, #20'),
    (0xe0833002, 'add   r3, r3, r2'),
    (0xe3530a01, 'cmp   r3, #0x1000'),
    (0x8a00000f, 'bhi   fallback'),        # src crosses a page
    (0xe1a0c000, 'mov   ip, r0'),
    (0xe1803001, 'orr   r3, r0, r1'),
    (0xe3130003, 'tst   r3, #3'),
    (0x1a000005, 'bne   bytes'),
    (0xe3520004, 'cmp   r2, #4'),          # words:
    (0x3a000003, 'blo   bytes'),
    (0xe4913004, 'ldr   r3, [r1], #4'),
    (0xe48c3004, 'str   r3, [ip], #4'),
    (0xe2422004, 'sub   r2, r2, #4'),
    (0xeafffff9, 'b     words'),
    (0xe3520000, 'cmp   r2, #0'),          # bytes:
    (0x012fff1e, 'bxeq  lr'),
    (0xe4d13001, 'ldrb  r3, [r1], #1'),
    (0xe4cc3001, 'strb  r3, [ip], #1'),
    (0xe2422001, 'sub   r2, r2, #1'),
    (0xeafffff9, 'b     bytes'),
    (0, 'b     original'),                 # fallback: patched at install
]


def branch(source, target, condition=14):
    delta = target - source - 8
    if delta % 4 or not -0x2000000 <= delta < 0x2000000:
        raise ValueError('copy shim branch out of range')
    return (condition << 28) | 0x0a000000 | ((delta // 4) & 0xffffff)


def code(original, small=True):
    words = list(MATRIX)
    for index, condition in ((1, 1), (4, 1), (7, 3), (10, 3)):
        words[index] = branch(CODE + index*4, original, condition)
    if small:
        # Other sizes, and unaligned/overlapping 64-byte copies, try the small
        # path; it applies its own overlap/page checks before the host binding.
        for index, condition in ((1, 1), (4, 1), (7, 3), (10, 3)):
            words[index] = branch(CODE + index*4, SMALL, condition)
        words[11:11] = [word for word, _ in MATRIX_PAGE_CHECK]
        for index in (14, 18):
            words[index] = branch(CODE + index*4, SMALL, 8)
        words += [word for word, _ in SMALL_PATH]
        words[-1] = branch(CODE + 4*(len(words) - 1), original)
    return words


def install(runtime, small=True):
    if hasattr(runtime, 'copy_acceleration'):
        return
    original = next(address for address, name in runtime.import_names.items() if name == 'memcpy')
    words = code(original, small)
    runtime.machine.mem_map(CODE, 4096)
    runtime.machine.mem_write(CODE, struct.pack('<' + 'I'*len(words), *words))
    relocations = []
    for section in runtime.elf.sections:
        if section['type'] != 9:
            continue
        for at in range(section['offset'], section['offset'] + section['size'], section['entry_size'] or 8):
            address, info = struct.unpack_from('<II', runtime.elf.data, at)
            if info & 255 not in (2, 21, 22):
                continue
            symbol = runtime.elf.symbols[info >> 8]
            if symbol['name'] == 'memcpy' and runtime.word(BASE + address) == original:
                runtime.word(BASE + address, CODE)
                relocations.append(BASE + address)
    runtime.copy_acceleration = {'entry': CODE, 'original': original, 'relocations': relocations,
                                'small_limit': SMALL_LIMIT if small else 0, 'native': False}


def prefer_native(runtime, enabled):
    """Use the compiled memcpy rather than the older emulated matrix copy."""
    state = runtime.copy_acceleration
    bridge = getattr(runtime, '_native_bridge', None)
    if enabled and (bridge is None or not bridge.installed):
        raise RuntimeError('Native copies require an active compiled import bridge')
    for pointer in state['relocations']:
        if runtime.word(pointer) not in (state['entry'], state['original']):
            raise RuntimeError('Unexpected memcpy relocation; no copy routing was changed')
    for pointer in state['relocations']:
        runtime.word(pointer, state['original'] if enabled else state['entry'])
    state['native'] = bool(enabled)
