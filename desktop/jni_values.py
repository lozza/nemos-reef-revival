"""Read the ARM32 arguments in JNI's Call*MethodV va_list representation."""
import struct


def parameter_types(descriptor):
    if not descriptor.startswith("(") or ")" not in descriptor:
        raise ValueError("invalid JNI method descriptor")
    result = []
    at = 1
    while descriptor[at] != ")":
        start = at
        while descriptor[at] == "[":
            at += 1
        if descriptor[at] == "L":
            at = descriptor.index(";", at) + 1
        elif descriptor[at] in "ZBCSIJFD":
            at += 1
        else:
            raise ValueError("invalid JNI parameter type")
        result.append(descriptor[start:at])
    return result


def read_va_list(machine, pointer, descriptor):
    result = []
    for kind in parameter_types(descriptor):
        wide = kind in ("J", "D", "F")  # C varargs promote jfloat to double.
        if wide:
            pointer = (pointer + 7) & ~7
            value = struct.unpack("<q" if kind == "J" else "<d", machine.mem_read(pointer, 8))[0]
            pointer += 8
        else:
            value = struct.unpack("<I", machine.mem_read(pointer, 4))[0]
            pointer += 4
            if kind == "I":
                value = struct.unpack("<i", struct.pack("<I", value))[0]
            elif kind == "Z":
                value = bool(value & 255)
            elif kind == "B":
                value = struct.unpack("<b", bytes([value & 255]))[0]
            elif kind == "S":
                value = struct.unpack("<h", struct.pack("<H", value & 65535))[0]
            elif kind == "C":
                value &= 65535
        result.append(value)
    return result
