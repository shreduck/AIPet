@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")

echo [1/3] Installing build dependencies...
%PY% -m pip install --upgrade -r requirements.txt || goto :err

echo [2/3] Building the hook (no console, fast start)...
%PY% -m PyInstaller --noconfirm --clean --windowed --onedir --name aipet-hook aipet_hook.py || goto :err

echo [3/3] Building AIPet.exe...
%PY% -m PyInstaller --noconfirm --clean --windowed --onefile --name AIPet ^
  --icon assets\aipet.ico ^
  --add-data "dist\aipet-hook;hook" ^
  --add-data "aipet_hook.py;." ^
  --add-data "assets\sprites;assets\sprites" ^
  --hidden-import pystray._win32 ^
  aipet_app.py || goto :err

echo.
echo Done: dist\AIPet.exe
exit /b 0

:err
echo.
echo Build failed.
exit /b 1
