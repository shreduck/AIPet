#!/usr/bin/env python3
"""Architecture checks for the AIPet source tree (restructure.md, stage 0). Standard library only, no Tk.

    python tools/check_architecture.py                   # exit 1 on a violation
    python tools/check_architecture.py --update-baseline # accept the current counts (review the diff!)

Rules
  1. aipetlib never imports an application entry point or a compatibility facade (aipet, aipet_app, ...).
  2. Headless modules (agent backend, runtime, config, records, ...) never reach Tk, Pillow or the tray, directly or
     through other repository modules, so background work and tests run without a display.
  3. The hook (aipet_hook.py and what it imports from this repository) uses only the standard library and only the
     source files hooks_installer.deploy_files copies next to it.
  4. Modules that have been given explicit interfaces never take or use a `services` object again.
  5. Ratchets on the transitional wiring; counts may only go down:
       services_core     `services.core.X` reaches back into aipet.py from aipetlib
       module_services   `sys.modules[__name__]` passes a whole entry module as the services object
       services_refs     `services.X` lookups in aipetlib: what components still reach through the services object
     Lower counts are reported so the baseline can be tightened with --update-baseline.
"""
import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("architecture_baseline.json")

ENTRY_MODULES = {"aipet", "aipet_app", "aipet_agents", "aipet_agents_window", "aipet_settings", "aipet_guide",
                 "aipet_selftest", "hooks_installer", "legacy"}
GUI_PACKAGES = {"tkinter", "_tkinter", "PIL", "pystray", "pyvda", "comtypes", "objc", "AppKit"}
HEADLESS = ["aipetlib.agents", "aipetlib.runtime", "aipetlib.config", "aipetlib.records", "aipetlib.text",
            "aipetlib.housekeeping", "aipetlib.ui.sounds", "aipetlib.update",
            "aipetlib.workbench", "aipetlib.codex_questions", "aipetlib.gestures", "aipetlib.hook_events",
            "aipetlib.sessions", "aipetlib.permissions"]
# What hooks_installer.deploy_files copies to ~/.aipet/bin for the source hook (keep both lists in step)
HOOK_SOURCES = {"aipet_hook", "aipet_codex", "aipet_usage", "aipet_claude_usage", "aipetlib", "aipetlib.hook_events"}
HOOK_OPTIONAL = {"certifi"}  # imported inside try/except by aipet_usage; never required
# Modules with explicit interfaces (restructure.md step 3 onwards): no `services` parameter or name
SERVICE_FREE = ["aipetlib.agents", "aipetlib.runtime", "aipetlib.text", "aipetlib.ui.theme", "aipetlib.ui.sprites",
                "aipetlib.ui.font_metrics", "aipetlib.geometry", "aipetlib.windows",
                "aipetlib.vscode", "aipetlib.click_through", "aipetlib.permissions",
                "aipetlib.sessions", "aipetlib.ui.pet_style", "aipetlib.ui.pet_view", "aipetlib.ui.tooltips",
                "aipetlib.ui.appearance_controls", "aipetlib.ui.permission_card", "aipetlib.ui.agent_common",
                "aipetlib.ui.agent_prompts", "aipetlib.ui.agent_logs", "aipetlib.ui.agent_shell", "aipetlib.ui.agent_graph",
                "aipetlib.ui.agent_controls", "aipetlib.ui.agent_menu", "aipetlib.ui.sounds", "aipetlib.housekeeping", "aipetlib.update", "aipetlib.app", "aipetlib.ui.app_menus",
                "aipetlib.hook_events"]


