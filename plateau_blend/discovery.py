"""Bounded, read-only discovery of Windows Blender and nusamai executables."""
from __future__ import annotations
from pathlib import Path
import os
import re
import shutil
import sys


def _program_roots(environ: dict | None = None) -> list[Path]:
    e = environ if environ is not None else os.environ
    names = ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)', 'LOCALAPPDATA', 'USERPROFILE')
    return [Path(e[n]) for n in names if e.get(n)]


def _version_key(path: Path) -> tuple[int, ...]:
    matches = re.findall(r'\d+', str(path))
    # Use Blender directory name rather than entire path (avoids year in username).
    for part in reversed(path.parts):
        if 'blender' in part.lower() and re.search(r'\d',part):
            matches = re.findall(r'\d+', part)
            break
    return tuple(map(int, matches[:3])) if matches else (0,)


def blender_candidates(environ: dict | None = None, platform: str | None = None) -> list[Path]:
    e = environ if environ is not None else os.environ
    platform = sys.platform if platform is None else platform
    paths: list[Path] = []
    for cmd in ('blender', 'blender.exe'):
        found = shutil.which(cmd)
        if found: paths.append(Path(found))
    for root in _program_roots(e):
        if not root.is_dir(): continue
        direct = [root / 'Blender Foundation']
        # Portable Blender is often unpacked under Downloads or Applications.
        if root == Path(e.get('USERPROFILE', '\0')):
            direct += [root / 'Downloads', root / 'Applications', root / 'Desktop']
        for parent in direct:
            if not parent.is_dir(): continue
            # Bounded: only direct and one child level; no slow full disk scan.
            paths += [parent / 'blender.exe', parent / 'Blender' / 'blender.exe']
            try:
                for child in parent.iterdir():
                    if not child.is_dir() or 'blender' not in child.name.lower(): continue
                    paths.append(child / 'blender.exe')
                    paths.append(child / 'Blender' / 'blender.exe')
            except (OSError, PermissionError): pass
    if platform == 'win32':
        try:
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for sub in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe',
                            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\blender.exe'):
                    try:
                        with winreg.OpenKey(hive, sub) as key:
                            paths.append(Path(winreg.QueryValueEx(key, None)[0]))
                    except OSError: pass
                for sub in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                            r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                    try:
                        with winreg.OpenKey(hive, sub) as base:
                            n,_ ,_=winreg.QueryInfoKey(base)
                            for i in range(n):
                                try:
                                    with winreg.OpenKey(base, winreg.EnumKey(base,i)) as key:
                                        name = winreg.QueryValueEx(key,'DisplayName')[0]
                                        if not str(name).lower().startswith('blender'): continue
                                        location = winreg.QueryValueEx(key,'InstallLocation')[0]
                                        paths.append(Path(location)/'blender.exe')
                                except OSError: continue
                    except OSError: pass
        except ImportError: pass
    return sorted({p.resolve() for p in paths if p.is_file()}, key=_version_key, reverse=True)


def find_blender() -> Path | None:
    candidates = blender_candidates()
    return candidates[0] if candidates else None


def bundled_nusamai() -> Path | None:
    """The CLI shipped next to the frozen exe (tools/nusamai), or in a source checkout."""
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
    candidate = base / 'tools' / 'nusamai' / 'nusamai.exe'
    return candidate if candidate.is_file() else None


def find_nusamai() -> Path | None:
    for value in (os.environ.get('NUSAMAI_EXE'), bundled_nusamai(), shutil.which('nusamai.exe'), shutil.which('nusamai')):
        if value and Path(value).is_file(): return Path(value).resolve()
    for root in _program_roots():
        for candidate in (root/'nusamai.exe',root/'Downloads'/'nusamai.exe',root/'Applications'/'nusamai.exe'):
            if candidate.is_file(): return candidate.resolve()
    return None
