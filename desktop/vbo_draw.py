"""Draw unchanged desktop VBO attributes without gathering them through Python."""
import ctypes as C

from desktop_gl import U, I, P

SIZES = {0x1400: 1, 0x1401: 1, 0x1402: 2, 0x1403: 2, 0x1406: 4}
POINTERS = {0x8074: 'glVertexPointer', 0x8075: 'glNormalPointer',
            0x8076: 'glColorPointer', 0x8078: 'glTexCoordPointer'}


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
        if not buffer or kind not in SIZES:
            return False  # Guest arrays and GL_FIXED require the existing conversion.
        attributes.append((token, unit, count, kind, stride, pointer, buffer))
    if not any(attribute[0] == 0x8074 for attribute in attributes):
        return False
    consecutive = isinstance(indices, range) and indices.step == 1
    if indices:
        first, last = ((indices[0], indices[-1]) if consecutive else (min(indices), max(indices)))
        for token, unit, count, kind, stride, pointer, buffer in attributes:
            size = count * SIZES[kind]
            step = stride or size
            if pointer + first * step < 0 or pointer + last * step + size > len(runtime.buffers[buffer]):
                raise ValueError('vertex buffer access out of bounds')
    backing = None
    native_elements = elements is not None and bool(elements[2])
    if not consecutive and not native_elements:
        backing = (C.c_ushort * len(indices))(*indices)
    host = runtime.graphics
    current = runtime.buffer_bindings[0x8892]
    client_texture = runtime.client_texture
    element_binding = runtime.buffer_bindings[0x8893]
    try:
        for token, unit, count, kind, stride, pointer, buffer in attributes:
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
        elif native_elements:
            kind, pointer, buffer = elements
            if element_binding != buffer:
                host.checked('glBindBuffer', None, (U, U), (0x8893, buffer))
                element_binding = buffer
            host.checked('glDrawElements', None, (U, I, U, P),
                         (mode, len(indices), kind, C.c_void_p(pointer)))
        else:
            if element_binding:
                host.checked('glBindBuffer', None, (U, U), (0x8893, 0))
                element_binding = 0
            host.checked('glDrawElements', None, (U, I, U, P), (mode, len(indices), 0x1403, backing))
        runtime.draws += 1
        runtime.direct_vbo_draws = getattr(runtime, 'direct_vbo_draws', 0) + 1
        return True
    finally:
        if current != runtime.buffer_bindings[0x8892]:
            host.checked('glBindBuffer', None, (U, U), (0x8892, runtime.buffer_bindings[0x8892]))
        if element_binding != runtime.buffer_bindings[0x8893]:
            host.checked('glBindBuffer', None, (U, U), (0x8893, runtime.buffer_bindings[0x8893]))
        if client_texture != runtime.client_texture:
            host.checked('glClientActiveTexture', None, (U,), (runtime.client_texture,))
