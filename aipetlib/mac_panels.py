"""Native passive Tk panels used by macOS overlays."""
import tkinter as tk


class MacOverlayWindow(tk.Toplevel):
    """Create passive overlays as panels before Tk realizes their native windows."""
    def __init__(self, master):
        super().__init__(master, takefocus=False)
        # Tk's main window is already an NSWindow by the time Tk() returns.
        # Changing MacWindowStyle later cannot turn it into an NSPanel. A fresh
        # toplevel lets Tk allocate a non-activating panel for full-screen Spaces.
        self.tk.call("::tk::unsupported::MacWindowStyle", "style", self._w, "utility",
                     ("noTitleBar", "nonActivating", "doesNotHide", "doesNotCycle"))


class MacPetWindow(MacOverlayWindow):
    """The pet panel owns the hidden Tk root that runs its event loop."""
    def __init__(self):
        self._owner = tk.Tk()
        self._owner.withdraw()
        self._destroying = False
        super().__init__(self._owner)

    def destroy(self):
        if self._destroying:
            return
        self._destroying = True
        super().destroy()
        self._owner.destroy()  # also end mainloop; never leave the hidden root alive

