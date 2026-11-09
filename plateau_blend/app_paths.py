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
        if len(str(candidate)) < 100 and _writable(candidate.parent):
            return candidate
    return Path(tempfile.gettempdir()) / _TEMP_FOLDER_NAME / 'edge-profile'
