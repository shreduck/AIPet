# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for AIPet (Windows: a portable folder) and AIPet.app (macOS). Used by build.bat and build_mac.sh:
#     python -m PyInstaller --noconfirm --clean aipet_app.spec
# Windows: one folder, dist/AIPet, with AIPet.exe and aipet-hook.exe sharing one Python runtime (_internal), instead
# of a single exe that carried a second, private runtime for the hook. hook_files.txt lists the runtime files the hook
# needs; on Install the app copies aipet-hook.exe plus those files to ~/.aipet/bin/hook (hooks_installer.deploy_files),
# so the installed hooks keep working wherever the folder is moved. macOS still bundles the separately built hook.
# It does what the old command lines did, minus what AIPet never uses, to keep the download small:
# - Pillow's AVIF, WebP, colour-management, maths and FreeType modules (AIPet only reads and writes PNG / ICO and draws
#   shapes; Pillow skips image formats whose modules are missing, and ImageFont only fails if text is actually drawn);
# - test / documentation tools of the standard library, and compression formats nothing here reads;
# - Tcl's time-zone database and translations and Tk's demo images (AIPet never formats dates through Tcl, and its
#   dialogs are its own, in English).
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

IS_MAC = sys.platform == "darwin"
HOOK = os.path.join("dist", "aipet-hook")  # built first, as its own small program (see build.bat / build_mac.sh)

EXCLUDES = [
    # Pillow features AIPet doesn't use
    "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp", "PIL.WebPImagePlugin", "PIL._imagingcms", "PIL.ImageCms",
    "PIL._imagingmath", "PIL.ImageMath", "PIL._imagingft", "PIL.ImageQt", "numpy",
    # standard-library tools that never run inside the app
    "unittest", "doctest", "pydoc", "pdb", "lib2to3", "idlelib", "turtle", "turtledemo", "tkinter.test",
    "distutils", "setuptools", "pip", "sqlite3",
    # compression formats nothing reads (zipfile / shutil fall back without them)
    "lzma", "_lzma", "bz2", "_bz2",
]

# The hook needs none of the window toolkit or images
HOOK_EXCLUDES = ["tkinter", "_tkinter", "PIL", "pystray", "pyvda", "comtypes", "decimal"]

# Folders inside Tcl's and Tk's own library trees that AIPet doesn't need
TCL_TK_ROOTS = {"_tcl_data", "_tk_data", "tcl", "tk"}
TCL_TK_SKIP = {"tzdata", "msgs", "images", "demos"}


# Tcl modules (tcl8/8.x/*.tm) nothing loads: Tcl's test framework and its HTTP client
TCL_MODULE_SKIP = ("tcltest-", "http-")


def slim(toc):
    kept = []
    for entry in toc:
        parts = entry[0].replace("\\", "/").split("/")
        if parts[0] in TCL_TK_ROOTS and TCL_TK_SKIP.intersection(parts[1:-1]):
            continue
        if parts[0] == "tcl8" and parts[-1].startswith(TCL_MODULE_SKIP):
            continue
        kept.append(entry)
    return kept


def without_ucrt(toc):
    """Windows' Universal C Runtime (ucrtbase.dll and the api-ms-win-* forwarders) is part of Windows 10 and 11, and
    the Python inside AIPet doesn't run on anything older, so the bundled copies are never used."""
    return [entry for entry in toc
            if not (os.path.basename(entry[0]).lower() == "ucrtbase.dll"
                    or os.path.basename(entry[0]).lower().startswith("api-ms-win-"))]


datas = ([(HOOK, "hook")] if IS_MAC else []) + [("aipet_hook.py", "."), ("aipet_usage.py", "."), ("aipet_claude_usage.py", "."),
         (os.path.join("assets", "sprites"), os.path.join("assets", "sprites"))]
datas += collect_data_files("certifi")
hiddenimports = [] if IS_MAC else ["pystray._win32"] + collect_submodules("pyvda")

a = Analysis(
    ["aipet_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.datas = slim(a.datas)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="AIPet",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=False,
        icon=[os.path.join("assets", "aipet.icns")],
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AIPet")
    app = BUNDLE(coll, name="AIPet.app", icon=os.path.join("assets", "aipet.icns"),
                 bundle_identifier="com.aipet.app")
else:
    # the hook: a second program in the same folder, sharing the app's runtime
    h = Analysis(
        ["aipet_hook.py"],
        pathex=[],
        binaries=[],
        datas=collect_data_files("certifi"),
        hiddenimports=[],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=EXCLUDES + HOOK_EXCLUDES,
        noarchive=False,
        optimize=0,
    )
    hook_pyz = PYZ(h.pure)
    a.binaries = without_ucrt(a.binaries)
    h.binaries = without_ucrt(h.binaries)
    # What the hook needs from _internal, for the copy in ~/.aipet/bin/hook
    needed = sorted({entry[0].replace("\\", "/") for entry in h.binaries + h.datas} | {"base_library.zip"})
    manifest = os.path.join(SPECPATH, "build", "hook_files.txt")
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("\n".join(needed) + "\n")
    a.datas += [("hook_files.txt", manifest, "DATA")]

    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="AIPet",
        debug=False,
        strip=False,
        upx=False,  # UPX-packed exes trip antivirus false alarms more often
        console=False,
        icon=[os.path.join("assets", "aipet.ico")],
    )
    hook_exe = EXE(
        hook_pyz,
        h.scripts,
        [],
        exclude_binaries=True,
        name="aipet-hook",
        debug=False,
        strip=False,
        upx=False,
        console=False,  # no console window flashes when Claude Code / Codex run it
        icon=[os.path.join("assets", "aipet.ico")],  # the robot, in Explorer and Task Manager too
    )
    coll = COLLECT(exe, hook_exe, a.binaries, a.datas, h.binaries, h.datas, strip=False, upx=False, name="AIPet")
