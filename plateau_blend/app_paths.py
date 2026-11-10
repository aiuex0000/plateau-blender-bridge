"""アプリが自分で書く物（ログ・Edge のプロファイル）の置き場。

ログや作業データは、展開したフォルダの中の ``data`` に置く。消す時はフォルダごと
消せば済む。展開先が書き込めない時（Program Files の中など）だけ、一時フォルダを使う。

Edge のプロファイルだけは例外で、``%LOCALAPPDATA%`` の短い場所に置く。
インストール先が深いと、プロファイルのパス長で Edge の描画プロセスが起動できず、
灰色の窓になるため（2026-11-09、実測）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

DATA_FOLDER_NAME = 'data'
_TEMP_FOLDER_NAME = 'PLATEAU-Blender-Bridge'


def app_root() -> Path:
    """凍結EXEでは実行ファイルのフォルダ、開発時は repo のフォルダ。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(folder: Path) -> bool:
    """一意の名前で排他的に作って確かめ、自分が作った物だけを消す（既存のファイルには触れない）。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix='.write-test-', dir=folder)
    except OSError:
        return False
    os.close(handle)
    try:
        os.unlink(name)
    except OSError:
        pass  # 書けることは分かった。空の確認用ファイルが1つ残るだけ
    return True


def data_dir(root: Path | None = None) -> Path:
    """``<アプリのフォルダ>\\data``。書き込めなければ一時フォルダの下。"""
    preferred = (app_root() if root is None else Path(root)) / DATA_FOLDER_NAME
    if _writable(preferred):
        return preferred
    return Path(tempfile.gettempdir()) / _TEMP_FOLDER_NAME


def logs_dir(root: Path | None = None) -> Path:
    return data_dir(root) / 'logs'


def edge_profile_dir(root: Path | None = None) -> Path:
    """Edge のプロファイル。``root`` を渡した時だけ、その下の ``data`` を使う。

    凍結アプリの既定は ``%LOCALAPPDATA%\\PLATEAU-Blender-Bridge\\edge-profile``。
    インストール先が深いと Edge の描画プロセスが起動できないため、アプリのフォルダ
    ではなく短い場所に置く。LOCALAPPDATA が使えない時は一時フォルダへ逃がす。
    """
    if root is not None:
        return data_dir(root) / 'edge-profile'
    local = os.environ.get('LOCALAPPDATA')
    if local:
        candidate = Path(local) / _TEMP_FOLDER_NAME / 'edge-profile'
        if len(str(candidate)) < 100:
            return candidate
    return Path(tempfile.gettempdir()) / _TEMP_FOLDER_NAME / 'edge-profile'


def is_slow_path(path: Path) -> bool:
    """OneDrive・UNC・ネットワークドライブは、存在確認が長くかかることがある。"""
    text = str(path)
    lower = text.lower()
    if text.startswith('\\\\') or text.startswith('//'):
        return True
    if '\\onedrive' in lower or '/onedrive' in lower:
        return True
    drive = os.path.splitdrive(text)[0]
    if drive and os.name == 'nt':
        try:
            import ctypes
            return ctypes.windll.kernel32.GetDriveTypeW(drive + '\\') == 4  # DRIVE_REMOTE
        except Exception:
            return False
    return False


def exists_quick(path: Path, timeout: float = 2.0) -> bool:
    """存在確認を短く打ち切る。遅い場所でHTTPの応答を止めないため。"""
    result = {'value': False}

    def worker():
        try:
            result['value'] = path.exists()
        except OSError:
            pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    return bool(result['value'])


def writable_quick(folder: Path, timeout: float = 3.0) -> bool | None:
    """作れるか・書けるかを小さなファイルで確かめる。``timeout`` 秒で打ち切り、
    分からなければ None。OneDrive やネットワークで HTTP の応答を止めないため。"""
    result: dict = {'value': None}

    def worker():
        probe = None
        try:
            target = Path(folder).expanduser()
            target.mkdir(parents=True, exist_ok=True)
            probe = target / f'.plateau_write_{os.getpid()}_{threading.get_ident()}'
            probe.write_bytes(b'')
            result['value'] = True
        except OSError:
            result['value'] = False
        finally:
            if probe is not None:
                try:
                    probe.unlink()
                except OSError:
                    pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    return None if thread.is_alive() else result['value']


def documents_dir() -> Path:
    """本当の「ドキュメント」。OneDrive へリダイレクトされていても、その先を返す。
    取れない時（Windows 以外・API の失敗）は ホーム/Documents。"""
    fallback = Path.home() / 'Documents'
    if os.name != 'nt':
        return fallback
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                        ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = _GUID(0xFDD39AD0, 0x238F, 0x46AF,
                          (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD,
                                                 wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) != 0:
            return fallback
        try:
            text = out.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
        return Path(text) if text else fallback
    except Exception:
        return fallback


def free_output_path_quick(path: Path, timeout: float = 3.0) -> Path:
    """既存名を避ける。確認全体を``timeout``秒で打ち切る。"""
    deadline = time.monotonic() + timeout
    if not exists_quick(path, timeout=min(2.0, timeout)):
        return path
    for number in range(2, 50):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return path
        candidate = path.with_name(f'{path.stem}_{number}{path.suffix}')
        if not exists_quick(candidate, timeout=min(2.0, remaining)):
            return candidate
    return path
