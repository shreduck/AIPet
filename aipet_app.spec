# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for AIPet.exe (Windows, one file) and AIPet.app (macOS). Used by build.bat and build_mac.sh:
#     python -m PyInstaller --noconfirm --clean aipet_app.spec
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

# Folders inside Tcl's and Tk's own library trees that AIPet doesn't need
TCL_TK_ROOTS = {"_tcl_data", "_tk_data", "tcl", "tk"}
TCL_TK_SKIP = {"tzdata", "msgs", "images", "demos"}


def slim(toc):
    kept = []
    for entry in toc:
        parts = entry[0].replace("\\", "/").split("/")
        if parts[0] in TCL_TK_ROOTS and TCL_TK_SKIP.intersection(parts[1:-1]):
            continue
        kept.append(entry)
    return kept


datas = [(HOOK, "hook"), ("aipet_hook.py", "."), ("aipet_usage.py", "."), ("aipet_claude_usage.py", "."),
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
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="AIPet",
        debug=False,
        strip=False,
        upx=False,  # UPX-packed exes trip antivirus false alarms more often
        runtime_tmpdir=None,
        console=False,
        icon=[os.path.join("assets", "aipet.ico")],
    )
