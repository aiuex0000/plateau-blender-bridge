@echo off
setlocal
cd /d "%~dp0"
if not defined PLATEAU_BRIDGE_VENV set "PLATEAU_BRIDGE_VENV=%~dp0.venv"
set "PYTHON="
if exist "%PLATEAU_BRIDGE_VENV%\Scripts\pythonw.exe" set "PYTHON=%PLATEAU_BRIDGE_VENV%\Scripts\pythonw.exe"
if not defined PYTHON if exist "%PLATEAU_BRIDGE_VENV%\Scripts\python.exe" set "PYTHON=%PLATEAU_BRIDGE_VENV%\Scripts\python.exe"
if not defined PYTHON goto no_venv
"%PYTHON%" plateau_launcher.py %*
exit /b %errorlevel%
:no_venv
echo Developer virtual environment was not found.
echo Run dev_setup_windows.bat from this source checkout, then retry start_dev_gui.bat.
exit /b 1
