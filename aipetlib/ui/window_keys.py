"""Window-local shortcuts that run before native widget class key handling."""
import tkinter as tk


class WindowKeys:
    def __init__(self, window, on_key, on_click=None):
        self.window = window
        self.owner = window._root()
        self.tag = f'AIPetWindowKeys_{id(window)}'
        self.bindings = []
        for event, callback in (('<KeyPress>', on_key), ('<ButtonPress-1>', on_click)):
            if callback is not None:
                self.bindings.append((event, window.bind_class(self.tag, event, callback)))
        window.bind('<Destroy>', self.close, add='+')

    def install(self, widget):
        if widget is not self.window and widget.winfo_class() == 'Toplevel':
            return
        tags = widget.bindtags()
        if self.tag not in tags:
            widget.bindtags((self.tag, *tags))
        for child in widget.winfo_children():
            self.install(child)

    def close(self, event):
        if event.widget is not self.window:
            return
        for sequence, command in self.bindings:
            self.window.unbind_class(self.tag, sequence)
            if command:
                try:
                    self.owner.deletecommand(command)
                except tk.TclError:
                    pass
        self.bindings.clear()
