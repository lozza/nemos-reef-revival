"""Validated PVRTC1 transfers for drivers without the IMG extension.

Decoder: https://github.com/K0lb3/texture2ddecoder (MIT).
Layout: Khronos GL_IMG_texture_compression_pvrtc specification.
"""
FORMATS = (0x8c00, 0x8c01, 0x8c02, 0x8c03)


def layout(width, height, pixel_format):
    if pixel_format not in FORMATS:
        raise ValueError(f"unsupported compressed texture format {pixel_format:#x}")
    if any(n <= 0 or n & (n - 1) for n in (width, height)):
        raise ValueError("PVRTC dimensions must be positive powers of two")
    if width * height > 16_777_216:
        raise ValueError("PVRTC image exceeds desktop transfer budget")
    two = pixel_format in (0x8c01, 0x8c03)
    padded_width, padded_height = max(width, 16 if two else 8), max(height, 8)
    return padded_width, padded_height, padded_width * padded_height // (4 if two else 2), two


def decode(data, width, height, pixel_format):
    padded_width, padded_height, size, two = layout(width, height, pixel_format)
    if len(data) != size:
        raise ValueError(f"PVRTC transfer length {len(data)} differs from required {size}")
    from texture2ddecoder import decode_pvrtc
    bgra = decode_pvrtc(data, padded_width, padded_height, two)
    if len(bgra) != padded_width * padded_height * 4:
        raise ValueError("PVRTC decoder returned an unexpected image size")
    if (width, height) != (padded_width, padded_height):
        bgra = b"".join(bgra[y*padded_width*4:y*padded_width*4+width*4] for y in range(height))
    rgba = bytearray(bgra)
    rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]
    if pixel_format in (0x8c00, 0x8c01):
        rgba[3::4] = b"\xff" * (width * height)
    return bytes(rgba)