def module_files():
    files = {}
    for path in list(ROOT.glob("*.py")) + list((ROOT / "aipetlib").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        files[".".join(parts)] = path
    return files


def imports_of(name, path, local):
    """Every module name imported anywhere in the file (also inside functions), resolved against the tree."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[:len(package.split(".")) - node.level + 1]
                base = ".".join(parts + ([base] if base else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in local)
    resolved = set()
    for full in found:
        parts = full.split(".")
        for i in range(1, len(parts) + 1):  # a package import also runs its parents' __init__
            prefix = ".".join(parts[:i])
            resolved.add(prefix if prefix in local else parts[0])
    resolved.discard(name)
    return resolved, tree


def closure(start, graph):
    seen, stack = set(), [start]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        stack.extend(graph.get(mod, ()))
    return seen


def path_to(start, target, graph):
    """A shortest import chain start -> ... -> target, for readable messages."""
    prev, queue = {start: None}, [start]
    while queue:
        mod = queue.pop(0)
        if mod == target:
            chain = []
            while mod:
                chain.append(mod)
                mod = prev[mod]
            return " -> ".join(reversed(chain))
        for nxt in sorted(graph.get(mod, ())):
            if nxt not in prev:
                prev[nxt] = mod
                queue.append(nxt)
    return target


def count_services_core(tree):
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Attribute) and node.attr == "core" and
               ((isinstance(node.value, ast.Name) and node.value.id == "services") or
                (isinstance(node.value, ast.Attribute) and node.value.attr == "services")))


def count_services_refs(tree):
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "services")


def count_module_services(tree):
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
               and node.value.attr == "modules" and isinstance(node.value.value, ast.Name)
               and node.value.value.id == "sys" and isinstance(node.slice, ast.Name) and node.slice.id == "__name__")


def analyse():
    local = module_files()
    graph, trees = {}, {}
    for name, path in local.items():
        graph[name], trees[name] = imports_of(name, path, local)
    errors = []

    for name in sorted(n for n in local if n.startswith("aipetlib")):
        bad = sorted(graph[name] & ENTRY_MODULES)
        if bad:
            errors.append(f"rule 1: {name} imports entry/facade module(s) {', '.join(bad)}")

    for prefix in HEADLESS:
        for name in sorted(n for n in local if n == prefix or n.startswith(prefix + ".")):
            reach = closure(name, graph)
            for bad in sorted(reach & (GUI_PACKAGES | ENTRY_MODULES)):
                errors.append(f"rule 2: headless {name} reaches {bad}: {path_to(name, bad, graph)}")

    stdlib = set(getattr(sys, "stdlib_module_names", ())) or None
    reach = closure("aipet_hook", graph)
    for mod in sorted(reach):
        if mod in local and mod not in HOOK_SOURCES:
            errors.append(f"rule 3: the hook needs {mod}, which deploy_files does not copy: "
                          f"{path_to('aipet_hook', mod, graph)}")
        elif mod not in local and mod not in HOOK_OPTIONAL and stdlib is not None and mod not in stdlib:
            errors.append(f"rule 3: the hook imports non-stdlib {mod}: {path_to('aipet_hook', mod, graph)}")

    for prefix in SERVICE_FREE:
        for name in sorted(n for n in local if n == prefix or n.startswith(prefix + ".")):
            if any((isinstance(node, ast.Name) and node.id == "services") or
                   (isinstance(node, ast.arg) and node.arg == "services") for node in ast.walk(trees[name])):
                errors.append(f"rule 4: {name} has an explicit interface but uses a services object")

    counts = {"services_core": {}, "module_services": {}, "services_refs": {}}
    for name, tree in trees.items():
        rel = local[name].relative_to(ROOT).as_posix()
        if name.startswith("aipetlib") and count_services_core(tree):
            counts["services_core"][rel] = count_services_core(tree)
        if name.startswith("aipetlib") and count_services_refs(tree):
            counts["services_refs"][rel] = count_services_refs(tree)
        if count_module_services(tree):
            counts["module_services"][rel] = count_module_services(tree)
    return errors, counts


def compare(counts, baseline):
    errors, lower = [], []
    for rule, now in counts.items():
        allowed = baseline.get(rule, {})
        for path, n in sorted(now.items()):
            if n > allowed.get(path, 0):
                errors.append(f"rule 5 ({rule}): {path} has {n}, baseline allows {allowed.get(path, 0)}")
        for path, n in sorted(allowed.items()):
            if now.get(path, 0) < n:
                lower.append(f"{rule}: {path} {n} -> {now.get(path, 0)}")
    return errors, lower


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    errors, counts = analyse()
    if args.update_baseline:
        BASELINE.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    ratchet, lower = compare(counts, baseline)
    errors += ratchet
    total = {rule: sum(v.values()) for rule, v in counts.items()}
    print("transitional wiring: " + ", ".join(f"{k}={v}" for k, v in total.items()))
    for line in lower:
        print(f"improved (tighten with --update-baseline): {line}")
    for line in errors:
        print(line)
    print("architecture check: " + ("FAILED" if errors else "ok"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
