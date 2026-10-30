"""Nonblocking desktop dialogs; button callbacks update host state only."""


class MessageBoxOverlay:
    def __init__(self, visible=True):
        self.visible = visible
        self.window = None
        self.current = None
        self.buttons = {}

    def sync(self, token, title='', text='', buttons=(), choose=None):
        if token is self.current and self.window is not None:
            return
        self.close()
        if token is None:
            return
        import tkinter as tk
        from tkinter import ttk
        window = tk.Tk()
        window.withdraw()
        self.window = window
        self.current = token
        window.title(title or "Nemo's Reef")
        window.resizable(False, False)
        content = ttk.Frame(window, padding=20)
        content.pack(fill='both', expand=True)
        ttk.Label(content, text=text, wraplength=480, justify='left').pack(fill='x')
        actions = ttk.Frame(content)
        actions.pack(fill='x', pady=(20, 0))

        def selected(value):
            # Never call the ARM engine here: update() can run inside a guest
            # frame. The launcher delivers replies at the next safe boundary.
            if self.current is token and self.window is not None:
                choose(value)
                self.close()

        for value, label in buttons:
            button = ttk.Button(actions, text=label, command=lambda value=value: selected(value))
            button.pack(side='left', padx=(0, 8))
            self.buttons[value] = button
        # Closing a dialog must not guess a native button result.
        window.protocol('WM_DELETE_WINDOW', lambda: None)
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_reqwidth()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_reqheight()) // 2)
        window.geometry(f'+{x}+{y}')
        if self.visible:
            window.deiconify()
            window.lift()
            window.attributes('-topmost', True)
            if self.buttons:
                next(iter(self.buttons.values())).focus_set()

    def pump(self):
        if self.window is not None:
            self.window.update_idletasks()
            self.window.update()

    def close(self):
        if self.window is not None:
            self.window.destroy()
            self.window = None
        self.current = None
        self.buttons = {}
