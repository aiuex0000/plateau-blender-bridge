@echo off
setlocal
set "APP=C:\Users\You\Workspace\Workbench\Projects\PLATEAU-Blender-Bridge\app\PLATEAU-Blender-Bridge.exe"
if exist "%APP%" goto launch
 echo PLATEAU-Blender-Bridge.exe was not found at:
 echo %APP%
 echo For source development, use start_dev_gui.bat.
 exit /b 1
:launch
start "" "%APP%"
exit /b 0