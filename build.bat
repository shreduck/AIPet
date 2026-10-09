@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")

echo [1/3] Installing build dependencies...
%PY% -m pip install --upgrade -r requirements.txt || goto :err

echo Version...
%PY% tools\write_version.py || goto :err

echo [2/3] Building the hook (no console, fast start)...
%PY% -m PyInstaller --noconfirm --clean --windowed --onedir --collect-data certifi ^
  --exclude-module tkinter --exclude-module PIL --exclude-module unittest --exclude-module pydoc --exclude-module doctest --exclude-module pdb --exclude-module sqlite3 --exclude-module lzma ^
  --exclude-module bz2 --exclude-module decimal ^
  --name aipet-hook aipet_hook.py || goto :err

echo [3/3] Building AIPet.exe...
rem aipet_app.spec: the files, icon and hidden imports, minus what AIPet never uses (keeps the exe smaller)
%PY% -m PyInstaller --noconfirm --clean aipet_app.spec || goto :err

echo.
echo Done: dist\AIPet.exe
exit /b 0

:err
echo.
echo Build failed.
exit /b 1
