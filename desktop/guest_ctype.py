"""Legacy Bionic exported C-locale tables used directly by ARM code.

ABI reference: Android bionic libc/stdlib/ctype_.c and tolower_.c.
These exports are pointer variables, rather than callable imports.
"""
import struct


def bind_tables(runtime):
    classes = bytearray([0])  # EOF at table[0]; character c at table[c + 1].
    for c in range(256):
        value = 0
        if c < 32 or 127 <= c < 160:
            value |= 0x20
        if c in (9, 10, 11, 12, 13, 32):
            value |= 0x08
        if c == 32:
            value |= 0x80
        if 65 <= c <= 90:
            value |= 0x01
        elif 97 <= c <= 122:
            value |= 0x02
        elif 48 <= c <= 57:
            value |= 0x04
        elif 33 <= c <= 126 or c >= 160:
            value |= 0x10
        if 65 <= c <= 70 or 97 <= c <= 102:
            value |= 0x40
        classes.append(value)
    tables = {"_ctype_": bytes(classes),
              "_tolower_tab_": struct.pack("<257h", -1, *(c + 32 if 65 <= c <= 90 else c for c in range(256))),
              "_toupper_tab_": struct.pack("<257h", -1, *(c - 32 if 97 <= c <= 122 else c for c in range(256)))}
    for address, name in runtime.import_names.items():
        if name in tables:
            runtime.machine.mem_write(address, struct.pack("<I", runtime.allocate_bytes(tables[name])))
