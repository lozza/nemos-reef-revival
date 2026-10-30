"""ARM soft-float C math calls dispatched to the host C math library."""
import ctypes as C
import ctypes.util
import struct
import sys

import guest_errno
from smoke_original import arm

UNARY = {"acos", "acosf", "asinf", "ceilf", "cos", "cosf", "floor", "floorf",
         "log", "roundf", "sin", "sinf", "sqrt", "sqrtf", "tanf", "truncf"}
BINARY = {"fmax", "fmaxf", "fmin", "fmodf", "pow"}
SUPPORTED = UNARY | BINARY


class MathBindings:
    def __init__(self):
        library = "ucrtbase.dll" if sys.platform == "win32" else C.util.find_library("m")
        if not library:
            raise RuntimeError("No host C math library found")
        self.library = C.CDLL(library, use_errno=True)
        self.functions = {}

    def call(self, runtime, name, words):
        narrow = name.endswith("f")
        kind = C.c_float if narrow else C.c_double
        count = 2 if name in BINARY else 1
        if name not in self.functions:
            function = getattr(self.library, name)
            function.argtypes = [kind] * count
            function.restype = kind
            self.functions[name] = function
        packed = struct.pack("<4I", *words)
        values = struct.unpack_from("<" + ("f" if narrow else "d") * count, packed)
        C.set_errno(0)
        result = self.functions[name](*values)
        error = C.get_errno()
        if error:
            runtime.word(runtime.errno_address, guest_errno.from_host(OSError(error, name)))
        encoded = struct.pack("<f" if narrow else "<d", result)
        if narrow:
            return struct.unpack("<I", encoded)[0]
        low, high = struct.unpack("<II", encoded)
        runtime.machine.reg_write(arm.UC_ARM_REG_R1, high)
        return low
