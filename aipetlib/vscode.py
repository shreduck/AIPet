"""VS Code window and conversation routing.

Imports no Tk and no application entry point.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

from .runtime import diagnostics

IS_MAC = sys.platform == "darwin"
VSCODE_FLAVOURS = ("Code", "Code - Insiders", "VSCodium", "Cursor")


def vscode_user_dirs():
    if os.name == "nt":
        base = os.environ.get("APPDATA", "")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return [os.path.join(base, f) for f in VSCODE_FLAVOURS if base and os.path.isdir(os.path.join(base, f))]


def _uri_parts(uri):
    """file:///c%3A/x -> ("", "c:/x"); vscode-remote://wsl%2Bubuntu/home/x -> ("wsl+ubuntu", "/home/x")."""
    import urllib.parse
    u = urllib.parse.urlparse(uri)
    path = urllib.parse.unquote(u.path)
    if u.scheme == "file":
        if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return "", path
    if u.scheme == "vscode-remote":
        return urllib.parse.unquote(u.netloc).lower(), path
    return None, None


def _backup_uris(b):
    """(uri, is_workspace_file) from VS Code's backup-workspaces record, in any of its formats."""
    if not isinstance(b, dict):
        return []
    uris = [(x.get("folderUri"), False) for x in b.get("folders") or [] if isinstance(x, dict)]
    uris += [(x, False) for x in b.get("folderURIWorkspaces") or [] if isinstance(x, str)]  # older format
    for key in ("workspaces", "rootURIWorkspaces"):  # current format / older one
        for x in b.get(key) or []:
            if isinstance(x, dict):
                ws = x.get("workspace") if isinstance(x.get("workspace"), dict) else x
                uris.append((ws.get("configURIPath") or ws.get("configPath"), True))
    return uris


def vscode_open_folders(user_dirs=None, report=None):
    """Folders and .code-workspace files open in VS Code windows: [(authority, path, is_workspace_file)].

    Sources, freshest first: globalStorage/storage.json "backupWorkspaces" (current VS Code keeps the open windows
    there, updated as they open and close), Backups/workspaces.json (older VS Code), then the window state saved in
    storage.json (only written when VS Code quits or a window closes, so it can be stale: macOS keeps VS Code running
    with its windows closed). report, if a list, receives one line per source (counts only, for diagnostics)."""
    found = []
    for d in user_dirs if user_dirs is not None else vscode_user_dirs():
        uris, storage = [], {}
        try:
            with open(os.path.join(d, "User", "globalStorage", "storage.json"), encoding="utf-8") as f:
                storage = json.load(f) or {}
        except (OSError, ValueError):
            pass
        live = _backup_uris(storage.get("backupWorkspaces"))
        legacy_file = []
        try:
            with open(os.path.join(d, "Backups", "workspaces.json"), encoding="utf-8") as f:
                legacy_file = _backup_uris(json.load(f))
        except (OSError, ValueError):
            pass
        uris = live + legacy_file
        saved = []
        try:
            ws = storage.get("windowsState") or {}
            for w in [ws.get("lastActiveWindow") or {}] + list(ws.get("openedWindows") or []):
                if not isinstance(w, dict):
                    continue
                if w.get("folder"):
                    saved.append((w["folder"], False))
                elif isinstance(w.get("workspace"), dict) and w["workspace"].get("configPath"):
                    saved.append((w["workspace"]["configPath"], True))
        except AttributeError:
            pass
        if not uris:
            uris = saved
        if isinstance(report, list):
            report.append(f"{os.path.basename(d)}: backupWorkspaces={len(live)} workspaces.json={len(legacy_file)} "
                          f"windowsState={len(saved)}")
        for uri, is_ws in uris:
            if isinstance(uri, str):
                auth, path = _uri_parts(uri)
                if path:
                    found.append((auth, path, is_ws))
    return found


def _workspace_roots(ws_path):
    """Folder paths listed in a local .code-workspace file (relative ones resolved against the file)."""
    try:
        with open(ws_path, encoding="utf-8") as f:
            text = f.read()
        import re
        text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text,
                      flags=re.S)  # .code-workspace allows // and /* */ comments (kept inside strings)
        text = re.sub(r",(\s*[}\]])", r"\1", text)  # ... and trailing commas
        data = json.loads(text)
    except (OSError, ValueError):
        return []
    roots = []
    for fo in data.get("folders") or []:
        p = fo.get("path") if isinstance(fo, dict) else None
        if p:
            roots.append(os.path.normpath(os.path.join(os.path.dirname(ws_path), p)))
    return roots


def vscode_target(cwd, env="", distro="", user_dirs=None):
    """What to pass to `code` so it raises the window that already holds cwd: the deepest open folder that is cwd or
    one of its parents (so a session in a subfolder of an open workspace doesn't open a new window), or the
    .code-workspace file whose folders contain it. Falls back to cwd itself. Returns (args, path)."""
    wsl = env == "wsl"
    want_auth = f"wsl+{(distro or 'ubuntu').lower()}" if wsl else ""
    if wsl:
        norm = lambda p: p.rstrip("/") or "/"  # noqa: E731
    elif IS_MAC or sys.platform == "darwin":
        # macOS: the default file system ignores case, and folders are often reached through symlinks (/var ->
        # /private/var, a renamed or linked ~/Code); compare resolved, case-folded paths
        norm = lambda p: os.path.normpath(os.path.realpath(p)).casefold()  # noqa: E731
    else:
        norm = lambda p: os.path.normcase(os.path.normpath(p))  # noqa: E731
    target = norm(cwd)

    def inside(root):
        r = norm(root)
        sep = "/" if wsl else os.sep
        return target == r or target.startswith(r.rstrip(sep) + sep)
    best = None  # (depth, path, is_workspace_file)
    for auth, path, is_ws in vscode_open_folders(user_dirs):
        if (auth or "") != want_auth:
            continue
        if is_ws:
            if wsl:
                continue
            roots = [r for r in _workspace_roots(path) if inside(r)]
            if roots:
                depth = max(len(norm(r)) for r in roots)
                if not best or depth > best[0]:
                    best = (depth, path, True)
        elif inside(path):
            if not best or len(norm(path)) > best[0]:
                best = (len(norm(path)), path, False)
    path = best[1] if best else cwd
    return (["--remote", f"wsl+{distro or 'Ubuntu'}", path] if wsl else [path]), path


def vscode_conversation_uri(d, code_path=""):
    """vscode://anthropic.claude-code/open?session=<id>: the Claude Code extension (v2.1.72+) opens that conversation's
    tab. Only for sessions that run in the extension (entry point claude-vscode), not the CLI in VS Code's terminal,
    where it would open a second copy of the conversation in the extension."""
    sid = d.get("session_id") or ""
    if not sid or sid == "unknown" or "vscode" not in (d.get("entry") or "").lower():
        return None
    import urllib.parse
    scheme = "vscode-insiders" if "insiders" in (code_path or "").lower() else "vscode"
    return f"{scheme}://anthropic.claude-code/open?session={urllib.parse.quote(sid, safe='')}"


def open_uri(uri):
    try:
        if os.name == "nt":
            os.startfile(uri)
        else:
            subprocess.Popen(["open" if IS_MAC else "xdg-open", uri])
    except (AttributeError, OSError) as e:
        diagnostics.log_error(f"couldn't open {uri.split('?')[0]}: {e!r}")
