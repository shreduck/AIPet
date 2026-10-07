#!/usr/bin/env bash
# Build AIPet.app on macOS (run on a Mac; the GitHub Actions workflow runs the same steps).
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
echo "[1/3] Installing build dependencies..."
$PY -m pip install --upgrade pyinstaller pillow

$PY tools/write_version.py

echo "[2/3] Building the built-in hook (console build: no window appears for a non-terminal child)..."
$PY -m PyInstaller --noconfirm --clean --onedir --name aipet-hook aipet_hook.py

echo "[3/3] Building AIPet.app..."
$PY -m PyInstaller --noconfirm --clean --windowed --name AIPet \
  --osx-bundle-identifier com.aipet.app \
  --icon assets/aipet.icns \
  --add-data "dist/aipet-hook:hook" \
  --add-data "aipet_hook.py:." \
  --add-data "assets/sprites:assets/sprites" \
  aipet_app.py

echo
echo "Done: dist/AIPet.app (unsigned: first launch needs right-click > Open)"
