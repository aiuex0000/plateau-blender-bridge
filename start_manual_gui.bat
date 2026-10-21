@echo off
cd /d "%~dp0"
if exist "dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe" (
  start "" "dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe" --manual
  exit /b 0
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m plateau_blend.gui
  exit /b %errorlevel%
)
echo Development files only. Obtain the compiled Windows ZIP first.
pause
exit /b 1
