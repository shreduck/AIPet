#!/usr/bin/env bash
# Build AIPet.app on macOS (run on a Mac; the GitHub Actions workflow runs the same steps).
set -euo pipefail
cd "$(dirname "$0")"

if [[ $(uname -s) != Darwin ]]; then
  echo "build_mac.sh must run on macOS." >&2
  exit 1
fi

# Keep build dependencies out of system Python and prevent another runtime's
# Tcl/Tk paths (or an activated environment) from leaking into the bundle.
unset PYTHONHOME PYTHONPATH TCL_LIBRARY TK_LIBRARY
export PYTHONNOUSERSITE=1
RUNTIME_DIR="$PWD/build/mac-runtime"
VENV_DIR="$RUNTIME_DIR/venv"
# Newer standalone 3.12 builds ship Tk 9; keep the tested Tk 8.6 runtime.
MANAGED_PYTHON_VERSION="3.12.11"
mkdir -p "$RUNTIME_DIR"

usable_python() {
  "$1" - <<'PY' >/dev/null 2>&1
import pathlib
import sys
import tkinter
import _tkinter

assert sys.version_info[:2] == (3, 12)
assert _tkinter.TK_VERSION == "8.6"
tcl = pathlib.Path(tkinter.Tcl().eval("info library"))
assert (tcl / "init.tcl").is_file()
# Python.org, Homebrew and standalone distributions use these layouts.
roots = [tcl.parent / "tk8.6", pathlib.Path(sys.base_prefix) / "lib/tk8.6",
         pathlib.Path(sys.base_prefix) / "Frameworks/Tk.framework/Versions/8.6/Resources/Scripts",
         pathlib.Path("/Library/Frameworks/Tk.framework/Versions/8.6/Resources/Scripts")]
assert any((root / "tk.tcl").is_file() for root in roots)
PY
}

echo "[1/4] Setting up Python 3.12, Tcl/Tk and build dependencies..."
BASE_PY=""
if [[ -n ${PYTHON:-} ]]; then
  if ! usable_python "$PYTHON"; then
    echo "PYTHON must name a Python 3.12 executable with Tcl/Tk 8.6. Unset PYTHON for automatic setup." >&2
    exit 1
  fi
  BASE_PY="$PYTHON"
else
  for candidate in "$VENV_DIR/bin/python" python3.12 python3; do
    if usable_python "$candidate"; then
      BASE_PY="$candidate"
      break
    fi
  done
  if [[ -z $BASE_PY ]]; then
    UV="$RUNTIME_DIR/tools/uv"
    if [[ ! -x $UV ]]; then
      echo "Downloading a local Python installer..."
      curl --fail --location --silent --show-error https://astral.sh/uv/install.sh -o "$RUNTIME_DIR/uv-install.sh"
      UV_UNMANAGED_INSTALL="$RUNTIME_DIR/tools" sh "$RUNTIME_DIR/uv-install.sh"
    fi
    export UV_PYTHON_INSTALL_DIR="$RUNTIME_DIR/python"
    export UV_CACHE_DIR="$RUNTIME_DIR/uv-cache"
    "$UV" python install --no-config --no-bin "$MANAGED_PYTHON_VERSION"
    BASE_PY=$("$UV" python find --no-config --managed-python "$MANAGED_PYTHON_VERSION")
    if ! usable_python "$BASE_PY"; then
      echo "Downloaded Python did not provide usable Tcl/Tk 8.6; build stopped before packaging." >&2
      exit 1
    fi
  fi
fi

BASE_EXECUTABLE=$("$BASE_PY" -c 'import os, sys; print(os.path.realpath(sys._base_executable))')
VENV_BASE=$("$VENV_DIR/bin/python" -c 'import os, sys; print(os.path.realpath(sys._base_executable))' 2>/dev/null || true)
if [[ $VENV_BASE != "$BASE_EXECUTABLE" ]] || ! usable_python "$VENV_DIR/bin/python"; then
  # Always create from the base executable, never clear an environment while
  # using its own interpreter to create the replacement.
  "$BASE_EXECUTABLE" -m venv --clear "$VENV_DIR"
fi
PY="$VENV_DIR/bin/python"
# Standalone Tcl searches beside the venv executable when Tk starts, rather
# than beside the base Python. Give that search its own relative script links.
"$BASE_EXECUTABLE" - "$VENV_DIR" <<'PY'
import os
import pathlib
import sys
import tkinter

tcl = pathlib.Path(tkinter.Tcl().eval("info library"))
roots = [tcl.parent / "tk8.6", pathlib.Path(sys.base_prefix) / "lib/tk8.6",
         pathlib.Path(sys.base_prefix) / "Frameworks/Tk.framework/Versions/8.6/Resources/Scripts",
         pathlib.Path("/Library/Frameworks/Tk.framework/Versions/8.6/Resources/Scripts")]
tk = next(root for root in roots if (root / "tk.tcl").is_file())
library = pathlib.Path(sys.argv[1]) / "lib"
library.mkdir(exist_ok=True)
for name, source in (("tcl8.6", tcl), ("tk8.6", tk)):
    target = library / name
    if target.is_symlink():
        target.unlink()
    if not target.exists():
        target.symlink_to(os.path.relpath(source, library), target_is_directory=True)
PY
export PIP_CACHE_DIR="$RUNTIME_DIR/pip-cache"
"$PY" -m pip install --upgrade -r requirements.txt
if ! usable_python "$PY"; then
  echo "The build environment cannot load Python/Tcl/Tk; packaging was not started." >&2
  exit 1
fi

"$PY" tools/write_version.py

echo "[2/4] Building the built-in hook (console build: no window appears for a non-terminal child)..."
"$PY" -m PyInstaller --noconfirm --clean --onedir \
  --exclude-module ssl --exclude-module _ssl --exclude-module _hashlib --exclude-module certifi \
  --exclude-module _multibytecodec --exclude-module _codecs_cn --exclude-module _codecs_hk --exclude-module _codecs_iso2022 \
  --exclude-module _codecs_jp --exclude-module _codecs_kr --exclude-module _codecs_tw \
  --hidden-import _md5 --hidden-import _sha1 --hidden-import _sha2 --hidden-import _sha3 --hidden-import _blake2 \
  --exclude-module tkinter --exclude-module PIL --exclude-module unittest --exclude-module pydoc --exclude-module doctest --exclude-module pdb --exclude-module sqlite3 --exclude-module lzma \
  --exclude-module bz2 --exclude-module decimal \
  --name aipet-hook aipet_hook.py

echo "[3/4] Building AIPet.app..."
# aipet_app.spec: the bundle, icon and files, minus what AIPet never uses (keeps the app smaller)
"$PY" -m PyInstaller --noconfirm --clean aipet_app.spec

echo "[4/4] Sharing the Python runtime and verifying the app signature..."
"$PY" tools/share_mac_runtime.py dist/AIPet.app

echo
echo "Done: dist/AIPet.app (ad-hoc signed and signature-verified; not Developer ID signed or notarized)"
