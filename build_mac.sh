#!/usr/bin/env bash
# Build ClaudePet.app on macOS (run on a Mac; the GitHub Actions workflow runs the same steps).
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
echo "[1/3] Installing build dependencies..."
$PY -m pip install --upgrade pyinstaller pillow

echo "[2/3] Building the built-in hook (console build: no window appears for a non-terminal child)..."
$PY -m PyInstaller --noconfirm --clean --onedir --name claude-pet-hook claude_pet_hook.py

echo "[3/3] Building ClaudePet.app..."
$PY -m PyInstaller --noconfirm --clean --windowed --name ClaudePet \
  --osx-bundle-identifier com.claudepet.app \
  --add-data "dist/claude-pet-hook:hook" \
  --add-data "claude_pet_hook.py:." \
  --add-data "assets/sprites:assets/sprites" \
  claude_pet_app.py

echo
echo "Done: dist/ClaudePet.app (unsigned: first launch needs right-click > Open)"
