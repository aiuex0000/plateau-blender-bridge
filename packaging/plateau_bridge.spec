# -*- mode: python ; coding: utf-8 -*-
"""Windows onedir bundle: user runs the exe, not a Python virtual environment."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH).resolve().parent
app_dir = root / 'plateau_blend'
datas = [(str(app_dir / f), 'plateau_blend')
         for f in ('blender_scene.py', 'blender_verify.py')]
datas += collect_data_files('pyproj')
binaries = collect_dynamic_libs('pyproj')

a = Analysis(
    [str(root / 'plateau_launcher.py')],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=['plateau_blend.gui','plateau_blend.auto_gui',
                   'plateau_blend.auto_cli','pyproj.datadir'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='PLATEAU-Blender-Bridge', console=False,
          debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas,
               strip=False, upx=False, name='PLATEAU-Blender-Bridge')

# nusamai CLI (MIT) has no official Windows build; build_windows.bat compiles it into
# tools/nusamai. It sits beside the exe (not in _internal) so users can see/replace it.
import shutil
tools = root / 'tools' / 'nusamai'
if (tools / 'nusamai.exe').is_file():
    dest = Path(DISTPATH) / 'PLATEAU-Blender-Bridge' / 'tools' / 'nusamai'
    dest.mkdir(parents=True, exist_ok=True)
    for f in tools.iterdir():
        if f.is_file(): shutil.copy2(f, dest / f.name)
