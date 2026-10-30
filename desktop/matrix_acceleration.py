"""Guarded VFP matrix multiplication, with the original routine as fallback."""
from pathlib import Path
import struct
import hashlib
from smoke_original import BASE

CODE = 0x02200000
NAME = '_ZN3sdr8Matrix4f3mulERKS0_S2_'


def thumb_branch(source, target):
    delta = target - source - 4
    if delta % 2 or not -(1 << 24) <= delta < (1 << 24):
        raise ValueError('Thumb branch target out of range')
    value = delta & 0x1ffffff
    sign, i1, i2 = (value >> 24) & 1, (value >> 23) & 1, (value >> 22) & 1
    j1, j2 = 1 ^ i1 ^ sign, 1 ^ i2 ^ sign
    return struct.pack('<HH', 0xf000 | (sign << 10) | ((value >> 12) & 1023),
                       0x9000 | (j1 << 13) | (j2 << 11) | ((value >> 1) & 2047))


def install(runtime):
    if hasattr(runtime, 'matrix_acceleration'):
        return
    if not hasattr(runtime, 'vfp_originals'):
        raise RuntimeError('Matrix acceleration requires the initialized VFP environment')
    entry = BASE + (runtime.elf.symbol(NAME)['address'] & ~1)
    original = bytes(runtime.machine.mem_read(entry, 4))
    body = bytes(runtime.machine.mem_read(entry, runtime.elf.symbol(NAME)['size']))
    if (entry != 0x01a54f24 or original != bytes.fromhex('f0b55f46') or
            hashlib.sha256(body).hexdigest() != '1c6afb4d075c5014e5b06f531691f5f3089fc644712b82dac51b74eef4f543d9'):
        raise RuntimeError('Matrix shim does not match this client library')
    data = Path(__file__).with_name('matrix_shim.bin').read_bytes()
    runtime.machine.mem_map(CODE, (len(data) + 4095) & ~4095)
    runtime.machine.mem_write(CODE, data)
    patch = thumb_branch(entry, CODE)
    runtime.matrix_acceleration = {'entry': entry, 'original': original, 'patch': patch, 'enabled': True}
    enable(runtime, True)


def enable(runtime, enabled):
    state = runtime.matrix_acceleration
    runtime.machine.mem_write(state['entry'], state['patch'] if enabled else state['original'])
    runtime.machine.ctl_remove_cache(state['entry'], state['entry'] + 4)
    state['enabled'] = bool(enabled)
