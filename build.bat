@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")

echo [1/3] Installing build dependencies...
%PY% -m pip install --upgrade -r requirements.txt || goto :err

echo Version...
%PY% tools\write_version.py || goto :err

echo [2/3] Building the AIPet folder (AIPet.exe and aipet-hook.exe sharing one runtime)...
rem aipet_app.spec: the files, icon and hidden imports, minus what AIPet never uses (keeps the download small)
%PY% -m PyInstaller --noconfirm --clean aipet_app.spec || goto :err

echo [3/3] Zipping the portable app...
if exist dist\AIPet-windows-x64.zip del dist\AIPet-windows-x64.zip
powershell -NoProfile -Command "Compress-Archive -Path dist\AIPet -DestinationPath dist\AIPet-windows-x64.zip -Force -ErrorAction Stop" || goto :err

echo.
echo Done: dist\AIPet (run AIPet.exe in it) and dist\AIPet-windows-x64.zip
exit /b 0

:err
echo.
echo Build failed.
exit /b 1
