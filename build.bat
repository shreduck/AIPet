@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")

echo [1/3] Installing build dependencies...
%PY% -m pip install --upgrade -r requirements.txt || goto :err

echo [2/3] Building the hook (no console, fast start)...
%PY% -m PyInstaller --noconfirm --clean --windowed --onedir --name claude-pet-hook claude_pet_hook.py || goto :err

echo [3/3] Building ClaudePet.exe...
%PY% -m PyInstaller --noconfirm --clean --windowed --onefile --name ClaudePet ^
  --icon assets\claude_pet.ico ^
  --add-data "dist\claude-pet-hook;hook" ^
  --add-data "claude_pet_hook.py;." ^
  --add-data "assets\sprites;assets\sprites" ^
  --hidden-import pystray._win32 ^
  claude_pet_app.py || goto :err

echo.
echo Done: dist\ClaudePet.exe
exit /b 0

:err
echo.
echo Build failed.
exit /b 1
