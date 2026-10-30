"""Reusable ARM32 argument/result buffers for pinned Unicorn 2.1.4.

Uses the same native API as Unicorn's Python register helpers, with fixed
general-purpose register types instead of allocating wrappers for every call.
"""
import ctypes as C
import struct
from smoke_original import arm, uc
from unicorn.unicorn_py3.unicorn import uclib


class ArmRegisterIO:
    def __init__(self, machine):
        self.machine = machine
        self.ids = (C.c_int * 4)(arm.UC_ARM_REG_R0, arm.UC_ARM_REG_R1,
                                  arm.UC_ARM_REG_R2, arm.UC_ARM_REG_R3)
        self.values = (C.c_uint32 * 4)()
        self.pointers = (C.c_void_p * 4)(*(C.addressof(self.values) + n*4 for n in range(4)))
        self.stack = C.c_uint32()
        self.stack_pointer = C.byref(self.stack)
        self.link = C.c_uint32()
        self.link_pointer = C.byref(self.link)
        self.results = (C.c_uint32 * 2)()
        self.result_pointer = C.byref(self.results)
        self.pc_pointer = C.byref(self.results, 4)

    def args(self, count=4):
        if count == 0:
            return []
        status = uclib.uc_reg_read_batch(self.machine._uch, self.ids, self.pointers, min(count, 4))
        if status:
            raise uc.UcError(status)
        values = list(self.values[:min(count, 4)])
        if count > 4:
            status = uclib.uc_reg_read(self.machine._uch, arm.UC_ARM_REG_SP, self.stack_pointer)
            if status:
                raise uc.UcError(status)
            values.extend(struct.unpack('<' + 'I'*(count-4), self.machine.mem_read(self.stack.value, (count-4)*4)))
        return values

    def return_value(self, value):
        status = uclib.uc_reg_read(self.machine._uch, arm.UC_ARM_REG_LR, self.link_pointer)
        if status:
            raise uc.UcError(status)
        self.results[0], self.results[1] = value & 0xffffffff, self.link.value
        status = uclib.uc_reg_write(self.machine._uch, arm.UC_ARM_REG_R0, self.result_pointer)
        if status:
            raise uc.UcError(status)
        # A scalar PC write tells Unicorn to leave the current translated block;
        # its batch API does not provide the same hook-return behavior.
        status = uclib.uc_reg_write(self.machine._uch, arm.UC_ARM_REG_PC, self.pc_pointer)
        if status:
            raise uc.UcError(status)
