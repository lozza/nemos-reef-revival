"""Native OpenGL context and checked entry points; no Android or EGL runtime."""
import ctypes as C
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / ".deps"))
import glfw

U = C.c_uint
I = C.c_int
F = C.c_float
B = C.c_ubyte
P = C.c_void_p
CALL = getattr(C, "WINFUNCTYPE", C.CFUNCTYPE)


class DesktopGL:
    def __init__(self, width=960, height=540, visible=False):
        self.errors = []
        self.functions = {}
        self.check_each_call = True
        self.window = None
        self.fullscreen = False
        self._framebuffer = 0
        self._viewport = (0, 0, width, height)
        self._scissor = self._viewport
        self._display_size = None
        glfw.set_error_callback(lambda code, message: self.errors.append(
            {"code": code, "message": message.decode("utf-8", "replace")}))
        if not glfw.init():
            raise RuntimeError(f"GLFW initialization failed: {self.errors}")
        try:
            glfw.window_hint(glfw.VISIBLE, visible)
            glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 2)
            glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 1)
            glfw.window_hint(glfw.DEPTH_BITS, 24)
            self.window = glfw.create_window(width, height, "Nemo's Reef desktop", None, None)
            if not self.window:
                raise RuntimeError(f"OpenGL context creation failed: {self.errors}")
            glfw.make_context_current(self.window)
            self.width, self.height = width, height
            self.refresh_display()
            glfw.set_key_callback(self.window, self.key)
            self.info = {key: self.string(token).decode("ascii", "replace") for key, token in
                         (("vendor", 0x1f00), ("renderer", 0x1f01), ("version", 0x1f02))}
            self.extensions = set(self.string(0x1f03).decode("ascii").split())
            self.info.update({"framebuffer": [self.width, self.height],
                              "matrix_palette": "GL_ARB_matrix_palette" in self.extensions,
                              "pvrtc": "GL_IMG_texture_compression_pvrtc" in self.extensions})
        except Exception:
            self.close()
            raise

    def function(self, name, result, *arguments):
        key = (name, result, arguments)
        if key not in self.functions:
            address = glfw.get_proc_address(name)
            if not address:
                raise RuntimeError(f"Desktop OpenGL entry point unavailable: {name}")
            self.functions[key] = CALL(result, *arguments)(address)
        return self.functions[key]

    def string(self, token):
        value = self.function("glGetString", C.c_char_p, U)(token)
        if value is None:
            raise RuntimeError(f"glGetString({token:#x}) returned NULL")
        return value

    def checked(self, name, result, types, values):
        if name == 'glClear' and not self._framebuffer:
            from display_layout import content_rect
            rectangle = content_rect(*glfw.get_framebuffer_size(self.window), self.width, self.height)
            enabled = self.function('glIsEnabled', B, U)(0x0c11)
            previous = (I * 4)()
            self.function('glGetIntegerv', None, U, P)(0x0c10, previous)
            fw, fh = glfw.get_framebuffer_size(self.window)
            x, y, w, h = rectangle
            if (x, y, w, h) != (0, 0, fw, fh):
                color = (F * 4)()
                mask = (B * 4)()
                self.function('glGetFloatv', None, U, P)(0x0c22, color)
                self.function('glGetBooleanv', None, U, P)(0x0c23, mask)
                self.function('glEnable', None, U)(0x0c11)
                self.function('glColorMask', None, B, B, B, B)(1, 1, 1, 1)
                self.function('glClearColor', None, F, F, F, F)(0, 0, 0, 1)
                for bar in ((0, 0, fw, y), (0, y+h, fw, fh-y-h),
                            (0, y, x, h), (x+w, y, fw-x-w, h)):
                    if bar[2] > 0 and bar[3] > 0:
                        self.function('glScissor', None, I, I, I, I)(*bar)
                        self.function('glClear', None, U)(0x4000)
                self.function('glClearColor', None, F, F, F, F)(*color)
                self.function('glColorMask', None, B, B, B, B)(*mask)
            if enabled:
                x, y, w, h = rectangle
                a, b, c, d = previous
                left, bottom = max(x, a), max(y, b)
                rectangle = (left, bottom, max(0, min(x+w, a+c)-left), max(0, min(y+h, b+d)-bottom))
            self.function('glEnable', None, U)(0x0c11)
            self.function('glScissor', None, I, I, I, I)(*rectangle)
            try:
                return self.function(name, result, *types)(*values)
            finally:
                self.function('glScissor', None, I, I, I, I)(*previous)
                if not enabled:
                    self.function('glDisable', None, U)(0x0c11)
        if name in ('glViewport', 'glScissor'):
            from display_layout import viewport_rect
            setattr(self, '_viewport' if name == 'glViewport' else '_scissor', tuple(values))
            if not self._framebuffer:
                values = viewport_rect(values, *glfw.get_framebuffer_size(self.window), self.width, self.height)
        elif name == 'glGetIntegerv' and values[0] in (0x0ba2, 0x0c10):
            rectangle = self._viewport if values[0] == 0x0ba2 else self._scissor
            destination = C.cast(values[1], C.POINTER(I))
            for index, item in enumerate(rectangle):
                destination[index] = item
            return
        value = self.function(name, result, *types)(*values)
        if self.check_each_call:
            self.check_error(name)
        if name == 'glBindFramebuffer':
            self._framebuffer = values[1]
            self._apply_rectangles()
        return value

    def check_error(self, label='frame'):
        error = self.function('glGetError', U)()
        if error:
            raise RuntimeError(f'{label} produced desktop OpenGL error {error:#x}')

    def _apply_rectangles(self):
        from display_layout import viewport_rect
        size = glfw.get_framebuffer_size(self.window)
        for name, rectangle in (('glViewport', self._viewport), ('glScissor', self._scissor)):
            if not self._framebuffer:
                rectangle = viewport_rect(rectangle, *size, self.width, self.height)
            self.function(name, None, I, I, I, I)(*rectangle)

    def refresh_display(self):
        size = glfw.get_framebuffer_size(self.window)
        if min(size) <= 0 or size == self._display_size:
            return
        if self._display_size is not None:
            # Windows applies drawable reallocations when the resized surface is presented.
            glfw.swap_buffers(self.window)
        self._display_size = size
        # Clear newly exposed letterbox areas while retaining the game's GL state.
        if not self._framebuffer:
            color = (F * 4)()
            self.function('glGetFloatv', None, U, P)(0x0c22, color)
            mask = (B * 4)()
            self.function('glGetBooleanv', None, U, P)(0x0c23, mask)
            scissor = self.function('glIsEnabled', B, U)(0x0c11)
            self.function('glDisable', None, U)(0x0c11)
            self.function('glColorMask', None, B, B, B, B)(1, 1, 1, 1)
            self.function('glClearColor', None, F, F, F, F)(0, 0, 0, 1)
            self.function('glClear', None, U)(0x4000)
            self.function('glClearColor', None, F, F, F, F)(*color)
            self.function('glColorMask', None, B, B, B, B)(*mask)
            if scissor:
                self.function('glEnable', None, U)(0x0c11)
        self._apply_rectangles()

    def pointer(self, x=None, y=None, clamp=False):
        from display_layout import pointer_position
        if x is None:
            x, y = glfw.get_cursor_pos(self.window)
        return pointer_position(x, y, *glfw.get_window_size(self.window),
                                *glfw.get_framebuffer_size(self.window), self.width, self.height, clamp=clamp)

    def toggle_fullscreen(self):
        if not self.fullscreen:
            position = glfw.get_window_pos(self.window)
            size = glfw.get_window_size(self.window)
            self._windowed = (position, size, glfw.get_window_attrib(self.window, glfw.DECORATED))
            center = (position[0] + size[0] / 2, position[1] + size[1] / 2)
            monitor = glfw.get_primary_monitor()
            for candidate in glfw.get_monitors():
                x, y = glfw.get_monitor_pos(candidate)
                mode = glfw.get_video_mode(candidate)
                if x <= center[0] < x + mode.size.width and y <= center[1] < y + mode.size.height:
                    monitor = candidate
                    break
            mode = glfw.get_video_mode(monitor)
            glfw.set_window_attrib(self.window, glfw.DECORATED, glfw.FALSE)
            glfw.set_window_pos(self.window, *glfw.get_monitor_pos(monitor))
            glfw.set_window_size(self.window, mode.size.width, mode.size.height)
        else:
            position, size, decorated = self._windowed
            glfw.set_window_attrib(self.window, glfw.DECORATED, decorated)
            glfw.set_window_size(self.window, *size)
            glfw.set_window_pos(self.window, *position)
        self.fullscreen = not self.fullscreen
        self.refresh_display()

    def key(self, window, key, scancode, action, modifiers):
        if action == glfw.PRESS and (key == glfw.KEY_F11 or
                (key == glfw.KEY_ENTER and modifiers & glfw.MOD_ALT) or
                (key == glfw.KEY_ESCAPE and self.fullscreen)):
            self.toggle_fullscreen()

    def close(self):
        if self.window:
            glfw.destroy_window(self.window)
            self.window = None
        glfw.terminate()

    def capture(self, path):
        import hashlib
        import struct
        import zlib
        width, height = glfw.get_framebuffer_size(self.window)
        pixels = (B * (width * height * 4))()
        self.checked("glReadPixels", None, (I, I, I, I, U, U, P),
                     (0, 0, width, height, 0x1908, 0x1401, pixels))
        rgba = bytes(pixels)
        stride = width * 4
        raw = b"".join(b"\0" + rgba[y*stride:(y+1)*stride] for y in reversed(range(height)))
        def chunk(kind, value):
            return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
        encoded = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        return {"path": str(path.resolve()), "sha256": hashlib.sha256(encoded).hexdigest(),
                "nonuniform": any(rgba[i:i+4] != rgba[:4] for i in range(0, len(rgba), 4))}


def probe():
    """Check actual drawing and readback, not just context creation."""
    context = DesktopGL(32, 32)
    try:
        context.checked("glClearColor", None, (F, F, F, F), (0.25, 0.5, 0.75, 1.0))
        context.checked("glClear", None, (U,), (0x4000,))
        pixel = (B * 4)()
        context.checked("glReadPixels", None, (I, I, I, I, U, U, P),
                        (0, 0, 1, 1, 0x1908, 0x1401, pixel))
        if any(abs(value - expected) > 1 for value, expected in zip(pixel, (64, 128, 191, 255))):
            raise RuntimeError(f"Unexpected framebuffer pixel: {list(pixel)}")
        return {"status": "passed", "driver": context.info, "clear_pixel": list(pixel),
                "scope": "Desktop context clear/readback only; not a game frame."}
    finally:
        context.close()


if __name__ == "__main__":
    import json
    from audit_apk import write_json
    result = probe()
    write_json(Path(__file__).resolve().parent / "work" / "graphics-info.json", result)
    print(json.dumps(result, indent=2))
