"""Bridge the original ARM renderer to real desktop OpenGL, strictly by ABI.

Only implemented calls are dispatched. Guest addresses are never passed to the
host driver. Unsupported ES extensions stop with a diagnostic.
"""
import ctypes as C
import struct
from collections import deque

from cooperative_platform import CooperativePlatform
from desktop_gl import U, I, F, B, P
from palette_skinning import Palette, IDENTITY
from smoke_original import BASE, arm, uc
from vertex_reader import VertexReader
from graphics_events import GraphicsEvents


SCALAR_CALLS = {
    "glActiveTexture": (U,), "glClientActiveTexture": (U,),
    "glAlphaFunc": (U, F), "glBindBuffer": (U, U), "glBindTexture": (U, U),
    "glBlendFunc": (U, U), "glClear": (U,), "glClearColor": (F, F, F, F),
    "glColor4f": (F, F, F, F), "glColorMask": (B, B, B, B),
    "glCullFace": (U,), "glDepthMask": (B,), "glDisable": (U,),
    "glDisableClientState": (U,), "glEnable": (U,), "glEnableClientState": (U,),
    "glFinish": (), "glFogf": (U, F), "glFrontFace": (U,),
    "glLineWidth": (F,), "glMatrixMode": (U,), "glPixelStorei": (U, I),
    "glScissor": (I, I, I, I), "glStencilFunc": (U, I, U),
    "glStencilOp": (U, U, U), "glTexEnvf": (U, U, F),
    "glTexEnvi": (U, U, I), "glTexParameteri": (U, U, I),
    "glViewport": (I, I, I, I),
}
# Explicit output lengths prevent driver writes beyond host allocations.
INTEGER_COUNTS = {0x0d31: 1, 0x0d32: 1, 0x0d33: 1, 0x84e2: 1, 0x0d36: 1, 0x0d38: 1,
                  0x0d39: 1, 0x0d3a: 2, 0x0ba2: 4, 0x0c10: 4,
                  0x0d50: 1, 0x0d52: 1, 0x0d53: 1, 0x0d54: 1,
                  0x0d55: 1, 0x0d56: 1, 0x0d57: 1, 0x80a8: 1, 0x80a9: 1}

VERTEX_SIZES = {0x1400: 1, 0x1401: 1, 0x1402: 2, 0x1403: 2, 0x1406: 4, 0x140c: 4}
VERTEX_FORMATS = {0x1400: 'b', 0x1401: 'B', 0x1402: 'h', 0x1403: 'H', 0x1406: 'f', 0x140c: 'i'}
VERTEX_DECODERS = {(count, kind): struct.Struct('<' + fmt * count)
                   for count in range(1, 5) for kind, fmt in VERTEX_FORMATS.items()}
FRAMEBUFFER_CALLS = {'glBindFramebufferOES': (U, U), 'glBindRenderbufferOES': (U, U),
                     'glRenderbufferStorageOES': (U, U, I, I),
                     'glFramebufferRenderbufferOES': (U, U, U, U),
                     'glFramebufferTexture2DOES': (U, U, U, U, I)}
POINTER_TOKENS = {'glVertexPointer': 0x8074, 'glNormalPointer': 0x8075,
                  'glColorPointer': 0x8076, 'glTexCoordPointer': 0x8078,
                  'glMatrixIndexPointerOES': 0x8844, 'glWeightPointerOES': 0x86ad,
                  'glPointSizePointerOES': 0x8b9c}


FAST_SCALAR_CALLS = {name: types for name, types in SCALAR_CALLS.items()
                     if name not in {'glBindBuffer', 'glMatrixMode', 'glEnable', 'glDisable',
                                     'glEnableClientState', 'glDisableClientState',
                                     'glClientActiveTexture', 'glPixelStorei'}}


