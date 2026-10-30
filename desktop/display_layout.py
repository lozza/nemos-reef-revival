"""Aspect-preserving placement of the fixed logical game screen inside a resizable window.

Framebuffer and viewport rectangles use the OpenGL convention: integer x, y, width, height
with the origin at the bottom-left. Window cursor positions use the windowing-system convention
(top-left origin, window units that may differ from framebuffer pixels under DPI scaling).
Logical pointer coordinates are returned with a top-left origin, matching touch input.
"""
from fractions import Fraction
import math


def _round(value):
    """Round half up so shared edges always land on the same pixel."""
    return math.floor(value + Fraction(1, 2))


def content_rect(width, height, logical_width, logical_height):
    """Return the centered letterboxed/pillarboxed (x, y, w, h) for the logical screen."""
    width, height = max(1, int(width)), max(1, int(height))
    logical_width, logical_height = Fraction(logical_width), Fraction(logical_height)
    if logical_width <= 0 or logical_height <= 0:
        raise ValueError('logical size must be positive')
    if width * logical_height <= height * logical_width:
        w, h = width, _round(width * logical_height / logical_width)
    else:
        w, h = _round(height * logical_width / logical_height), height
    w, h = min(width, max(1, w)), min(height, max(1, h))
    return (width - w) // 2, (height - h) // 2, w, h


def pointer_position(x, y, window_width, window_height, framebuffer_width, framebuffer_height,
                     logical_width, logical_height, clamp=False):
    """Map a top-left window cursor position to top-left logical coordinates.

    Returns None when the cursor lies on the bars or outside the window, unless clamp is set,
    in which case the result is clamped to the logical rectangle.
    """
    if window_width <= 0 or window_height <= 0 or framebuffer_width <= 0 or framebuffer_height <= 0:
        return None
    cx, cy, cw, ch = content_rect(framebuffer_width, framebuffer_height, logical_width, logical_height)
    fx = Fraction(x) * framebuffer_width / window_width
    # Content rect is bottom-left; measure its top edge from the top of the framebuffer.
    top = framebuffer_height - (cy + ch)
    fy = Fraction(y) * framebuffer_height / window_height
    lx = (fx - cx) * Fraction(logical_width) / cw
    ly = (fy - top) * Fraction(logical_height) / ch
    if not clamp and not (0 <= lx < logical_width and 0 <= ly < logical_height):
        return None
    if clamp:
        lx = min(max(lx, 0), Fraction(logical_width))
        ly = min(max(ly, 0), Fraction(logical_height))
    return float(lx), float(ly)


def viewport_rect(rect, framebuffer_width, framebuffer_height, logical_width, logical_height):
    """Scale a logical bottom-left (x, y, w, h) into framebuffer pixels inside the content rect."""
    x, y, w, h = (Fraction(value) for value in rect)
    cx, cy, cw, ch = content_rect(framebuffer_width, framebuffer_height, logical_width, logical_height)
    sx, sy = Fraction(cw) / Fraction(logical_width), Fraction(ch) / Fraction(logical_height)
    left, right = cx + _round(x * sx), cx + _round((x + w) * sx)
    bottom, top = cy + _round(y * sy), cy + _round((y + h) * sy)
    return left, bottom, right - left, top - bottom
