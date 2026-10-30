"""C-locale integer conversion with the original ARM32 widths and end pointers."""
import guest_errno as errno
from smoke_original import arm


def convert(runtime, name, args):
    pointer = args[0]
    destination, base = (0, 10) if name == "atoi" else args[1:3]
    bits = 64 if name == "strtoll" else 32
    unsigned = name == "strtoul"
    if base and not 2 <= base <= 36:
        runtime.word(runtime.errno_address, errno.EINVAL)
        if destination:
            runtime.word(destination, pointer)
        return 0
    text = runtime.bytestring(pointer)
    at = 0
    while at < len(text) and text[at] in b" \t\n\r\v\f":
        at += 1
    negative = at < len(text) and text[at] == 45
    if at < len(text) and text[at] in (43, 45):
        at += 1

    def digit(byte):
        if 48 <= byte <= 57:
            return byte - 48
        if 65 <= byte <= 90:
            return byte - 65 + 10
        if 97 <= byte <= 122:
            return byte - 97 + 10
        return 99

    if base in (0, 16) and text[at:at+2].lower() == b"0x" and at + 2 < len(text) and digit(text[at+2]) < 16:
        base = 16
        at += 2
    if not base:
        base = 8 if text[at:at+1] == b"0" else 10
    start = at
    limit = (1 << bits) - 1 if unsigned else (1 << (bits - 1)) - (not negative)
    value = 0
    overflow = False
    while at < len(text) and digit(text[at]) < base:
        value = value * base + digit(text[at])
        if value > limit:
            overflow = True
            value = limit + 1
        at += 1
    if destination:
        runtime.word(destination, pointer + at if at > start else pointer)
    if overflow:
        runtime.word(runtime.errno_address, errno.ERANGE)
        value = limit
    if negative and not (overflow and unsigned):
        value = -value
    value &= (1 << bits) - 1
    if bits == 64:
        runtime.machine.reg_write(arm.UC_ARM_REG_R1, value >> 32)
    return value & 0xffffffff