class GraphicsPlatform(CooperativePlatform):
    def __init__(self, elf, files, graphics):
        super().__init__(elf, files)
        from revival_exchange import install as install_revival_exchange
        install_revival_exchange(self)
        from gameplay_compatibility import install as install_gameplay_compatibility
        install_gameplay_compatibility(self)
        from vfp_acceleration import install as install_vfp
        install_vfp(self)
        from integer_flag_compare import install as install_integer_flags
        install_integer_flags(self)
        from copy_acceleration import install as install_copy
        install_copy(self, small=False)  # Extended path awaits timed full-game verification.
        from matrix_acceleration import install as install_matrix
        install_matrix(self)
        from checksum_acceleration import install
        install(self)
        from json_lookup_acceleration import install as install_json_lookup
        install_json_lookup(self)
        from zip_lookup_acceleration import install as install_zip_lookup
        install_zip_lookup(self)
        from png_filter_acceleration import install as install_png_filter
        install_png_filter(self)
        from png_interlace_acceleration import install as install_png_interlace
        install_png_interlace(self)
        from png_combine_acceleration import install as install_png_combine
        install_png_combine(self)
        self.graphics = graphics
        self.gl_events = GraphicsEvents(maxlen=4096)
        self.fast_scalar_dispatch = True
        self.gl_events_total = 0
        self._native_bridge = None
        self.gl_strings = {}
        self.palette = Palette()
        self.host_matrix_mode = 0x1700
        self.client_texture = 0x84c0
        self.attributes = {}
        self.enabled_attributes = set()
        self.buffer_bindings = {0x8892: 0, 0x8893: 0}
        self.buffers = {}
        self.draws = 0
        self.vertex_reader = VertexReader(self.machine)
        from vbo_snapshot_cache import VboSnapshotCache
        self.vbo_snapshots = VboSnapshotCache()
        self.reuse_vbo_snapshots = True  # Bounded; invalidated by every buffer upload/delete.
        self.direct_vbos = False  # Enabled explicitly by the profiling/verification harness.
        self.direct_vertices = True
        self.direct_vbo_draws = 0
        self.reuse_palette_vertices = True
        self.palette_vertices_transformed = 0
        self.palette_vertices_reused = 0
        self.unpack_alignment = 4
        self.bitmap_acceleration = True
        self.bitmap_accelerations = []
        self.pvrtc_transfers = []
        location = BASE + (elf.symbol("_ZN3sdr6Bitmap13convertBitmapENS_9IRenderer11PixelFormatES2_jjPPhRj")["address"] & ~1)
        self.machine.hook_add(uc.UC_HOOK_CODE, self.accelerate_bitmap, begin=location, end=location)
        for label, name in (("rendererConstructor", "_ZN3sdr11RendererES1C1Ev"),
                            ("screenResize", "_ZN4sdrx8internal4Root19notifyScreenResizedEii"),
                            ("applicationCreate", "_Z17applicationCreatev")):
            location = BASE + (elf.symbol(name)["address"] & ~1)
            self.machine.hook_add(uc.UC_HOOK_CODE, self.observe_graphics_call,
                                  user_data=label, begin=location, end=location)
        from compiled_bridge import install_if_requested
        install_if_requested(self)
        if self._native_bridge is not None:
            from copy_acceleration import prefer_native
            prefer_native(self, True)

    @property
    def gl_events_total(self):
        bridge = getattr(self,'_native_bridge',None)
        return self._gl_events_total + (bridge.gl_total if bridge is not None else 0)

    @gl_events_total.setter
    def gl_events_total(self,value):
        self._gl_events_total = value

    def accelerate_bitmap(self, machine, address, size, context):
        if not self.bitmap_acceleration:
            return
        this, source_format, target_format, width, height, data_ref, bits_ref = self.imported_args(7)
        if (source_format, target_format) != (2, 7):
            return  # Every other conversion continues through original code.
        from bitmap_conversion import rgba4444
        pixels = width * height
        if pixels > 16_777_216:
            raise RuntimeError("bitmap conversion exceeds guest memory budget")
        source = self.word(data_ref)
        if source not in self.heap.live:
            raise RuntimeError("bitmap conversion source is not an owned guest allocation")
        if self.heap.live[source][0] < pixels * 4:
            raise RuntimeError("bitmap conversion exceeds the source allocation")
        value = rgba4444(bytes(machine.mem_read(source, pixels * 4)))
        destination = self.heap.malloc(len(value))
        if not destination:
            raise MemoryError("guest allocation failed during bitmap conversion")
        if value:
            machine.mem_write(destination, value)
        self.heap.free(source)
        self.word(data_ref, destination)
        self.word(bits_ref, 16)
        self.bitmap_accelerations.append({"source_format": 2, "target_format": 7,
                                          "width": width, "height": height, "pixels": pixels})
        self.return_value(0)

    def observe_graphics_call(self, machine, address, size, label):
        state = self.milestones[label] = {"entered": True, "returned": False}
        caller = machine.reg_read(arm.UC_ARM_REG_LR) & ~1
        token = []
        def returned(machine, address, size, context):
            state["returned"] = True
            machine.hook_del(token[0])
        token.append(machine.hook_add(uc.UC_HOOK_CODE, returned, begin=caller, end=caller))

    def attribute_key(self, token):
        return (token, self.client_texture if token == 0x8078 else 0)

    def draw(self, mode, indices, elements=None):
        bridge = getattr(self, '_native_bridge', None)
        if bridge is not None and getattr(bridge, 'client_state_supported', False):
            bridge.sync_client_state()
            # A native draw refused for stale state lands here; republish so later draws stay in C.
            if getattr(bridge, 'draw_enabled', False) and bridge.draw_valid is not None and not bridge.draw_valid[0]:
                bridge.publish_draw_state()
        if len(indices) > 1_000_000:
            raise ValueError("unreasonable vertex count")
        if self.attribute_key(0x8074) not in self.enabled_attributes:
            raise RuntimeError("draw without enabled vertex array")
        if self.direct_vertices:
            from direct_vertex_draw import try_draw
            if try_draw(self, mode, indices, elements):
                return
        if self.direct_vbos:
            from vbo_draw import try_draw
            if try_draw(self, mode, indices, elements):
                return
        data = {}
        snapshots = {}
        sizes = VERTEX_SIZES
        for key in self.enabled_attributes:
            attribute = self.attributes.get(key)
            if attribute is None:
                raise RuntimeError(f"enabled attribute has no pointer: {key}")
            count, kind, stride, pointer, buffer = attribute
            if kind not in sizes:
                raise RuntimeError(f"unsupported vertex type: {kind:#x}")
            size = count * sizes[kind]
            stride = stride or size
            if buffer and self.reuse_vbo_snapshots:
                snapshot = self.vbo_snapshots.read(self.vertex_reader, buffer, self.buffers[buffer],
                                                   indices, pointer, size, stride)
                snapshots[key] = snapshot
                rows = snapshot.rows
            else:
                rows = self.vertex_reader.read(indices, pointer, size, stride,
                                               self.buffers[buffer] if buffer else None)
            data[key] = (count, kind, rows)

        def decoded(key):
            count, kind, rows = data[key]
            if key in snapshots:
                return snapshots[key].decoded(count, kind)
            decoder = VERTEX_DECODERS[count, kind]
            values = [decoder.unpack(row) for row in rows]
            return [tuple(v / 65536.0 for v in row) for row in values] if kind == 0x140c else values

        if self.palette.enabled:
            for token in (0x8844, 0x86ad):
                if self.attribute_key(token) not in data:
                    raise RuntimeError("matrix palette draw without index and weight arrays")
            positions = decoded(self.attribute_key(0x8074))
            palette_indices = decoded(self.attribute_key(0x8844))
            weights = decoded(self.attribute_key(0x86ad))
            normals = decoded(self.attribute_key(0x8075)) if self.attribute_key(0x8075) in data else None
            result = self.palette.transform_indexed(indices, positions, palette_indices, weights, normals,
                                                   reuse=self.reuse_palette_vertices)
            self.palette_vertices_transformed += self.palette.last_transform_count
            self.palette_vertices_reused += len(indices) - self.palette.last_transform_count
            data[self.attribute_key(0x8074)] = (4, 0x1406, [struct.pack("<4f", *value[0]) for value in result])
            snapshots.pop(self.attribute_key(0x8074), None)
            if normals is not None:
                data[self.attribute_key(0x8075)] = (3, 0x1406, [struct.pack("<3f", *value[1]) for value in result])
                snapshots.pop(self.attribute_key(0x8075), None)
        host = self.graphics
        retained = []
        previous_matrix = None
        host.checked("glBindBuffer", None, (U, U), (0x8892, 0))
        try:
            for key, (count, kind, rows) in data.items():
                token, unit = key
                if token in (0x8844, 0x86ad, 0x8b9c):
                    continue
                if key in snapshots:
                    backing = snapshots[key].backing(count, kind)
                    if kind == 0x140c:
                        kind = 0x1406
                elif kind == 0x140c:
                    values = decoded(key)
                    rows = [struct.pack("<" + "f" * count, *row) for row in values]
                    kind = 0x1406
                    backing = C.create_string_buffer(b"".join(rows))
                else:
                    backing = C.create_string_buffer(b"".join(rows))
                retained.append(backing)
                if token == 0x8075:
                    host.checked("glNormalPointer", None, (U, I, P), (kind, 0, backing))
                else:
                    function = {0x8074: "glVertexPointer", 0x8076: "glColorPointer", 0x8078: "glTexCoordPointer"}[token]
                    if token == 0x8078:
                        host.checked("glClientActiveTexture", None, (U,), (unit,))
                    host.checked(function, None, (I, U, I, P), (count, kind, 0, backing))
            if self.palette.enabled:
                previous_matrix = (F * 16)()
                host.checked("glGetFloatv", None, (U, P), (0x0ba6, previous_matrix))
                host.checked("glMatrixMode", None, (U,), (0x1700,))
                host.checked("glLoadMatrixf", None, (P,), ((F * 16)(*IDENTITY),))
            point_sizes = decoded(self.attribute_key(0x8b9c)) if mode == 0 and self.attribute_key(0x8b9c) in data else None
            if point_sizes is None:
                host.checked("glDrawArrays", None, (U, I, I), (mode, 0, len(indices)))
            else:
                previous_size = F()
                host.checked("glGetFloatv", None, (U, P), (0x0b11, C.byref(previous_size)))
                try:
                    for index, value in enumerate(point_sizes):
                        host.checked("glPointSize", None, (F,), (value[0],))
                        host.checked("glDrawArrays", None, (U, I, I), (mode, index, 1))
                finally:
                    host.checked("glPointSize", None, (F,), (previous_size.value,))
            self.draws += 1
        finally:
            if previous_matrix is not None:
                host.checked("glLoadMatrixf", None, (P,), (previous_matrix,))
                host.checked("glMatrixMode", None, (U,), (self.host_matrix_mode,))
            host.checked("glBindBuffer", None, (U, U), (0x8892, self.buffer_bindings[0x8892]))
            host.checked("glClientActiveTexture", None, (U,), (self.client_texture,))

    def bind_import(self, name, args):
        bridge = getattr(self, '_native_bridge', None)
        if bridge is not None and getattr(bridge, 'client_state_supported', False):
            bridge.sync_client_state()
        if not name.startswith("gl"):
            return super().bind_import(name, args)
        if bridge is not None:
            if not bridge.in_fallback:
                bridge.record(name,args)
        else:
            if isinstance(self.gl_events, GraphicsEvents):
                self.gl_events.record(name, args)
            else:
                self.gl_events.append({"name": name, "arguments": [hex(x) for x in args]})
            self.gl_events_total += 1
        if self.fast_scalar_dispatch and name in FAST_SCALAR_CALLS:
            types = FAST_SCALAR_CALLS[name]
            values = args[:len(types)]
            if F in types or I in types:
                values = [struct.unpack('<f', struct.pack('<I', value))[0] if kind is F else
                          C.c_int32(value).value if kind is I else value
                          for kind, value in zip(types, values)]
            self.graphics.checked(name, None, types, values)
            return 0
        if name == "glPixelStorei":
            if args[0] not in (0x0d05, 0x0cf5) or args[1] not in (1, 2, 4, 8):
                raise RuntimeError("unsupported desktop pixel-store layout")
            if args[0] == 0x0cf5:
                self.unpack_alignment = args[1]
        if name in ("glCompressedTexImage2D", "glCompressedTexSubImage2D"):
            from pvrtc_texture import decode, layout
            if name == "glCompressedTexImage2D":
                target, level, pixel_format, width, height, border, size, pointer = self.imported_args(8)
                if border:
                    raise ValueError("PVRTC textures require border zero")
            else:
                target, level, x, y, width, height, pixel_format, size, pointer = self.imported_args(9)
                if x or y:
                    raise ValueError("PVRTC subimage must replace the whole level")
                for token, expected in ((0x1000, width), (0x1001, height)):
                    actual = I()
                    self.graphics.checked("glGetTexLevelParameteriv", None, (U, I, U, P),
                                          (target, level, token, C.byref(actual)))
                    if actual.value != expected:
                        raise ValueError("PVRTC subimage dimensions differ from the level")
            if target != 0x0de1 or level > 14:
                raise ValueError("unsupported PVRTC texture target/level")
            expected = layout(width, height, pixel_format)[2]
            if size != expected or not pointer:
                raise ValueError("invalid PVRTC payload")
            rgba = decode(bytes(self.machine.mem_read(pointer, size)), width, height, pixel_format)
            data = C.create_string_buffer(rgba)
            # Tight RGBA rows work for every width, including the final 1px mip.
            self.graphics.checked("glPixelStorei", None, (U, I), (0x0cf5, 1))
            try:
                if name == "glCompressedTexImage2D":
                    internal = 0x1907 if pixel_format in (0x8c00, 0x8c01) else 0x1908
                    values = (target, level, internal, width, height, 0, 0x1908, 0x1401, data)
                    self.graphics.checked("glTexImage2D", None, (U, I, I, I, I, I, U, U, P), values)
                else:
                    values = (target, level, 0, 0, width, height, 0x1908, 0x1401, data)
                    self.graphics.checked("glTexSubImage2D", None, (U, I, I, I, I, I, U, U, P), values)
            finally:
                self.graphics.checked("glPixelStorei", None, (U, I), (0x0cf5, self.unpack_alignment))
            self.pvrtc_transfers.append({"width": width, "height": height, "level": level,
                                         "format": pixel_format, "bytes": size})
            return 0
        if name in ("glTexImage2D", "glTexSubImage2D"):
            values = self.imported_args(9)
            if name == "glTexImage2D":
                target, level, internal, width, height, border, pixel_format, kind, pointer = values
            else:
                target, level, x, y, width, height, pixel_format, kind, pointer = values
            channels = {0x1906: 1, 0x1907: 3, 0x1908: 4, 0x1909: 1, 0x190a: 2, 0x80e1: 4}
            if pixel_format not in channels or width > 16384 or height > 16384:
                raise ValueError("unsupported texture size/format")
            pixel_size = channels[pixel_format] if kind == 0x1401 else 2 if kind in (0x8363, 0x8033, 0x8034) else None
            if pixel_size is None:
                raise RuntimeError(f"unsupported texture data type {kind:#x}")
            row_size = width * pixel_size
            stride = (row_size + self.unpack_alignment - 1) & ~(self.unpack_alignment - 1)
            size = stride * max(0, height - 1) + row_size if height else 0
            if size > 64 * 1024 * 1024:
                raise ValueError("unreasonable texture transfer size")
            data = C.create_string_buffer(bytes(self.machine.mem_read(pointer, size))) if pointer else None
            self.graphics.checked(name, None, (U, I, I, I, I, I, U, U, P), (*values[:8], data))
            return 0
        if name in ("glGenBuffers", "glGenTextures", "glGenFramebuffersOES", "glGenRenderbuffersOES",
                    "glDeleteBuffers", "glDeleteTextures", "glDeleteFramebuffersOES", "glDeleteRenderbuffersOES"):
            count, pointer = args[:2]
            if count > 100_000:
                raise ValueError("unreasonable GL object count")
            values = (U * count)()
            if name.startswith("glDelete"):
                C.memmove(values, bytes(self.machine.mem_read(pointer, count * 4)), count * 4)
            self.graphics.checked(name.removesuffix("OES"), None, (I, P), (count, values))
            if name.startswith("glGen"):
                self.machine.mem_write(pointer, bytes(values))
                if name == "glGenBuffers":
                    for value in values:
                        if hasattr(bridge, "buffer_changed"):
                            bridge.buffer_changed(value)
                        self.vbo_snapshots.invalidate(value)
                        self.buffers[value] = bytearray()
            elif name == "glDeleteBuffers":
                for value in values:
                    if hasattr(bridge, "buffer_changed"):
                        bridge.buffer_changed(value)
                    self.vbo_snapshots.invalidate(value)
                    self.buffers.pop(value, None)
                    for target, bound in self.buffer_bindings.items():
                        if value == bound:
                            self.buffer_bindings[target] = 0
            return 0
        if name == "glBindBuffer":
            target, value = args[:2]
            if target not in self.buffer_bindings:
                raise RuntimeError(f"unsupported buffer target {target:#x}")
            self.buffer_bindings[target] = value
            if value:
                if hasattr(bridge, "buffer_changed") and value not in self.buffers:
                    bridge.buffer_changed(value)
                self.buffers.setdefault(value, bytearray())
        if name in ("glBufferData", "glBufferSubData"):
            target, second, third, fourth = args
            buffer = self.buffer_bindings.get(target)
            if not buffer:
                raise RuntimeError("buffer upload without bound buffer")
            if hasattr(bridge, "buffer_changed"):
                bridge.buffer_changed(buffer)
            self.vbo_snapshots.invalidate(buffer)
            if name == "glBufferData":
                size, pointer, usage = second, third, fourth
                if size > 64 * 1024 * 1024:
                    raise ValueError("unreasonable buffer size")
                data = bytes(self.machine.mem_read(pointer, size)) if pointer else bytes(size)
                self.buffers[buffer] = bytearray(data)
                self.graphics.checked(name, None, (U, C.c_ssize_t, P, U),
                                      (target, size, C.create_string_buffer(data), usage))
            else:
                offset, size, pointer = second, third, fourth
                if offset + size > len(self.buffers[buffer]):
                    raise ValueError("buffer update out of bounds")
                data = bytes(self.machine.mem_read(pointer, size))
                self.buffers[buffer][offset:offset + size] = data
                self.graphics.checked(name, None, (U, C.c_ssize_t, C.c_ssize_t, P),
                                      (target, offset, size, C.create_string_buffer(data)))
            return 0
        aliases = FRAMEBUFFER_CALLS
        if name in aliases:
            types = aliases[name]
            self.graphics.checked(name.removesuffix("OES"), None, types, self.imported_args(len(types)))
            return 0
        if name == "glCheckFramebufferStatusOES":
            return self.graphics.checked("glCheckFramebufferStatus", U, (U,), args[:1])
        if name == "glGetRenderbufferParameterivOES":
            value = I()
            self.graphics.checked("glGetRenderbufferParameteriv", None, (U, U, P), (*args[:2], C.byref(value)))
            self.word(args[2], value.value & 0xffffffff)
            return 0
        if name == "glFogfv":
            count = 4 if args[0] == 0x0b66 else 1
            values = (F * count).from_buffer_copy(bytes(self.machine.mem_read(args[1], count * 4)))
            self.graphics.checked(name, None, (U, P), (args[0], values))
            return 0
        if name == "glCurrentPaletteMatrixOES":
            self.palette.select(args[0])
            return 0
        if name == "glMatrixMode":
            self.palette.matrix_mode = args[0]
            if args[0] == 0x8840:
                return 0
            self.host_matrix_mode = args[0]
        if name in ("glEnable", "glDisable") and args[0] == 0x8840:
            self.palette.enabled = name == "glEnable"
            return 0
        if name in ("glEnableClientState", "glDisableClientState"):
            key = self.attribute_key(args[0])
            if name == "glEnableClientState":
                self.enabled_attributes.add(key)
            else:
                self.enabled_attributes.discard(key)
            if args[0] in (0x8844, 0x86ad, 0x8b9c):
                return 0
        if name == "glClientActiveTexture":
            self.client_texture = args[0]
        pointers = POINTER_TOKENS
        if name in pointers:
            count, kind, stride, pointer = ((3, *args[:3]) if name == "glNormalPointer" else
                                           (1, *args[:3]) if name == "glPointSizePointerOES" else args)
            if count < 1 or count > 4 or stride > 1_000_000:
                raise ValueError("invalid vertex attribute shape")
            self.attributes[self.attribute_key(pointers[name])] = (count, kind, stride, pointer, self.buffer_bindings[0x8892])
            return 0
        if name == "glDrawArrays":
            mode, first, count = args[:3]
            if count > 1_000_000 or first > 0x7fffffff:
                raise ValueError("invalid draw range")
            self.draw(mode, range(first, first + count))
            return 0
        if name == "glDrawElements":
            mode, count, kind, pointer = args
            if count > 1_000_000 or kind not in (0x1401, 0x1403):
                raise ValueError("invalid element count/type")
            size = count * (1 if kind == 0x1401 else 2)
            buffer = self.buffer_bindings[0x8893]
            if buffer:
                source = self.buffers[buffer]
                if pointer + size > len(source):
                    raise ValueError("index buffer access out of bounds")
                source = source[pointer:pointer + size]
            else:
                source = self.machine.mem_read(pointer, size)
            self.draw(mode, struct.unpack("<" + ("B" if kind == 0x1401 else "H") * count, source),
                      elements=(kind, pointer, buffer))
            return 0
        if name == "glGetString":
            token = args[0]
            if token not in self.gl_strings:
                value = self.graphics.string(token)
                if token == 0x1f03:
                    value += b" GL_OES_matrix_palette GL_OES_point_size_array GL_IMG_texture_compression_pvrtc"
                value += b"\0"
                pointer = self.heap.malloc(len(value))
                self.machine.mem_write(pointer, value)
                self.gl_strings[token] = pointer
            return self.gl_strings[token]
        if name == "glGetError":
            return self.graphics.function(name, U)()
        if name == "glGetIntegerv":
            token, destination = args[:2]
            if token in (0x86a2, 0x86a3):
                from pvrtc_texture import FORMATS
                count = I()
                self.graphics.checked(name, None, (U, P), (0x86a2, C.byref(count)))
                if not 0 <= count.value <= 4096:
                    raise ValueError("invalid driver compression format count")
                native = (I * count.value)()
                self.graphics.checked(name, None, (U, P), (0x86a3, native))
                formats = list(dict.fromkeys([*native, *FORMATS]))
                if token == 0x86a2:
                    self.word(destination, len(formats))
                else:
                    self.machine.mem_write(destination, struct.pack("<" + "I" * len(formats), *formats))
                return 0
            if token in (0x8842, 0x86a4):
                self.word(destination, self.palette.capacity if token == 0x8842 else self.palette.vertex_units)
                return 0
            if token not in INTEGER_COUNTS:
                raise RuntimeError(f"Unimplemented OpenGL integer query {token:#x}")
            values = (I * INTEGER_COUNTS[token])()
            self.graphics.checked(name, None, (U, P), (token, values))
            self.machine.mem_write(destination, bytes(values))
            return 0
        if name == "glLoadMatrixf":
            values = (F * 16).from_buffer_copy(bytes(self.machine.mem_read(args[0], 64)))
            if self.palette.matrix_mode == 0x8840:
                self.palette.load(values)
                return 0
            self.graphics.checked(name, None, (P,), (values,))
            return 0
        if name in SCALAR_CALLS:
            types = SCALAR_CALLS[name]
            # import_call already read R0..R3. Every scalar signature fits
            # those registers; avoid a second native register read per call.
            raw = args[:len(types)]
            values = [struct.unpack("<f", struct.pack("<I", v))[0] if t is F else
                      C.c_int32(v).value if t is I else v for t, v in zip(types, raw)]
            self.graphics.checked(name, None, types, values)
            return 0
        return super().bind_import(name, args)
