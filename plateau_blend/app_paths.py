"""アプリが自分で書く物（ログ・Edge のプロファイル）の置き場。

ポータブルアプリなので、展開したフォルダの中の ``data`` に置き、
%LOCALAPPDATA% などフォルダの外へは書かない。消す時はフォルダごと消せば済む。
展開先が書き込めない時（Program Files の中など）だけ、一時フォルダを使う。
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
    return data_dir(root) / 'edge-profile'
