"""Small native desktop editor over the original game's text field.

Tk callbacks only update host state. Original ARM callbacks are queued by the
launcher between completed game frames, never from a nested UI callback.
"""
from desktop_gl import glfw


class TextOverlay:
    def __init__(self, graphics, visible=True):
        self.graphics = graphics
        self.visible = visible
        self.window = None
        self.current = None
        self.dismissed = None
        self.finish_requested = False

    def sync(self, entry):
        if entry is None or not entry.active or entry is not self.current:
            self.close()
        if entry is None or not entry.active or entry is self.dismissed or self.window is not None:
            return
        import tkinter as tk
        self.current = entry
        self.finish_requested = False
        self.window = tk.Tk()
        self.window.withdraw()
        self.window.overrideredirect(True)
        left, top, sx, sy = self.layout()
        line_height = max(20, int(entry.font_height * sy) + 6)
        x = int(left + entry.x * sx) + 2
        y = int(top + (entry.y + entry.height / 2) * sy) - line_height // 2
        self.window.geometry(f"{max(20, int(entry.width * sx) - 4)}x{line_height}{x:+d}{y:+d}")
        self.variable = tk.StringVar(value=entry.text)
        initial = entry.text
        editor = tk.Entry(self.window, textvariable=self.variable, relief="flat", borderwidth=0,
                          highlightthickness=0, background="#fff2ce",
                          foreground=f"#{entry.text_color & 0xffffff:06x}",
                          font=(entry.family or "Arial", -max(10, int(entry.font_height * sy)), "bold"),
                          justify={0: "left", 1: "right", 2: "center"}.get(entry.text_align, "left"))
        editor.pack(fill="both", expand=True)

        def changed(*arguments):
            entry.replace_edited(self.variable.get())
            if self.variable.get() != entry.text:
                self.variable.set(entry.text)

        def finish(event=None):
            self.finish_requested = True

        def cancel(event=None):
            entry.replace(initial)
            self.finish_requested = True

        self.variable.trace_add("write", changed)
        editor.bind("<Return>", finish)
        editor.bind("<Escape>", cancel)
        editor.bind("<FocusOut>", finish)
        if self.visible:
            self.window.deiconify()
            self.window.lift()
            self.window.after_idle(editor.focus_force)
        editor.selection_range(0, "end")

    def pump(self):
        if self.window is not None:
            entry = self.current
            left, top, sx, sy = self.layout()
            line_height = max(20, int(entry.font_height * sy) + 6)
            x = int(left + entry.x * sx) + 2
            y = int(top + (entry.y + entry.height / 2) * sy) - line_height // 2
            self.window.geometry(f"{max(20, int(entry.width * sx) - 4)}x{line_height}{x:+d}{y:+d}")
            self.window.update_idletasks()
            self.window.update()

    def layout(self):
        from display_layout import content_rect
        graphics = self.graphics
        left, top = glfw.get_window_pos(graphics.window)
        width, height = glfw.get_window_size(graphics.window)
        fw, fh = glfw.get_framebuffer_size(graphics.window)
        x, y, w, h = content_rect(max(1, fw), max(1, fh), graphics.width, graphics.height)
        return (left + x * width / max(1, fw), top + (fh-y-h) * height / max(1, fh),
                w * width / max(1, fw) / graphics.width,
                h * height / max(1, fh) / graphics.height)

    def close(self):
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.finish_requested = False

    def dismiss(self):
        self.dismissed = self.current
        self.finish_requested = False
        self.close()
