#!/usr/bin/env python3
"""Drive the opt-in app probe; record real Space switching separately from flag checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import tkinter as tk

import Quartz  # CI-only dependency, not shipped with AIPet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records = []
    counter = 0
    marker = tk.Tk()
    marker.title("AIPet Space marker")
    marker.geometry("240x80+40+80")
    tk.Label(marker, text="Original desktop marker\nSpace test in progress").pack(padx=16, pady=16)
    marker.update()
    marker_windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    marker_number = next((w["kCGWindowNumber"] for w in marker_windows
                          if w.get("kCGWindowOwnerPID") == os.getpid() and w.get("kCGWindowLayer") == 0
                          and w.get("kCGWindowBounds", {}).get("Width", 0) >= 200), None)

    def request(label, **changes):
        nonlocal counter
        counter += 1
        data = {"id": str(counter), **changes}
        temp = output / "request.tmp"
        temp.write_text(json.dumps(data), encoding="utf-8")
        temp.replace(output / "request.json")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            marker.update()
            try:
                response = json.loads((output / "response.json").read_text(encoding="utf-8"))
                if response.get("id") == data["id"]:
                    (output / f"{label}.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
                    records.append({"label": label, "result": response})
                    return response
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(0.1)
        raise RuntimeError(f"App did not answer {label}")

    def screenshot(label):
        subprocess.run(["screencapture", "-x", str(output / f"{label}.png")], timeout=15, check=False)

    def onscreen(pid, window=None):
        windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        if windows is None:
            raise RuntimeError("No GUI window server session")
        return any(w.get("kCGWindowOwnerPID") == pid and
                   (window is None or w.get("kCGWindowNumber") == window) for w in windows)

    def desktop(action):
        # The Dock accessibility tree on macOS 14 exposes desktop buttons.
        # Click the thumbnails directly; keyboard shortcuts may be disabled.
        subprocess.run(["open", "-a", "Mission Control"], check=True, timeout=10)
        time.sleep(1.5)
        script = '''tell application "System Events"
tell process "Dock"
set spacesBar to group "Spaces Bar" of group 1 of group "Mission Control"
'''
        if action == "create":
            script += '''click (first button of spacesBar whose description is "add desktop")
delay 1
set desktopCount to count of buttons of list 1 of spacesBar
click first button of list 1 of spacesBar
return desktopCount
'''
        elif action == "second":
            script += "click last button of list 1 of spacesBar\nreturn \"selected last desktop\"\n"
        else:
            script += "click first button of list 1 of spacesBar\nreturn \"selected first desktop\"\n"
        script += "end tell\nend tell"
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
        with (output / "desktop-automation.log").open("a", encoding="utf-8") as f:
            f.write(f"{action}: exit={result.returncode}\n{result.stdout}\n{result.stderr}\n")
        if result.returncode:
            subprocess.run(["osascript", "-e", 'tell application "System Events" to key code 53'], timeout=10, check=False)
            raise RuntimeError(result.stderr.strip() or "Mission Control automation failed")
        time.sleep(2)
        return result.stdout.strip()

    def check_flags(label, expected, **changes):
        data = request(label, all_spaces=expected, **changes)
        passed = (not data.get("error") and data.get("can_join_all_spaces") == expected and
                  (not expected or (data.get("fullscreen_auxiliary") and not data.get("conflicting_flags"))))
        records.append({"check": label + " flags", "status": "passed" if passed else "failed"})
        screenshot(label)
        return data

    try:
        update = request("update-https", action="update")
        records.append({"check": "HTTPS update check", "status": "failed" if update.get("error") else "passed"})
        baseline = check_flags("all-desktops-on", True)
        check_flags("all-desktops-off", False)
        check_flags("off-after-remap", False, remap=True)
        check_flags("on-after-remap", True, remap=True)
        check_flags("compact-resized", True, compact=True)
        check_flags("expanded-resized", True, compact=False)
        positions = []
        for label, scenario, state in (("position-idle-before", "idle", "idle"),
                                        ("position-working", "single", "working"),
                                        ("position-done", "single", "done"),
                                        ("position-needs-input", "single", "needs_input"),
                                        ("position-idle-after", "idle", "idle")):
            data = request(label, scenario=scenario, state=state)
            coords = list(data.get("pet_positions", {}).values())
            positions.append(coords[0] if len(coords) == 1 else None)
            screenshot(label)
        stable = all(p is not None and all(abs(a - b) <= 1 for a, b in zip(p, positions[0])) for p in positions) if positions[0] is not None else False
        records.append({"check": "pet position across idle / working / done / input", "status": "passed" if stable else "failed", "positions": positions})
        baseline = request("sessions-restored", scenario="all", state="working")
        desktop_ready = False
        try:
            if marker_number is None or not onscreen(os.getpid(), marker_number):
                raise RuntimeError("Original desktop marker was not detected")
            count = int(desktop("create"))
            if count < 2:
                raise RuntimeError(f"Only {count} desktop found")
            desktop("second")
            if onscreen(os.getpid(), marker_number):
                raise RuntimeError("Marker is still on screen: desktop switch was not verified")
            desktop_ready = True
        except Exception as e:
            records.append({"check": "actual desktop switching", "status": "skipped", "reason": str(e)})
        if desktop_ready:
            data = request("on-second-desktop")
            visible = onscreen(baseline["pid"], data["window_number"])
            records.append({"check": "visible on second desktop when enabled", "status": "passed" if visible else "failed"})
            screenshot("on-second-desktop")
            desktop("first")
            check_flags("off-original-desktop", False)
            desktop("second")
            if onscreen(os.getpid(), marker_number):
                raise RuntimeError("Second desktop switch failed; cannot test visibility")
            data = request("off-second-desktop")
            visible = onscreen(baseline["pid"], data["window_number"])
            records.append({"check": "hidden on second desktop when disabled", "status": "passed" if not visible else "failed"})
            screenshot("off-second-desktop")
            desktop("first")
            check_flags("restored-original-desktop", True)
        request("about", about=True)
        screenshot("about")
    except Exception as e:
        records.append({"check": "probe execution", "status": "failed", "reason": repr(e)})
    finally:
        # On an ephemeral Actions VM a created desktop can be left for teardown.
        # Returning is best effort, including when accessibility was denied.
        try:
            desktop("first")
        except Exception:
            pass
        (output / "request.json").write_text(json.dumps({"id": "quit", "action": "quit"}), encoding="utf-8")
        marker.destroy()
        (output / "summary.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        for record in records:
            if "check" in record:
                print(f"{record['status'].upper()}: {record['check']} {record.get('reason', '')}")
    return 1 if any(r.get("status") == "failed" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
