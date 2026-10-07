"""Opt-in CI control for inspecting the actual packaged pet window on macOS."""
import json
import os
import threading
import time
from pathlib import Path


def start_spaces_probe(app, output):
    import mac_statusbar as mac
    import aipet_update as updates

    os.makedirs(output, exist_ok=True)
    request_path = os.path.join(output, "request.json")
    response_path = os.path.join(output, "response.json")
    last_id = None

    def publish(data):
        with open(response_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(response_path + ".tmp", response_path)

    def snapshot(request):
        try:
            w = mac.window_titled(app.root.title())
            if not w:
                raise RuntimeError("pet NSWindow not found")
            flags = mac.send(w, "collectionBehavior", restype=mac.c_ulong)
            data = {"id": request["id"], "pid": os.getpid(), "time": time.time(),
                    "all_spaces": app.pet.cfg.get("all_spaces"), "collection_behavior": flags,
                    "can_join_all_spaces": bool(flags & 1), "fullscreen_auxiliary": bool(flags & (1 << 8)),
                    "conflicting_flags": flags & mac.ALL_SPACES_CONFLICTS,
                    "window_number": mac.send(w, "windowNumber", restype=mac.c_long),
                    "on_active_space": bool(mac.send(w, "isOnActiveSpace", restype=mac.c_bool)),
                    "visible": bool(mac.send(w, "isVisible", restype=mac.c_bool)),
                    "tk_window_style": str(app.root.tk.call("::tk::unsupported::MacWindowStyle", "style", app.root._w)),
                    "canvas_sizes": [(p.canvas.winfo_width(), p.canvas.winfo_height()) for p in app.pet.pets.values()]}
            import aipet as core
            data["pet_positions"] = {key: [p.canvas.winfo_rootx() + core.px(core.BADGE_GUTTER + getattr(p, "gutter", 0) + core.PET_W / 2),
                                            p.canvas.winfo_rooty() + core.px(90 - core.TOP_TRIM)]
                                     for key, p in app.pet.pets.items()}
            publish(data)
        except Exception as e:
            publish({"id": request["id"], "error": repr(e)})

    def update_probe(request):
        try:
            result = updates.latest_release()
            publish({"id": request["id"], "release": result})
        except Exception as e:
            publish({"id": request["id"], "error": repr(e)})

    def poll():
        nonlocal last_id
        try:
            with open(request_path, encoding="utf-8") as f:
                request = json.load(f)
            if request.get("id") and request["id"] != last_id:
                last_id = request["id"]
                if request.get("action") == "quit":
                    app.quit()
                    return
                if request.get("action") == "update":
                    threading.Thread(target=update_probe, args=(request,), daemon=True).start()
                else:
                    if request.get("scenario"):
                        import aipet as core
                        folder = Path(core.SESSIONS_DIR)
                        # Only seeded CI fixtures are moved; restore after each scenario.
                        for path in folder.glob("selftest-*.json.disabled"):
                            path.rename(path.with_suffix(""))
                        scenario = request["scenario"]
                        for path in folder.glob("selftest-*.json"):
                            if scenario == "idle" or (scenario == "single" and path.stem != "selftest-working"):
                                path.rename(path.with_suffix(".json.disabled"))
                            elif request.get("state"):
                                rec = json.loads(path.read_text(encoding="utf-8"))
                                rec.update(state=request["state"], updated=time.time(), changed=time.time())
                                path.write_text(json.dumps(rec), encoding="utf-8")
                    if "all_spaces" in request:
                        app.pet.set_all_spaces(request["all_spaces"])
                    if "compact" in request:
                        app.pet.toggle_compact(request["compact"])
                    if request.get("about"):
                        app.show_about()
                    if request.get("remap"):
                        app.root.withdraw()
                        app.root.after(100, app.root.deiconify)
                    app.root.after(1000, lambda r=request: snapshot(r))
        except FileNotFoundError:
            pass
        except Exception as e:
            publish({"id": last_id, "error": repr(e)})
        app.root.after(200, poll)

    app.root.after(1000, poll)
    app.root.after(240000, app.quit)  # a failed controller cannot leave the runner hanging
