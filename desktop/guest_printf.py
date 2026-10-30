"""Bounded C integer/string printf for the ARM32 host interface.

Floating arguments use ARM's aligned, promoted doubles. Positional, dynamic
width, locale-specific and pointer-write formats are explicit unsupported
operations. No host varargs function receives guest pointers.
"""
import re

TOKEN = re.compile(rb"%([-+ #0]*)([0-9]*)(?:\.([0-9]*))?(hh|h|ll|l|z|t|j)?([diuoxXscfFeEgG%])")


def format_c(runtime, template, argument_word, argument_double=None, argument_integer64=None):
    output = bytearray()
    at = 0
    while at < len(template):
        if template[at] != 37:
            output.append(template[at])
            at += 1
            continue
        match = TOKEN.match(template, at)
        if not match:
            raise RuntimeError(f"unsupported C printf format at {template[at:at+24]!r}")
        flags, width_bytes, precision_bytes, length, conversion = match.groups()
        at = match.end()
        width = int(width_bytes or b"0")
        precision = None if precision_bytes is None else int(precision_bytes or b"0")
        if width > 1_000_000 or (precision or 0) > 1_000_000:
            raise ValueError("unreasonable printf field size")
        if conversion == b"%":
            if flags or width_bytes or precision_bytes is not None or length:
                raise RuntimeError("unsupported decorated percent conversion")
            output.extend(b"%")
            continue
        if conversion in (b"f", b"F", b"e", b"E", b"g", b"G"):
            if argument_double is None or length not in (None, b"l"):
                raise RuntimeError("unsupported printf floating argument ABI")
            specifier = b"%" + flags + width_bytes
            if precision_bytes is not None:
                specifier += b"." + precision_bytes
            output.extend((specifier + conversion) % argument_double())
            continue
        wide = length in (b"ll", b"j")
        if wide and argument_integer64 is None:
            raise RuntimeError("64-bit printf argument ABI is not implemented")
        value = argument_integer64() if wide else argument_word()
        sign, prefix = b"", b""
        if conversion == b"s":
            if length:
                raise RuntimeError("wide printf strings not implemented")
            if not value:
                raise RuntimeError("NULL printf string")
            if precision is None:
                text = runtime.bytestring(value)
            else:
                # Precision bounds memory access even without a NUL terminator.
                text = bytearray()
                for index in range(precision):
                    byte = bytes(runtime.machine.mem_read(value + index, 1))
                    if byte == b"\0":
                        break
                    text.extend(byte)
                text = bytes(text)
        elif conversion == b"c":
            if length:
                raise RuntimeError("wide printf characters not implemented")
            text = bytes([value & 255])
        else:
            bits = 64 if wide else 8 if length == b"hh" else 16 if length == b"h" else 32
            value &= (1 << bits) - 1
            signed = conversion in (b"d", b"i")
            if signed and value & (1 << (bits - 1)):
                value -= 1 << bits
            if value < 0:
                sign, value = b"-", -value
            elif signed:
                sign = b"+" if b"+" in flags else b" " if b" " in flags else b""
            base = {b"o": "o", b"x": "x", b"X": "X"}.get(conversion, "d")
            text = format(value, base).encode("ascii")
            if precision == 0 and value == 0:
                text = b""
            if precision is not None:
                text = text.rjust(precision, b"0")
            if b"#" in flags:
                if conversion == b"o" and not text.startswith(b"0"):
                    prefix = b"0"
                elif conversion in (b"x", b"X") and value:
                    prefix = b"0x" if conversion == b"x" else b"0X"
        if b"0" in flags and b"-" not in flags and precision is None and conversion not in (b"s", b"c"):
            text = text.rjust(max(0, width - len(sign) - len(prefix)), b"0")
        text = sign + prefix + text
        output.extend(text.ljust(width, b" ") if b"-" in flags else text.rjust(width, b" "))
    return bytes(output)


def snprintf(runtime, args, va_list=False, bounded=True, allocated=False):
    if bounded:
        destination, capacity, template = args[:3]
        cursor = args[3] if va_list else 3
    else:
        destination, template = args[:2]
        capacity = None
        cursor = args[2] if va_list else 2

    def next_word():
        nonlocal cursor
        if va_list:
            value = runtime.word(cursor)
            cursor += 4
        else:
            value = runtime.imported_args(cursor + 1)[cursor]
            cursor += 1
        return value

    def next_integer64():
        nonlocal cursor
        cursor = (cursor + (7 if va_list else 1)) & ~(7 if va_list else 1)
        low, high = next_word(), next_word()
        return low | (high << 32)

    def next_double():
        import struct
        return struct.unpack("<d", struct.pack("<Q", next_integer64()))[0]

    result = format_c(runtime, runtime.bytestring(template), next_word, next_double, next_integer64)
    if allocated:
        reference = destination
        destination = runtime.heap.malloc(len(result) + 1)
        runtime.word(reference, destination)
        if not destination:
            import guest_errno
            runtime.word(runtime.errno_address, guest_errno.ENOMEM)
            return -1
    if capacity is None:
        runtime.machine.mem_write(destination, result + b"\0")
    elif capacity:
        runtime.machine.mem_write(destination, result[:capacity - 1] + b"\0")
    return len(result)
