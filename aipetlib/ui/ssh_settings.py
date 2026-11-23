"""Settings for saved SSH connections and remote hook installation."""
from concurrent.futures import Future
import threading
import tkinter as tk
from tkinter import ttk

from ..integrations import ssh
from . import theme


class SSHSettings:
    def __init__(self, parent, connections):
        self.connections = connections
        self.selected = None
        self.busy = False
        self._refresh_job = self._save_job = None
        self.win = win = tk.Toplevel(parent)
        win.title("SSH connections")
        win.minsize(520, 380)
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        win.bind("<Escape>", lambda _event: win.destroy())
        win.bind("<Destroy>", self._on_destroy, add="+")

        self.list = ttk.Treeview(win, columns=("status",), show="tree headings", height=5, selectmode="browse")
        self.list.heading("#0", text="Connection")
        self.list.heading("status", text="Status")
        self.list.column("#0", width=175)
        self.list.column("status", width=335)
        self.list.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 4))
        self.list.bind("<<TreeviewSelect>>", self._selected)

        form = tk.Frame(win)
        form.grid(row=1, column=0, sticky="ew", padx=12, pady=6)
        form.columnconfigure(1, weight=1)
        self.url = tk.StringVar(master=win)
        self.user = tk.StringVar(master=win)
        self.password = tk.StringVar(master=win)
        self.fingerprint = tk.StringVar(master=win)
        self.enabled = tk.BooleanVar(master=win, value=True)
        self.note = tk.StringVar(master=win, value=connections.load_error)
        for row, (label, variable) in enumerate((("Host or SSH URL", self.url), ("Username", self.user),
                                                  ("Password", self.password), ("Host fingerprint", self.fingerprint))):
            tk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            entry = tk.Entry(form, textvariable=variable, show="*" if variable is self.password else "")
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            if row == 0:
                self.host_entry = entry
        tk.Label(form, text="Use a verified SHA256 fingerprint or an existing SSH known_hosts entry.",
                 anchor="w", justify="left", wraplength=380).grid(row=4, column=1, sticky="w")
        tk.Checkbutton(form, text="Enabled", variable=self.enabled).grid(row=5, column=1, sticky="w")
        tk.Label(win, textvariable=self.note, anchor="w", justify="left", wraplength=485).grid(
            row=2, column=0, sticky="ew", padx=12)

        buttons = tk.Frame(win)
        buttons.grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 12))
        self.buttons = []
        for name, action in (("New", self._new), ("Save", self._save), ("Remove", self._remove),
                             ("Reconnect", self._reconnect), ("Install hooks", self._install)):
            btn = tk.Button(buttons, text=name, command=action)
            btn.pack(side="left", padx=(0, 6))
            self.buttons.append(btn)
        tk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
        theme.theme_window(win)
        self._tick()
        self.host_entry.focus_set()

    def _refresh(self):
        profiles = self.connections.profiles()
        active = {p["id"] for p in profiles}
        for key in self.list.get_children():
            if key not in active:
                self.list.delete(key)
        for profile in profiles:
            key = profile["id"]
            values = (self.connections.status(key),)
            if self.list.exists(key):
                self.list.item(key, text=ssh.label(profile), values=values)
            else:
                self.list.insert("", "end", iid=key, text=ssh.label(profile), values=values)
        enabled = any(p["id"] == self.selected and p["enabled"] for p in profiles)
        for button, allowed in zip(self.buttons, (True, not self.connections.load_error, bool(self.selected),
                                                  enabled, enabled)):
            button.config(state="normal" if allowed and not self.busy else "disabled")

    def _tick(self):
        self._refresh()
        self._refresh_job = self.win.after(500, self._tick)

    def _on_destroy(self, event):
        if event.widget is self.win:
            for job in (self._refresh_job, self._save_job):
                if job:
                    self.win.after_cancel(job)

    def show(self, connection=None):
        if self.win.winfo_exists():
            self.win.lift()
            if connection and self.list.exists(connection) and not self.busy:
                self.list.selection_set(connection)
                self.list.see(connection)
                self._selected(None)

    def _new(self):
        self.selected = None
        self.list.selection_remove(*self.list.selection())
        for variable in (self.url, self.user, self.password, self.fingerprint):
            variable.set("")
        self.enabled.set(True)
        self.note.set(self.connections.load_error)
        self.host_entry.focus_set()
        self._refresh()

    def _selected(self, _event):
        if self.busy:
            return
        selected = self.list.selection()
        key = selected[0] if selected else None
        if not key or key == self.selected:
            return
        profile = next((p for p in self.connections.profiles() if p["id"] == key), None)
        if profile:
            self.selected = key
            self.url.set(ssh.address_label(profile))
            self.user.set(profile["user"])
            self.password.set("")
            self.fingerprint.set(profile["fingerprint"])
            self.enabled.set(profile["enabled"])
            self.note.set("Leave the password blank to keep the saved password.")
            self._refresh()

    def _background(self, action, completed):
        self.busy = True
        self._refresh()
        result = Future()

        def run():
            try:
                result.set_result(action())
            except Exception as exc:
                result.set_exception(exc)

        def check():
            if not result.done():
                self._save_job = self.win.after(80, check)
                return
            self._save_job = None
            self.busy = False
            try:
                completed(result.result())
            except ValueError as exc:
                self.note.set(str(exc))
            except OSError:
                self.note.set("Could not update the saved SSH connections.")
            self._refresh()

        threading.Thread(target=run, daemon=True).start()
        self._save_job = self.win.after(80, check)

    def _save(self):
        values = (self.selected, self.url.get(), self.user.get(), self.password.get(),
                  self.fingerprint.get(), self.enabled.get())
        self.password.set("")

        def saved(key):
            self.selected = key
            self._refresh()
            self.list.selection_set(key)
            self.note.set("Saved. Install hooks to set up this host." if self.enabled.get() else "Saved. Connection disabled.")

        self._background(lambda: self.connections.save(*values), saved)

    def _remove(self):
        key = self.selected
        self._background(lambda: self.connections.remove(key), lambda _result: self._new())

    def _reconnect(self):
        self.connections.action(self.selected, reconnect=True)
        self.note.set("Reconnect requested.")

    def _install(self):
        self.connections.action(self.selected, install=True)
        self.note.set("Watch the connection status. Restart remote sessions after installation.")
