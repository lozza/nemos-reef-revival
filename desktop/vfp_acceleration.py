"""Use guest VFP arithmetic for finite soft-float helper operands.

Patch only the loaded memory image. Nonfinite/subnormal inputs, division by zero
and subnormal results execute original software, preserving legacy rounding.
"""
import struct
from smoke_original import BASE, arm

CODE = 0x02f00000
OPERATIONS = {'__aeabi_fadd': 0xee300a20, '__aeabi_fsub': 0xee300a60,
              '__aeabi_frsub': 0xee300ac0, '__aeabi_fmul': 0xee200a20,
              '__aeabi_fdiv': 0xee800a20}


def branch(source, destination, condition=14):
    delta = destination - source - 8
    if delta % 4 or not -0x2000000 <= delta < 0x2000000:
        raise ValueError('ARM branch target out of range')
    return (condition << 28) | 0x0a000000 | ((delta // 4) & 0xffffff)


def install(runtime):
    if hasattr(runtime, 'vfp_originals'):
        return
    runtime.vfp_originals = {}
    runtime.vfp_patches = {}
    runtime.vfp_enabled = True
    machine = runtime.machine
    machine.mem_map(CODE, 4096)
    machine.reg_write(arm.UC_ARM_REG_C1_C0_2, machine.reg_read(arm.UC_ARM_REG_C1_C0_2) | 0xf00000)
    machine.reg_write(arm.UC_ARM_REG_FPEXC, 0x40000000)
    machine.reg_write(arm.UC_ARM_REG_FPSCR, 0)  # Nearest/even, gradual underflow.
    current = machine.context_save()
    for thread in getattr(runtime, 'threads', {}).values():
        if thread.get('context') is not None:
            machine.context_restore(thread['context'])
            machine.reg_write(arm.UC_ARM_REG_C1_C0_2, machine.reg_read(arm.UC_ARM_REG_C1_C0_2) | 0xf00000)
            machine.reg_write(arm.UC_ARM_REG_FPEXC, 0x40000000)
            machine.reg_write(arm.UC_ARM_REG_FPSCR, 0)
            thread['context'] = machine.context_save()
    machine.context_restore(current)
    for n, (name, operation) in enumerate(OPERATIONS.items()):
        entry = BASE + runtime.elf.symbol(name)['address']
        stub = CODE + n * 128
        original = bytes(machine.mem_read(entry, 4))
        runtime.vfp_originals[name] = (entry, original)
        words = []
        jumps = []
        for operand in (0, 1):
            words.extend([0xe1a02080 | operand, 0xe3520000])
            zero = len(words)
            words.extend([0, 0xe3520401])
            jumps.append((len(words), 3))  # Below minimum normal.
            words.append(0)
            words.append(0xe35204ff)
            jumps.append((len(words), 2))  # Infinity or NaN.
            words.append(0)
            if name == '__aeabi_fdiv' and operand == 1:
                jumps.append((zero, 0))
            else:
                words[zero] = branch(stub + zero*4, stub + len(words)*4, 0)
        words.extend([0xee000a10, 0xee001a90, operation, 0xee102a10,
                      0xe1a02082, 0xe3520000])
        zero_result = len(words)
        words.extend([0, 0xe3520401])
        jumps.append((len(words), 3))  # Tiny result retains original rounding.
        words.extend([0, 0xee100a10, 0xe12fff1e])
        words[zero_result] = branch(stub + zero_result*4, stub + (len(words)-2)*4, 0)
        slow = stub + len(words)*4
        for index, condition in jumps:
            words[index] = branch(stub + index*4, slow, condition)
        data = struct.pack('<' + 'I'*len(words), *words) + original
        data += struct.pack('<I', branch(slow + 4, entry + 4))
        machine.mem_write(stub, data)
        patch = struct.pack('<I', branch(entry, stub))
        runtime.vfp_patches[name] = patch
        machine.mem_write(entry, patch)
        machine.ctl_remove_cache(entry, entry + 4)


def enable(runtime, enabled):
    for name, (entry, original) in runtime.vfp_originals.items():
        runtime.machine.mem_write(entry, runtime.vfp_patches[name] if enabled else original)
        runtime.machine.ctl_remove_cache(entry, entry + 4)
    runtime.vfp_enabled = bool(enabled)
