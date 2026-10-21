@echo off
setlocal
cd /d "%~dp0"
rem [DEV ONLY] nusamai CLI has no official Windows binary; build the pinned tag with cargo.
set NUSAMAI_TAG=v0.1.19
set SRC=.build-nusamai\src
if exist "tools\nusamai\nusamai.exe" (echo tools\nusamai\nusamai.exe already exists& exit /b 0)
where cargo >nul 2>&1
if errorlevel 1 (echo Rust toolchain ^(cargo^) needed only for developers building nusamai.& exit /b 1)
if not exist "%SRC%\Cargo.toml" (
  git clone --depth 1 --branch %NUSAMAI_TAG% https://github.com/MIERUNE/plateau-gis-converter.git "%SRC%"
  if errorlevel 1 exit /b 1
  rem v0.1.19 textured_max_lod empties untextured buildings; see patches\README.md
  git -C "%SRC%" apply --ignore-whitespace "%~dp0patches\nusamai-v0.1.19-textured-lod-fallback.patch"
  if errorlevel 1 exit /b 1
)
cargo build --release -p nusamai --manifest-path "%SRC%\Cargo.toml"
if errorlevel 1 exit /b 1
if not exist "tools\nusamai" mkdir "tools\nusamai"
copy /y "%SRC%\target\release\nusamai.exe" "tools\nusamai\nusamai.exe" >nul
copy /y "%SRC%\LICENSE" "tools\nusamai\LICENSE-nusamai.txt" >nul
echo nusamai %NUSAMAI_TAG% built into tools\nusamai
