@echo off
setlocal
cd /d "%~dp0"
rem [DEV ONLY] nusamai CLI has no official Windows binary; build the pinned commit with cargo.
set NUSAMAI_TAG=v0.1.19
set NUSAMAI_COMMIT=a038cff3b1a98c38ef4753f5f7ca3272a3eb1e37
set SRC=.build-nusamai\src
where cargo >nul 2>&1
if errorlevel 1 (echo Rust toolchain ^(cargo^) needed only for developers building nusamai.& exit /b 1)
if not exist "%SRC%\Cargo.toml" (
  git clone --depth 1 --branch %NUSAMAI_TAG% https://github.com/MIERUNE/plateau-gis-converter.git "%SRC%"
  if errorlevel 1 exit /b 1
  rem See patches\README.md. The second patch removes the sinks Bridge does not use,
  rem together with kv-extsort, whose redistribution terms could not be confirmed.
  git -C "%SRC%" apply --ignore-whitespace "%~dp0patches\nusamai-v0.1.19-textured-lod-fallback.patch"
  if errorlevel 1 exit /b 1
  git -C "%SRC%" apply --ignore-whitespace "%~dp0patches\nusamai-v0.1.19-drop-unused-sinks.patch"
  if errorlevel 1 exit /b 1
)
for /f %%c in ('git -C "%SRC%" rev-parse HEAD') do set HEAD_COMMIT=%%c
if /i not "%HEAD_COMMIT%"=="%NUSAMAI_COMMIT%" (echo %SRC% is at %HEAD_COMMIT%, expected %NUSAMAI_COMMIT%& exit /b 1)
findstr /c:"kv-extsort" "%SRC%\Cargo.lock" >nul
if not errorlevel 1 (echo %SRC% is an older unpatched checkout; delete %SRC% and rerun.& exit /b 1)
rem Always build; an existing tools\nusamai\nusamai.exe is replaced only after a successful build.
cargo build --release --locked -p nusamai --manifest-path "%SRC%\Cargo.toml"
if errorlevel 1 exit /b 1
if not exist "tools\nusamai" mkdir "tools\nusamai"
copy /y "%SRC%\target\release\nusamai.exe" "tools\nusamai\nusamai.exe" >nul
copy /y "%SRC%\LICENSE" "tools\nusamai\LICENSE-nusamai.txt" >nul
echo nusamai %NUSAMAI_TAG% (%NUSAMAI_COMMIT%, patched) built into tools\nusamai
