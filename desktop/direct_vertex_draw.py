"""Draw VBO and shared-heap client vertex arrays without gathering them through Python.

Client (buffer 0) attributes and element arrays are only handed to the host driver after
runtime.heap.host_pointer validated the whole guest range; anything else falls back.
"""
import ctypes as C

from desktop_gl import U, I, P
from vbo_draw import SIZES, POINTERS

ELEMENT_SIZES = {0x1401: 1, 0x1403: 2}
GUEST_LIMIT = 1 << 32
_EMPTY = (C.c_ubyte * 64)()  # Bound for zero-vertex draws only; the driver reads nothing.


def _host_range(runtime, address, length):
    """Return a host address for a nonempty in-heap guest range, else None. Never dereferences."""
    if address < 0 or length <= 0 or address + length > GUEST_LIMIT:
        return None
    host_pointer = getattr(getattr(runtime, 'heap', None), 'host_pointer', None)
    return host_pointer(address, length) if host_pointer is not None else None


def try_draw(runtime, mode, indices, elements=None):
    if runtime.palette.enabled or (mode == 0 and (0x8b9c, 0) in runtime.enabled_attributes):
        return False
    attributes = []
    for key in runtime.enabled_attributes:
        token, unit = key
        if token in (0x8844, 0x86ad, 0x8b9c):
            continue
        attribute = runtime.attributes.get(key)
        if token not in POINTERS or attribute is None:
            return False
        count, kind, stride, pointer, buffer = attribute
        if kind not in SIZES:
            return False  # GL_FIXED requires the existing conversion.
        attributes.append((token, unit, count, kind, stride, pointer, buffer))
    if not any(attribute[0] == 0x8074 for attribute in attributes):
        return False
    consecutive = isinstance(indices, range) and indices.step == 1
    if indices:
        first, last = ((indices[0], indices[-1]) if consecutive else (min(indices), max(indices)))
    resolved = []
    clients = 0
    for token, unit, count, kind, stride, pointer, buffer in attributes:
        size = count * SIZES[kind]
        step = stride or size
        if buffer:
            if indices and (pointer + first * step < 0
                            or pointer + last * step + size > len(runtime.buffers[buffer])):
                raise ValueError('vertex buffer access out of bounds')
        elif not indices:
            pointer = C.addressof(_EMPTY)
        else:
            start = pointer + first * step
            length = (last - first) * step + size
            if start < 0 or start + length > GUEST_LIMIT:
                raise ValueError('guest vertex access out of bounds')
            host = _host_range(runtime, start, length)
            if host is None or host < first * step:
                return False  # Not entirely inside the shared heap: the existing path reads it.
            pointer = host - first * step  # Driver only reads rows first..last.
            clients += 1
        resolved.append((token, unit, count, kind, stride, pointer, buffer))
    element = None
    backing = None
    if not consecutive:
        if elements is not None and elements[2]:
            element = (elements[0], C.c_void_p(elements[1]), elements[2])
        else:
            host = None
            if elements is not None and elements[0] in ELEMENT_SIZES:
                host = _host_range(runtime, elements[1], len(indices) * ELEMENT_SIZES[elements[0]])
            if host is None:
                backing = (C.c_ushort * len(indices))(*indices)
                element = (0x1403, backing, 0)
            else:
                element = (elements[0], C.c_void_p(host), 0)
    host = runtime.graphics
    current = runtime.buffer_bindings[0x8892]
    client_texture = runtime.client_texture
    element_binding = runtime.buffer_bindings[0x8893]
    try:
        for token, unit, count, kind, stride, pointer, buffer in resolved:
            if buffer != current:
                host.checked('glBindBuffer', None, (U, U), (0x8892, buffer))
                current = buffer
            if token == 0x8078 and unit != client_texture:
                host.checked('glClientActiveTexture', None, (U,), (unit,))
                client_texture = unit
            if token == 0x8075:
                host.checked(POINTERS[token], None, (U, I, P), (kind, stride, C.c_void_p(pointer)))
            else:
                host.checked(POINTERS[token], None, (I, U, I, P), (count, kind, stride, C.c_void_p(pointer)))
        if consecutive:
            host.checked('glDrawArrays', None, (U, I, I), (mode, indices.start, len(indices)))
        else:
            kind, pointer, buffer = element
            if element_binding != buffer:
                host.checked('glBindBuffer', None, (U, U), (0x8893, buffer))
                element_binding = buffer
            host.checked('glDrawElements', None, (U, I, U, P), (mode, len(indices), kind, pointer))
        runtime.draws += 1
        runtime.direct_vertex_draws = getattr(runtime, 'direct_vertex_draws', 0) + 1
        runtime.direct_client_attributes = getattr(runtime, 'direct_client_attributes', 0) + clients
        return True
    finally:
        if current != runtime.buffer_bindings[0x8892]:
            host.checked('glBindBuffer', None, (U, U), (0x8892, runtime.buffer_bindings[0x8892]))
        if element_binding != runtime.buffer_bindings[0x8893]:
            host.checked('glBindBuffer', None, (U, U), (0x8893, runtime.buffer_bindings[0x8893]))
        if client_texture != runtime.client_texture:
            host.checked('glClientActiveTexture', None, (U,), (runtime.client_texture,))
