@echo off
setlocal
cd /d "%~dp0"
echo [DEV ONLY] Producing portable exe in an isolated .build-venv
where py >nul 2>&1
if errorlevel 1 (echo Python launcher needed only for developers building the exe.& exit /b 1)
if not exist ".build-venv\Scripts\python.exe" (
  py -3 -m venv .build-venv
  if errorlevel 1 exit /b 1
)
".build-venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel pyinstaller pyinstaller-hooks-contrib .
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean packaging\plateau_bridge.spec
if errorlevel 1 exit /b 1
"dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe" --self-check frozen-check.json
if errorlevel 1 (echo Frozen self-check failed; see frozen-check.json& exit /b 1)
echo Build created at dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe
pause
