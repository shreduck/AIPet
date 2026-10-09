#!/usr/bin/env bash
# Build AIPet.app on macOS (run on a Mac; the GitHub Actions workflow runs the same steps).
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
echo "[1/3] Installing build dependencies..."
$PY -m pip install --upgrade -r requirements.txt

$PY tools/write_version.py

echo "[2/3] Building the built-in hook (console build: no window appears for a non-terminal child)..."
$PY -m PyInstaller --noconfirm --clean --onedir --collect-data certifi \
  --exclude-module tkinter --exclude-module PIL --exclude-module unittest --exclude-module pydoc --exclude-module doctest --exclude-module pdb --exclude-module sqlite3 --exclude-module lzma \
  --exclude-module bz2 --exclude-module decimal \
  --name aipet-hook aipet_hook.py

echo "[3/3] Building AIPet.app..."
# aipet_app.spec: the bundle, icon and files, minus what AIPet never uses (keeps the app smaller)
$PY -m PyInstaller --noconfirm --clean aipet_app.spec

echo
echo "Done: dist/AIPet.app (unsigned: first launch needs right-click > Open)"
