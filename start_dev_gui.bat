@echo off
setlocal
cd /d "%~dp0"
set "PYTHON="
if exist "C:\apps\PLATEAU-Blender-Bridge\.venv\Scripts\pythonw.exe" set "PYTHON=C:\apps\PLATEAU-Blender-Bridge\.venv\Scripts\pythonw.exe"
if not defined PYTHON if exist "C:\apps\PLATEAU-Blender-Bridge\.venv\Scripts\python.exe" set "PYTHON=C:\apps\PLATEAU-Blender-Bridge\.venv\Scripts\python.exe"
if not defined PYTHON if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not defined PYTHON if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON goto no_venv
"%PYTHON%" -m plateau_blend.auto_gui
exit /b %errorlevel%
:no_venv
echo Developer virtual environment was not found.
echo Run dev_setup_windows.bat from this source checkout, then retry start_dev_gui.bat.
exit /b 1