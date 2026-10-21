@echo off
setlocal
cd /d "%~dp0"
echo [1/3] Python venv setup...
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
echo [2/3] Installing project, Pillow and pyproj into .venv only...
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto fail
echo [3/3] Developer Python environment is ready.
echo This is a source checkout, not the user-facing Windows distribution.
echo Run tests separately with: .venv\Scripts\python.exe -m unittest discover -s tests -v
echo For ordinary users, use the compiled executable instead.
pause
exit /b 0
:fail
echo Setup failed. Check Python 3.10+, pip, network access and above messages.
pause
exit /b 1
