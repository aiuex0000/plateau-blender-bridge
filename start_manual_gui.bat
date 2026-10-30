@echo off
setlocal
set "APP=%~dp0app\PLATEAU-Blender-Bridge.exe"
if exist "%APP%" goto launch
set "APP=%~dp0dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe"
if exist "%APP%" goto launch
 echo PLATEAU-Blender-Bridge.exe was not found at:
 echo %~dp0app\PLATEAU-Blender-Bridge.exe
 echo %~dp0dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe
 echo For source development, use start_dev_gui.bat.
 exit /b 1
:launch
start "" "%APP%" --manual
exit /b 0
