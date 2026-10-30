"""Apply the Revival exchange compatibility fixes to loaded memory, never the APK."""
import struct
from smoke_original import BASE

GAME_URL = b'http://nemo.pragueweb.tapulous.com/nemo/'
EXCHANGE_URL = b'http://nemo.desktop.local/nemo/e1/'
BALANCE_AT = 0x4697bc
DIALOG_PATCHES = (
    (0x469864, bytes.fromhex('be597c00'), bytes.fromhex('b6597c00')),
    (0x46979c, bytes.fromhex('705a7c00'), bytes.fromhex('685a7c00')),
)


def thumb_bl(source, target):
    delta = target-source-4
    if delta & 1 or not -(1 << 24) <= delta < (1 << 24):
        raise ValueError('Thumb call target out of range')
    value = delta & 0x1ffffff
    sign, i1, i2 = (value >> 24) & 1, (value >> 23) & 1, (value >> 22) & 1
    return struct.pack('<HH', 0xf000 | (sign << 10) | ((value >> 12) & 1023),
                       0xd000 | ((1 ^ i1 ^ sign) << 13) | ((1 ^ i2 ^ sign) << 11)
                       | ((value >> 1) & 2047))


def install(runtime):
    if getattr(runtime, 'revival_exchange', None):
        return
    patches = [(BASE+BALANCE_AT, thumb_bl(BALANCE_AT, 0x234738),
                thumb_bl(BALANCE_AT, 0x23473c))]
    patches += [(BASE+at, old, new) for at, old, new in DIALOG_PATCHES]
    original = GAME_URL+b'\0'
    position = runtime.elf.data.find(original)
    if position < 0 or runtime.elf.data.find(original, position+1) >= 0:
        raise RuntimeError('Revival exchange needs the original 1.8.1 game URL')
    segments = [s for s in runtime.elf.segments if s['type'] == 1 and
                s['offset'] <= position and position+len(original) <= s['offset']+s['file_size']]
    if len(segments) != 1 or len(EXCHANGE_URL) > len(GAME_URL):
        raise RuntimeError('Revival exchange game URL is not in one loaded segment')
    segment = segments[0]
    url_address = BASE+segment['address']+position-segment['offset']
    patches.append((url_address, original, EXCHANGE_URL.ljust(len(original), b'\0')))
    # Validate the entire patch set before writing any of it. A different client
    # must never advertise support for an unpatched currency balance check.
    for address, old, new in patches:
        if bytes(runtime.machine.mem_read(address, len(old))) != old:
            raise RuntimeError(f'Revival exchange client mismatch at {address:#x}')
    for address, old, new in patches:
        runtime.machine.mem_write(address, new)
        runtime.machine.ctl_remove_cache(address, address+len(new))
    runtime.revival_exchange = {'active': True, 'route': '/nemo/e1/',
                               'patches': [hex(address) for address, _, _ in patches]}
