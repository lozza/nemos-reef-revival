"""Exact original float-comparison flags using integer ARM code and no stack.

The shared cfcmple/cfcmpeq entry preserves all R0-R11, SP and FPSCR, and reproduces
the original helper's LR value after return. The
original reverse veneer is left intact, including its operand-register swap.
The integer ordering handles signed zero, subnormals, infinities and NaNs.
See assemble_integer_flags.py for the reviewed assembly source. Disabled until
explicitly installed; only loaded memory changes, never the APK.
"""
import hashlib
import struct
from smoke_original import BASE
from vfp_acceleration import branch

CODE = 0x02c00000
NAME = '__aeabi_cfcmple'
DIGEST = '9811c00d578147c727da266668d9aac08bd5f8796d1b61dff8aa0dba75670e36'
BODY = bytes.fromhex(
    '80c0a0e1ff045ce31300008a81c0a0e1ff045ce31000008a01c080e102c1dce3'
    '1000000a01c020e002011ce30300001a020110e30100001a010050e1000000ea'
    '000051e100c0a0e301c0a08300c0e03300005ce300007c43030000ea01c0a0e3'
    '00005ce3000000ea00005ce30ec0a0e100e09fe51cff2fe12c42b401')


def install(runtime):
    if hasattr(runtime, 'integer_flag_compare'):
        return
    symbol = runtime.elf.symbol(NAME)
    entry = BASE+symbol['address']
    original = bytes(runtime.machine.mem_read(entry, symbol['size']))
    if symbol['address'] != 0xb44224 or hashlib.sha256(original).hexdigest() != DIGEST:
        raise RuntimeError('Integer comparison shim does not match the original 1.8.1 helper')
    runtime.machine.mem_map(CODE, 4096)
    runtime.machine.mem_write(CODE, BODY)
    runtime.integer_flag_compare = {'entry': entry, 'original': original[:4],
                                   'patch': struct.pack('<I', branch(entry, CODE)), 'enabled': False}
    enable(runtime, True)


def enable(runtime, enabled):
    state = runtime.integer_flag_compare
    runtime.machine.mem_write(state['entry'], state['patch'] if enabled else state['original'])
    runtime.machine.ctl_remove_cache(state['entry'], state['entry']+4)
    state['enabled'] = bool(enabled)
