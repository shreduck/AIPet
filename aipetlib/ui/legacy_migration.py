"""First-start dialog when moving from Claude Pet to AIPet."""
from ..integrations import legacy
from ..integrations import cowork as hook_cowork


def migration_window(APP_NAME, set_autostart):
    """Tk window shown before the app starts (its own Tk root).
    Explain the Claude Pet -> AIPet changes, close the old app (or ask the user to), copy the settings.
    Returns False if the user chose to quit."""
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title(f"Claude Pet is now {APP_NAME}")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    grey, red = "#6b7280", "#b91c1c"
    p = legacy.plan()
    result = {"go": False}
    home = "~" if legacy.IS_MAC else "%USERPROFILE%"
    sep = "/" if legacy.IS_MAC else "\\"
    copied = ", ".join(p["copy"]) or "nothing to copy"
    lines = [
        ("Settings folder", f"{home}{sep}.claude-pet  ->  {home}{sep}.aipet\nCopied: {copied}. The old folder is kept "
                            "(older hooks still write there and AIPet keeps reading it); a note inside says when "
                            "you can delete it."),
        ("Claude Code hooks", "Your settings.json files still call the old claude-pet-hook. Right after this, AIPet "
                              "offers to update them (each file is backed up first). WSL distros that aren't running "
                              "can be updated later from the menu: Claude Code hooks."),
    ]
    if p["autostart"]:
        lines.append(("Start " + ("at login" if legacy.IS_MAC else "with Windows"),
                      "Moved from Claude Pet to AIPet."))
    lines += [
        ("Cowork plugin", f"Rebuilt as {hook_cowork.COWORK_ZIP}. In the Claude app: Customize > Plugins, uninstall the old "
                          f"{hook_cowork.PLUGIN_NAME} plugin and upload the new zip (menu: Claude Code hooks > Cowork)."),
        ("Claude Code CLI plugin", "Only if you installed it: run the two commands from Claude Code hooks > Cowork "
                                   "again (its folder moved)."),
        ("The old app", ("Delete ClaudePet.app" if legacy.IS_MAC else "Delete ClaudePet.exe") + " once AIPet is running."),
    ]
    tk.Label(root, text=f"Claude Pet has a new name: {APP_NAME}", font=(legacy.UI_FONT, 13, "bold")
             ).pack(anchor="w", padx=16, pady=(14, 2))
    tk.Label(root, text="Here is what changes on this computer:", fg=grey).pack(anchor="w", padx=16)
    body = tk.Frame(root)
    body.pack(fill="x", padx=16, pady=8)
    for head, text in lines:
        tk.Label(body, text=head, font=(legacy.UI_FONT, 9, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(body, text=text, justify="left", wraplength=480, font=(legacy.UI_FONT, 9)).pack(anchor="w")
    status = tk.Label(root, fg=red, justify="left", wraplength=480, font=(legacy.UI_FONT, 9, "bold"))
    status.pack(anchor="w", padx=16)

    def refresh_status():
        status.config(text="Claude Pet is still running. AIPet will close it when you continue." if legacy.old_running()
                      else "")

    def ensure_closed():
        if not legacy.old_running():
            return True
        status.config(text="Closing Claude Pet...")
        root.update()
        while not legacy.close_old():
            if not messagebox.askretrycancel(
                    APP_NAME, "AIPet couldn't close Claude Pet by itself.\n\nQuit it yourself: right-click its "
                    + ("pet > Quit" if legacy.IS_MAC else "tray icon (near the clock) > Quit") + ", then press Retry.\n\n"
                    "Cancel quits AIPet without changing anything.", parent=root):
                return False
        return True

    def go(copy):
        if not ensure_closed():
            root.destroy()
            return
        try:
            if copy:
                done = legacy.migrate(set_new_autostart=set_autostart)
                messagebox.showinfo(APP_NAME, "Done:\n\n" + ("\n".join("- " + d for d in done) or "- nothing to copy")
                                    + "\n\nNext, AIPet offers to update your hooks.", parent=root)
            else:
                legacy.mark_done()
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Copying the old settings failed:\n{e}\n\nAIPet starts with default "
                                           "settings; the old folder is untouched.", parent=root)
            legacy.mark_done()
        result["go"] = True
        root.destroy()

    row = tk.Frame(root)
    row.pack(fill="x", padx=16, pady=(8, 14))
    tk.Button(row, text="Continue", width=12, default="active", command=lambda: go(True)).pack(side="right")
    tk.Button(row, text="Start fresh (don't copy)", command=lambda: go(False)).pack(side="right", padx=8)
    tk.Button(row, text="Quit", width=8, command=root.destroy).pack(side="left")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    refresh_status()
    root.update_idletasks()
    root.geometry(f"+{max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)}"
                  f"+{max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 3)}")
    root.mainloop()
    return result["go"]
