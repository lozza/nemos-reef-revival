"""Capture failing guest memory accesses without accepting or masking them."""
import struct
from smoke_original import BASE, arm, uc


def install(runtime):
    runtime.last_memory_fault = None

    def describe(address):
        address &= ~1
        for symbol in runtime.elf.symbols:
            start = BASE + (symbol['address'] & ~1)
            if symbol['section'] and symbol['type'] == 2 and start <= address < start + symbol['size']:
                return {'name': symbol['name'], 'offset': hex(address - start)}
        return None

    def fault(machine, access, address, size, value, data):
        registers = {f'r{i}': hex(machine.reg_read(getattr(arm, f'UC_ARM_REG_R{i}'))) for i in range(13)}
        for name in ('sp', 'lr', 'pc', 'cpsr'):
            registers[name] = hex(machine.reg_read(getattr(arm, 'UC_ARM_REG_' + name.upper())))
        stack = []
        try:
            raw = bytes(machine.mem_read(int(registers['sp'], 16), 128))
            for pointer in struct.unpack('<32I', raw):
                symbol = describe(pointer)
                if symbol is not None:
                    stack.append({'address': hex(pointer), **symbol})
        except uc.UcError:
            pass
        runtime.last_memory_fault = {
            'access': access, 'address': hex(address), 'size': size, 'value': hex(value & 0xffffffffffffffff),
            'thread': getattr(runtime, 'current_thread', 0), 'registers': registers,
            'function': describe(int(registers['pc'], 16)),
            'caller': describe(int(registers['lr'], 16) - 1), 'stack_candidates': stack}
        return False

    runtime.memory_fault_hook = runtime.machine.hook_add(uc.UC_HOOK_MEM_INVALID, fault)
