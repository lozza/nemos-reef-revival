"""Exact host equivalent of SDR's RGBA8888 -> RGBA4444 conversion.

The original divides each channel by 255 in single precision, multiplies by
15 in single precision, then truncates. The table preserves those roundings,
including any differences from simply shifting the original bytes.
"""
import struct


def single(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


QUANTIZE = tuple(int(single(single(value / 255.0) * 15.0)) for value in range(256))


def rgba4444(source):
    if len(source) % 4:
        raise ValueError("RGBA8888 source has an incomplete pixel")
    output = bytearray(len(source) // 2)
    table = QUANTIZE
    for at in range(0, len(source), 4):
        red, green, blue, alpha = source[at:at+4]
        output[at // 2] = (table[blue] << 4) | table[alpha]
        output[at // 2 + 1] = (table[red] << 4) | table[green]
    return bytes(output)
