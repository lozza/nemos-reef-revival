"""Desktop pointer events sent to the original C++ input queue.

This calls the same Events::addTouches entry used by SdrEvents JNI wrappers,
with their recovered action constants and framebuffer coordinates.
"""
import math
import struct

from smoke_original import BASE, RETURN, arm
from trace_startup import ENV

ACTIONS = {"down": 0, "move": 1, "up": 2, "cancel": 3}


class DesktopInput:
    def __init__(self, runtime):
        self.runtime = runtime
        self.events = []

    def require_idle(self):
        r = self.runtime
        if r.current_thread != 0 or r.threads[0]["state"] != "main" or r.machine.reg_read(arm.UC_ARM_REG_PC) != RETURN:
            raise RuntimeError("desktop input requires a completed guest callback")

    def set_text(self, value):
        entry = self.runtime.text_input
        if entry is None or not entry.active:
            raise RuntimeError("no active desktop text field")
        entry.replace(value)
        self.flush_text()

    def flush_text(self):
        r = self.runtime
        entry = r.text_input
        if entry is not None and entry.changed:
            self.require_idle()
            r.call("Java_net_livingmobile_sdr_appui_TextField_valueChanged", ENV, 0, entry.listener, 0)
            entry.changed = False

    def finish_text(self):
        self.require_idle()
        if self.runtime.text_input is None or not self.runtime.text_input.active:
            raise RuntimeError("no active desktop text field")
        self.flush_text()
        self.runtime.call("Java_net_livingmobile_sdr_appui_TextField_focusLost", ENV, 0)

    def touch(self, action, x, y, identifier=0):
        if action not in ACTIONS or not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("invalid desktop touch")
        if not 0 <= identifier <= 0x7fffffff:
            raise ValueError("invalid touch identifier")
        r = self.runtime
        self.require_idle()
        data = r.allocate_bytes(struct.pack("<ffi", x, y, identifier))
        try:
            queue = r.call("_ZN4sdrx3ndk6Events11getInstanceEv")
            r.call_address(BASE + r.elf.symbol("_ZN4sdrx3ndk6Events10addTouchesEiN14NdkMotionEvent10ActionTypeEPfS4_Pi")["address"],
                           queue, 1, ACTIONS[action], data, data + 4, data + 8,
                           instruction_limit=1_000_000, timeout_us=2_000_000)
            self.events.append({"action": action, "x": x, "y": y, "identifier": identifier})
        finally:
            r.heap.free(data)

    def zoom(self, steps):
        self.require_idle()
        from camera_zoom import zoom
        return zoom(self.runtime, steps)
