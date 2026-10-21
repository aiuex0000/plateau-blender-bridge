@echo off
cd /d "%~dp0"
if exist "dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe" (
  start "" "dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe"
  exit /b 0
)
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m plateau_blend.auto_gui
  exit /b %errorlevel%
)
echo No built application found. This is the source archive.
echo Ordinary users should use PLATEAU-Blender-Bridge.exe from the Windows distribution.
pause
exit /b 1
