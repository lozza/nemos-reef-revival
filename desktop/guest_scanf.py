"""C-locale scanning for the integer, string and scanset forms used by curl.

Positional arguments, wide characters and floating scans remain explicit
boundaries. Destination arguments are guest pointers, including stack varargs.
"""
import re

SPACE = b" \t\r\n\v\f"
NUMBERS = {ord("d"): rb"[+-]?[0-9]+", ord("u"): rb"[+-]?[0-9]+",
           ord("x"): rb"[+-]?(?:0[xX])?[0-9a-fA-F]+", ord("X"): rb"[+-]?(?:0[xX])?[0-9a-fA-F]+",
           ord("o"): rb"[+-]?[0-7]+",
           ord("i"): rb"[+-]?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)"}


def sscanf(runtime, args):
    source, template = runtime.bytestring(args[0]), runtime.bytestring(args[1])
    position = at = assignments = matched = 0
    argument = 2

    def failed(input_failure=False):
        return -1 if input_failure and matched == 0 else assignments

    def store(value):
        nonlocal argument
        pointer = runtime.imported_args(argument + 1)[argument]
        argument += 1
        runtime.machine.mem_write(pointer, value)

    while at < len(template):
        char = template[at]
        at += 1
        if char in SPACE:
            while position < len(source) and source[position] in SPACE:
                position += 1
            continue
        if char != 37:
            if position == len(source):
                return failed(True)
            if source[position] != char:
                return assignments
            position += 1
            continue
        if at < len(template) and template[at] == 37:
            at += 1
            if source[position:position+1] != b"%":
                return failed(position == len(source))
            position += 1
            continue
        suppress = at < len(template) and template[at] == 42
        at += suppress
        start = at
        while at < len(template) and 48 <= template[at] <= 57:
            at += 1
        width = int(template[start:at]) if at > start else None
        if width is not None and not 1 <= width <= 1_000_000:
            raise RuntimeError("unsupported scanf field width")
        length = b""
        for candidate in (b"hh", b"ll", b"h", b"l", b"j", b"z", b"t"):
            if template[at:at+len(candidate)] == candidate:
                length = candidate
                at += len(candidate)
                break
        if at == len(template):
            raise RuntimeError("incomplete scanf conversion")
        conversion = template[at]
        at += 1
        bits = 8 if length == b"hh" else 16 if length == b"h" else 64 if length in (b"ll", b"j") else 32
        if conversion == ord("n"):
            if not suppress:
                store(position.to_bytes(bits // 8, "little"))
            matched += 1
            continue
        if conversion not in (ord("c"), ord("[")):
            while position < len(source) and source[position] in SPACE:
                position += 1
        if position == len(source):
            return failed(True)
        available = source[position:position + width] if width else source[position:]
        if conversion in NUMBERS:
            token = re.match(NUMBERS[conversion], available)
            if token is None:
                return assignments
            token = token[0]
            digits = token.lstrip(b"+-")
            base = 16 if conversion in (ord("x"), ord("X")) else 8 if conversion == ord("o") else 10
            if conversion == ord("i"):
                base = 16 if digits.lower().startswith(b"0x") else 8 if digits.startswith(b"0") else 10
            value = int(token, base) & ((1 << bits) - 1)
            output = value.to_bytes(bits // 8, "little")
        elif conversion == ord("s"):
            if length:
                raise RuntimeError("wide scanf strings not implemented")
            token = bytes(byte for byte in available[:next((i for i, byte in enumerate(available) if byte in SPACE), len(available))])
            output = token + b"\0"
        elif conversion == ord("c"):
            if length:
                raise RuntimeError("wide scanf characters not implemented")
            token = source[position:position + (width or 1)]
            if len(token) < (width or 1):
                return failed(True)
            output = token
        elif conversion == ord("["):
            if length:
                raise RuntimeError("wide scanf sets not implemented")
            invert = at < len(template) and template[at] == ord("^")
            at += invert
            values = set()
            if at < len(template) and template[at] == ord("]"):
                values.add(ord("]"))
                at += 1
            while at < len(template) and template[at] != ord("]"):
                if at + 2 < len(template) and template[at + 1] == ord("-") and template[at + 2] != ord("]"):
                    values.update(range(template[at], template[at+2] + 1))
                    at += 3
                else:
                    values.add(template[at])
                    at += 1
            if at == len(template):
                raise RuntimeError("incomplete scanf set")
            at += 1
            count = 0
            while count < len(available) and ((available[count] in values) != invert):
                count += 1
            token = available[:count]
            if not token:
                return assignments
            output = token + b"\0"
        else:
            raise RuntimeError(f"unimplemented scanf conversion {chr(conversion)!r}")
        position += len(token)
        matched += 1
        if not suppress:
            store(output)
            assignments += 1
    return assignments
