"""Host C strtod with checked guest pointers and ARM soft-float return words."""
import ctypes as C
import ctypes.util
import locale
import struct
import sys

import guest_errno
from smoke_original import arm

_library = None


def strtod(runtime, args):
    global _library
    # Python normally leaves LC_NUMERIC in C. Do not mutate process locale or
    # silently parse with a comma decimal separator in an embedding process.
    if locale.setlocale(locale.LC_NUMERIC) != "C":
        raise RuntimeError("desktop strtod requires host LC_NUMERIC=C")
    if _library is None:
        name = "ucrtbase.dll" if sys.platform == "win32" else C.util.find_library("c")
        if not name:
            raise RuntimeError("host C library unavailable for strtod")
        _library = C.CDLL(name, use_errno=True)
        _library.strtod.argtypes = [C.c_char_p, C.POINTER(C.c_void_p)]
        _library.strtod.restype = C.c_double
    source, end_pointer = args[:2]
    text = runtime.bytestring(source)
    backing = C.create_string_buffer(text)
    end = C.c_void_p()
    C.set_errno(0)
    result = _library.strtod(backing, C.byref(end))
    error = C.get_errno()
    offset = end.value - C.addressof(backing)
    if not 0 <= offset <= len(text):
        raise RuntimeError("host strtod returned an invalid end pointer")
    if end_pointer:
        runtime.word(end_pointer, source + offset)
    if error:
        runtime.word(runtime.errno_address, guest_errno.from_host(OSError(error, "strtod")))
    low, high = struct.unpack("<II", struct.pack("<d", result))
    runtime.machine.reg_write(arm.UC_ARM_REG_R1, high)
    return low
