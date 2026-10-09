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
rem Python's zipfile writes standard "/" paths (PowerShell 5's Compress-Archive writes "\", which some unzip tools
rem turn into odd file names); a zip that is open elsewhere (7-Zip, Explorer preview) makes this step fail
%PY% -c "import shutil; shutil.make_archive('dist/AIPet-windows-x64', 'zip', 'dist', 'AIPet')" || goto :err

echo.
echo Done: dist\AIPet (run AIPet.exe in it) and dist\AIPet-windows-x64.zip
exit /b 0

:err
echo.
echo Build failed.
exit /b 1
