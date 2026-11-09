"""B案（一問ずつ）の画面。ローカルHTTP＋Edgeアプリ窓と、手書き HTML/JS から呼ばれる Python 側。

モジュール読込時には tkinter も webview も import しない。Tk の文言・純粋な補助関数は
``auto_gui`` が正本なので初回利用時に読み込み、ファイル選択の Tk は実際に開く時だけ読む。
画面は ``web_server.BridgeServer`` が 127.0.0.1 で配り、HTTP ハンドラーから ``BridgeApi`` を呼ぶ。

イベントは ``{"kind": ..., "data": ...}``。作業に紐づく ``stage`` / ``result`` / ``done`` /
``error`` / ``cancelled`` には ``job_id`` を添える。JS は自分の ``job_id`` 以外のイベントで
画面の状態を変えない（ログには出す）。
"""
from __future__ import annotations

import dataclasses
import errno
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from .auto import (AutoRequest, _is_lat, _is_lon, center_condition, generate, preview,
                   projected_selection)
from .catalog import NoPlateauData
from .diagnostics import inspect_environment
from .discovery import blender_candidates, find_nusamai
from .gsi import fractional_tile
from .resources import enforce_budget, forecast
from .runtime import (
    CancelToken, CancelledError, hidden_process_kwargs, open_saved_blend, open_with_default_app,
)
from .smoke import run_smoke
from .web_server import BridgeServer
from . import __version__, app_paths

RANGE_VIEW_WIDTH = 512
RANGE_VIEW_HEIGHT = 320
RANGE_TILE_SIZE = 256
RANGE_MARGIN = 24

MAPS_URL = 'https://www.google.com/maps'
MAPS_POINT_URL = 'https://www.google.com/maps/@{lat},{lon},16z'
JOB_EVENTS = ('stage', 'result', 'done', 'error', 'cancelled')
_APP_FOLDER_NAME = '作った街'
_NUM = re.compile(r'-?\d+(?:\.\d+)?')
_MAP_POINT = re.compile(r'@-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?')
_DMS = re.compile(r'(\d+)\s*°\s*(\d+)\s*[\'′]\s*(\d+(?:\.\d+)?)\s*(?:"|″|\'\')?\s*([NSEW])')
_UNSAFE_PLACE_NAME_CHARACTERS = re.compile(r'[.<>:"/\\|?*\x00-\x1f]')
_GUI = None

FIRST_CONTACT_TIMEOUT = 60.0
IDLE_TIMEOUT = 180.0  # 最小化中の Edge はタイマーを1分1回まで間引くので短くしない
BYE_GRACE = 3.0
_EDGE_RELATIVE = Path('Microsoft') / 'Edge' / 'Application' / 'msedge.exe'


def _fallback_dir() -> Path:
    return app_paths.logs_dir()


def write_fallback_log(reason, trace=None) -> Path | None:
    """Tk へ落ちた理由を残す。書けなくても例外を出さない。"""
    try:
        folder = _fallback_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'gui-fallback-{datetime.now():%Y%m%d-%H%M%S}.log'
        text = str(reason)
        if trace:
            text += '\n\n' + str(trace)
        target.write_text(text + '\n', encoding='utf-8')
        return target
    except Exception:
        return None


def _gui():
    """auto_gui は Tk を import するので、必要になった時だけ読む（この module の先頭では読まない）。"""
    global _GUI
    if _GUI is None:
        from . import auto_gui
        _GUI = auto_gui
    return _GUI


def _documents_output() -> Path:
    return Path.home() / 'Documents' / 'PLATEAU' / 'scene.blend'


def _default_kind() -> str:
    """ポータブルアプリなので、既定はアプリのフォルダ。書けない時（Program Files など）だけドキュメント。
    ``作った街`` がまだ無ければ作らず、親（exe のフォルダ）に書けるかだけを見る。
    もうあれば、それ自体がフォルダで書けるかを見る（同名のファイルや読み取り専用なら使わない）。"""
    folder = _app_folder()
    if folder.exists():
        return 'app' if folder.is_dir() and _folder_writable(folder) else 'documents'
    return 'app' if _folder_writable(folder.parent) else 'documents'


def _default_output() -> Path:
    if _default_kind() == 'app':
        return _app_folder() / _documents_output().name
    return _documents_output()


def _app_folder() -> Path:
    """凍結EXEでは実行ファイルの隣、開発時は repo 側の ``作った街``。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / _APP_FOLDER_NAME
    return Path(__file__).resolve().parent.parent / _APP_FOLDER_NAME


def _path_or_none(value) -> Path | None:
    text = str(value or '').strip()
    return Path(text) if text else None


def _first_path(result) -> str | None:
    """ファイル選択の戻り値（文字列・tuple・None）を1本のパスへ揃える。"""
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return str(result[0]) if result else None
    return str(result)


def _existing_dir(folder: Path) -> str:
    """保存先の隣を開くため、存在する一番近いフォルダまで遡る（auto_gui.existing_dir と同じ）。"""
    folder = folder.expanduser()
    while not folder.is_dir() and folder.parent != folder:
        folder = folder.parent
    return str(folder)


def _folder_writable(folder: Path) -> bool:
    """作れるか・書けるかを、実際に小さなファイルを置いて確かめる。"""
    probe = None
    try:
        folder = folder.expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f'.plateau_write_{os.getpid()}_{threading.get_ident()}'
        probe.write_bytes(b'')
        return True
    except OSError:
        return False
    finally:
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                pass


def _as_int(value, default: int, label: str) -> int:
    text = str(value if value is not None else '').strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        raise ValueError(f'{label}は数字で指定してください') from None


def _size_m(value) -> float:
    text = str(value if value is not None else '').strip() or '400'
    if text == 'input':
        return 400.0
    try:
        size = float(text)
    except ValueError:
        raise ValueError('広さは200・400・800・inputのいずれかで指定してください') from None
    if size <= 0:
        raise ValueError('広さは正の数で指定してください')
    return size


def explicit_size_m(area) -> float | None:
    """中心点の入力に3つ目の数字として一辺が書かれている時だけ、その数値を返す。

    ``center_condition`` と同じ読み方にする（NFKC、先頭 ``r:`` の除去、Googleマップの URL と
    度分秒は広さなし）。``@35.68,139.76,17z`` の ``17`` を広さと読まない。
    """
    text = unicodedata.normalize('NFKC', str(area if area is not None else '')).strip()
    if text.startswith('r:'):
        text = text[2:].strip()
    if _MAP_POINT.search(text) or len(_DMS.findall(text)) == 2:
        return None
    numbers = _NUM.findall(text)
    if len(numbers) != 3:
        return None
    try:
        return float(numbers[2])
    except ValueError:
        return None


def _first_two_numbers(area) -> str:
    """入力の最初の2つの数字だけを取り出して返す（カードで広さを上書きした時用）。"""
    text = unicodedata.normalize('NFKC', str(area if area is not None else '')).strip()
    if text.startswith('r:'):
        text = text[2:].strip()
    numbers = _NUM.findall(text)
    return ', '.join(numbers[:2])


def _condition_from_form(form: dict, notes: list[str] | None = None) -> str:
    raw = str(form.get('area') or '').strip()
    if not raw:
        raise ValueError('地域または範囲を指定してください')
    mode = str(form.get('mode') or 'center')
    if mode == 'bbox':
        condition = 'r:' + raw.replace(' ', '')
    elif mode == 'mesh':
        condition = 'm:' + raw.replace(' ', '')
    elif mode == 'center':
        size = str(form.get('size') or '400').strip() or '400'
        if size == 'input':
            condition = center_condition(raw, default_size_m=400.0, notes=notes)
        else:
            # カードで広さを選んだら、入力の3つ目の数字よりカードを優先する。
            text = _first_two_numbers(raw) if explicit_size_m(raw) is not None else raw
            condition = center_condition(text, default_size_m=_size_m(size), notes=notes)
    else:
        raise ValueError('指定のしかたが不正です')
    return condition


def request_from_form(form: dict, notes: list[str] | None = None) -> AutoRequest:
    """画面の辞書から ``AutoRequest`` を作る（auto_gui.build_request と同じ規則）。"""
    form = form or {}
    condition = _condition_from_form(form, notes)
    output = str(form.get('output') or '').strip()
    if not output:
        raise ValueError('保存先を指定してください')
    output_path = Path(output).expanduser()
    if form.get('auto_name') is True:
        output_path = _auto_output_path(output_path, form)
    else:
        output_path = output_path.resolve()
    clip = form.get('clip', True)
    if isinstance(clip, str):
        clip = clip.strip().lower() not in ('', '0', 'false', 'off', 'no')
    return AutoRequest(
        condition=condition,
        epsg=str(form.get('epsg') or 'AUTO').strip() or 'AUTO',
        output_blend=output_path,
        blender_executable=_path_or_none(form.get('blender')),
        nusamai_executable=_path_or_none(form.get('nusamai')),
        lod=str(form.get('lod') or 'textured_max_lod'),
        photo_zoom=_as_int(form.get('zoom'), 18, '航空写真ズーム'),
        terrain_resolution=_as_int(form.get('dem_res'), 100, '地形のサンプル密度'),
        axis_override=str(form.get('axis') or 'auto'),
        clip_requested_area=bool(clip),
    )


def inspect_area(form: dict | None = None) -> dict:
    """形式だけを通信なしで調べる。1問目の「次へ」はこれが ok の時だけ進む。"""
    form = form or {}
    mode = str(form.get('mode') or 'center')
    raw = str(form.get('area') or '').strip()
    empty = {'ok': False, 'mode': mode, 'explicit_size_m': None, 'error': '', 'note': ''}
    if mode not in ('center', 'bbox', 'mesh'):
        return {**empty, 'error': '指定のしかたが不正です'}
    if mode == 'mesh':
        compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', raw))
        if not re.fullmatch(r'[0-9]{6,12}', compact):
            return {**empty, 'error': 'メッシュコードは6〜12桁の数字で指定してください'}
        return {**empty, 'ok': True}
    if not raw or not re.search(r'\d', unicodedata.normalize('NFKC', raw)):
        return {**empty, 'error': '場所を選ぶか、座標を入れてください'}
    notes: list[str] = []
    try:
        if mode == 'bbox':
            center_condition('r:' + raw.replace(' ', ''), notes=notes)
            return {**empty, 'ok': True, 'note': notes[0] if notes else ''}
        explicit = explicit_size_m(raw)
        center_condition(raw, default_size_m=400.0 if explicit is None else explicit, notes=notes)
    except ValueError as exc:
        return {**empty, 'error': str(exc)}
    return {**empty, 'ok': True, 'explicit_size_m': explicit,
            'note': notes[0] if notes else ''}


def _area_size_m(form: dict) -> float | None:
    """3問目のまとめ用。中心点の時だけ、実際に使う一辺の長さ（m）を返す。"""
    if str(form.get('mode') or 'center') != 'center':
        return None
    size = str(form.get('size') or '400').strip() or '400'
    if size == 'input':
        return explicit_size_m(form.get('area'))
    try:
        return _size_m(size)
    except ValueError:
        return None


def _auto_output_path(requested: Path, form: dict) -> Path:
    size = _area_size_m(form)
    place = unicodedata.normalize('NFC', str(form.get('place_name') or ''))
    # Replace dots too so a name like CON.example cannot start with a device name.
    name = _UNSAFE_PLACE_NAME_CHARACTERS.sub('_', place)[:100].strip(' _')
    if size is None or not name:
        return requested.resolve()
    return requested.parent.resolve() / f'{name}_{size:g}m.blend'


def _area_label(form: dict) -> str:
    if str(form.get('mode') or 'center') != 'center':
        return '入力した範囲'
    size = str(form.get('size') or '400').strip() or '400'
    value = _area_size_m(form)
    if value is not None:
        return f'{value:g}m'
    if size == 'input':
        return '入力した広さ'
    return f'{size}m'


def _japan_point(lat, lon) -> tuple[float, float] | None:
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if _is_lat(lat_f) and _is_lon(lon_f):
        return lat_f, lon_f
    return None


def _generation_recovery(exc: Exception, output: Path) -> str:
    if isinstance(exc, NoPlateauData):
        return 'place'
    if not isinstance(exc, OSError) or not exc.filename:
        return 'unknown'
    if not (isinstance(exc, PermissionError) or exc.errno == errno.ENOSPC
            or getattr(exc, 'winerror', None) == 112):
        return 'unknown'
    target = Path(os.path.abspath(os.fsdecode(exc.filename)))
    work = output.with_suffix('.work')
    if (target in (output, output.parent, output.with_suffix('.provenance.json'))
            or target.is_relative_to(work)):
        return 'output'
    return 'unknown'


class BridgeApi:
    """JS から ``POST /api/<名前>`` で呼ばれる面。

    HTTP 側は ``_`` で始まらない、クラスに定義された呼び出し可能メソッドだけを公開する。
    キュー・スレッド・CancelToken・ダイアログはすべて ``_`` 付きで持つ。
    """

    def __init__(self):
        self._dialogs: Any = None  # save()/open() を持つ物（実体は _TkDialogs、テストでは偽物）
        self._events: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._log_lines: list[str] = []
        self._running = False
        self._scanning = False
        self._stage = 0
        self._announced = 0
        self._token: CancelToken | None = None
        self._result: Path | None = None
        self._form: dict = {}
        self._job: str | None = None
        self._job_seq = 0
        self._scanned_blender: Path | None = None
        self._scanned_nusamai: Path | None = None

    @property
    def running(self) -> bool:
        return self._running

    # ── 画面の初期値 ────────────────────────────────────────────────

    def initial_state(self) -> dict:
        gui = _gui()
        return {
            'version': __version__,
            'title': f'PLATEAU → Blender | 自動生成 v{__version__.rsplit(".", 1)[0]}',
            'places': [{'name': name, 'coordinates': coordinates, 'size': size}
                       for name, coordinates, size in gui.PLACE_PRESETS],
            'sizes': [
                {'value': '200', 'title': '小さめ', 'detail': '200m四方・すぐできる'},
                {'value': '400', 'title': '標準', 'detail': '400m四方・おすすめ'},
                {'value': '800', 'title': '広め', 'detail': '800m四方・時間がかかる'},
            ],
            'default_size': '400',
            'stages': [{'stage': index, 'text': text}
                       for index, (_, text) in enumerate(gui.STAGES, 1)],
            'modes': [
                {'value': 'center', 'label': gui.CENTER_LABEL, 'hint': gui.CENTER_HINT},
                {'value': 'bbox', 'label': '範囲 west,south,east,north',
                 'hint': '例: 135.42,34.81,135.43,34.82（経度・緯度。小範囲から試す）'},
                {'value': 'mesh', 'label': 'メッシュコード',
                 'hint': '例: 5235xxxx（6/8/9/10桁のメッシュ番号）'},
            ],
            'default_output': str(_default_output()),
            'default_kind': _default_kind(),
            'outputs': self.output_choices(),
            'min_size': [730, 580],
            'cancelled_message': gui.CANCELLED_MESSAGE,
        }

    # ── 環境スキャン・場所の確認 ─────────────────────────────────────

    def scan(self) -> dict:
        if self._scanning:
            return {'started': False}
        self._scanning = True
        self._log('Blender / nusamai 実行ファイルを検索中…')

        def task():
            try:
                blender = blender_candidates()
                nusamai = find_nusamai()
            except Exception as exc:
                self._log(f'検索エラー: {exc}')
                return
            finally:
                self._scanning = False
            # 一度見つけた値に固定せず、毎回のスキャン結果へ更新する。
            self._scanned_blender = blender[0] if blender else None
            self._scanned_nusamai = nusamai
            self._emit('scan', {
                'blender': [str(path) for path in blender],
                'nusamai': str(nusamai) if nusamai else None,
            })

        self._spawn(task)
        return {'started': True}

    def blender_status(self, path: str | None = None) -> dict:
        """使う Blender のパスが実在するファイルか。文字が入っているだけでは ok にしない。"""
        text = str(path or '').strip()
        if not text:
            return {'ok': False, 'path': ''}
        target = Path(text).expanduser()
        try:
            ok = target.is_file()
        except OSError:
            ok = False
        return {'ok': bool(ok), 'path': str(target)}

    def inspect_area(self, form: dict | None = None) -> dict:
        """1問目の形式検査（通信なし）。窓の JS からも同じ関数を呼ぶ。"""
        return inspect_area(form)

    def range_preview(self, form: dict | None = None) -> dict:
        """広さの指定から、地理院タイルの固定プレビュー用データを作る。

        生成と同じ範囲指定を使い、ブラウザの 512x320 の表示枠に必要な
        タイル座標を返す。
        """
        source = form if isinstance(form, dict) else self._form
        mode = str(source.get('mode') or 'center')
        if mode == 'mesh':
            return {'ok': False, 'supported': False, 'mode': mode,
                    'error': 'メッシュコードの指定では地図プレビューを使えません'}
        failure = {'ok': False, 'supported': True, 'mode': mode}
        try:
            condition = _condition_from_form(source)
        except ValueError as exc:
            return {**failure, 'error': str(exc)}
        try:
            west, south, east, north = map(float, condition[2:].split(','))
        except ValueError:
            return {**failure, 'error': '範囲は西端、南端、東端、北端の順に数値で指定してください'}
        if not all(math.isfinite(value) for value in (west, south, east, north)):
            return {**failure, 'error': '範囲の座標は有限の数値で指定してください'}
        if not (west < east and south < north):
            return {**failure, 'error': '範囲は西端、南端、東端、北端の順に指定してください'}

        try:
            west_tile, north_tile = fractional_tile(west, north, 0)
            east_tile, south_tile = fractional_tile(east, south, 0)
        except ValueError:
            return {**failure, 'error': '指定した座標は地図の表示範囲外です'}

        limit_width = RANGE_VIEW_WIDTH - RANGE_MARGIN * 2
        limit_height = RANGE_VIEW_HEIGHT - RANGE_MARGIN * 2

        for zoom in range(18, 1, -1):
            limit = 1 << zoom
            scale = limit * RANGE_TILE_SIZE
            width = (east_tile - west_tile) * scale
            height = (south_tile - north_tile) * scale
            if 0 < width <= limit_width and 0 < height <= limit_height:
                break
        else:
            return {**failure, 'error': '指定した範囲を地図の表示枠に収められません'}

        left = (west_tile + east_tile) / 2 * scale - RANGE_VIEW_WIDTH / 2
        top = (north_tile + south_tile) / 2 * scale - RANGE_VIEW_HEIGHT / 2
        x0 = math.floor(left / RANGE_TILE_SIZE)
        x1 = math.ceil((left + RANGE_VIEW_WIDTH) / RANGE_TILE_SIZE) - 1
        y0 = math.floor(top / RANGE_TILE_SIZE)
        y1 = math.ceil((top + RANGE_VIEW_HEIGHT) / RANGE_TILE_SIZE) - 1
        tiles = []
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if not (0 <= x < limit and 0 <= y < limit):
                    continue
                tiles.append({
                    'z': zoom, 'x': x, 'y': y,
                    'left': x * RANGE_TILE_SIZE - left,
                    'top': y * RANGE_TILE_SIZE - top,
                    'width': RANGE_TILE_SIZE, 'height': RANGE_TILE_SIZE,
                })

        return {
            'ok': True,
            'supported': True,
            'mode': mode,
            'zoom': zoom,
            'viewport': {'width': RANGE_VIEW_WIDTH, 'height': RANGE_VIEW_HEIGHT},
            'bounds': {'west': west, 'south': south, 'east': east, 'north': north},
            'rect': {'left': (RANGE_VIEW_WIDTH - width) / 2,
                     'top': (RANGE_VIEW_HEIGHT - height) / 2,
                     'width': width, 'height': height},
            'center': {'x': RANGE_VIEW_WIDTH / 2, 'y': RANGE_VIEW_HEIGHT / 2},
            'tiles': tiles,
        }

    def check_place(self, form: dict | None = None, request_id=None) -> dict:
        """カタログだけを見る（GMLは落とさない）。結果は ``place`` イベント。

        結果は3種類に分ける。``ok``（データあり）・``nodata``（``NoPlateauData``）・
        ``unknown``（通信などそれ以外）。通信の失敗を「データが無い」と言わない。
        """
        self._remember(form)
        if self._running:
            return {'started': False, 'text': '作業中です', 'request_id': request_id}
        notes: list[str] = []
        try:
            request = request_from_form(self._form, notes=notes)
        except Exception as exc:
            text = str(exc)
            self._emit('place', {'ok': False, 'status': 'invalid', 'text': text,
                                 'request_id': request_id})
            return {'started': False, 'text': text, 'request_id': request_id}
        prefix = f'↔ {notes[0]} ' if notes else ''

        def worker():
            try:
                sources = preview(request)
            except NoPlateauData:
                self._emit('place', {
                    'ok': False,
                    'status': 'nodata',
                    'text': 'この場所にはデータがありません。有名な場所から選ぶか、別の場所を試してください',
                    'request_id': request_id,
                })
                return
            except Exception as exc:
                self._emit('log', f'[PLACE] {type(exc).__name__}: {exc}')
                self._emit('place', {
                    'ok': False,
                    'status': 'unknown',
                    'text': '確かめられませんでした。通信を確かめて、もう一度押してください',
                    'request_id': request_id,
                })
                return
            cities = '、'.join(dict.fromkeys(f'{s.city}（{s.year}年度）' for s in sources))
            size = sum(s.size or 0 for s in sources) / 1048576
            text = f'{prefix}✓ {cities}のデータがあります・GML {len(sources)}件・約{size:.0f}MB'
            self._emit('place', {'ok': True, 'status': 'ok', 'text': text,
                                 'request_id': request_id,
                                 'files': len(sources), 'megabytes': round(size, 1)})

        self._spawn(worker)
        return {'started': True, 'request_id': request_id}

    # ── 3問目のまとめ・作成 ─────────────────────────────────────────

    def plan(self, form: dict | None = None) -> dict:
        """保存先が既にあれば別名を返す。入力の誤りは ``{"error": 文言}``。"""
        self._remember(form)
        notes: list[str] = []
        try:
            request = request_from_form(self._form, notes=notes)
            request.check()
        except Exception as exc:
            return {'error': str(exc)}
        requested = request.output_blend
        target = requested
        renamed = target.exists()
        if renamed:
            target = _gui().free_output_path(target)
        return {
            'output': str(target),
            'requested': str(requested),
            'output_name': target.name,
            'output_folder': str(target.parent),
            'renamed': renamed,
            'rename_note': (f'同じ名前のファイルがあるので、{target.name} で保存します'
                            if renamed else ''),
            'place': str(self._form.get('place_name') or self._form.get('area') or '').strip(),
            'area': _area_label(self._form),
            'area_m': _area_size_m(self._form),
            'mode': str(self._form.get('mode') or 'center'),
            'note': notes[0] if notes else '',
        }

    def output_choices(self) -> dict:
        """保存場所の切替に出す2つの候補（フォルダ）。"""
        return {'documents': str(_documents_output().parent), 'app': str(_app_folder())}

    def use_output_folder(self, kind: str | None = None, current: str | None = None) -> dict | None:
        """``documents`` / ``app`` へ保存場所を切り替える。書けなければドキュメントへ戻す。"""
        kind = str(kind or '')
        if kind not in ('documents', 'app'):
            return None
        raw = str(current or '').strip()
        name = Path(raw).name if raw else _documents_output().name
        if not name:
            name = _documents_output().name
        if not name.lower().endswith('.blend'):
            name += '.blend'
        folder = _documents_output().parent if kind == 'documents' else _app_folder()
        if _folder_writable(folder):
            return {'output': str(folder / name), 'folder': str(folder), 'kind': kind}
        fallback = _documents_output().parent
        self._log(f'[OUTPUT] {folder} には保存できません。ドキュメントを使ってください')
        result = {'error': 'このフォルダには保存できません。ドキュメントを使ってください',
                  'kind': 'documents'}
        if kind != 'documents' and _folder_writable(fallback):
            result.update({'output': str(fallback / name), 'folder': str(fallback)})
        return result

    def start_generate(self, form: dict | None = None) -> dict:
        """確認ダイアログなしで作成を始める。走っている間の呼び出しは何もしない。"""
        self._remember(form)
        notes: list[str] = []
        try:
            request = request_from_form(self._form, notes=notes)
            request.check()
        except Exception as exc:
            return {'started': False, 'error': str(exc)}
        if request.output_blend.exists():
            fresh = _gui().free_output_path(request.output_blend)
            self._log(f'[RENAME] 同じ名前のファイルがあるので、上書きせず {fresh.name} で保存します')
            request = dataclasses.replace(request, output_blend=fresh)
        self._form['output'] = str(request.output_blend)
        if notes:
            self._log('↔ ' + notes[0])
        gui = _gui()
        token = CancelToken()
        # 「動いているか」の判定と _running=True を同じ鍵の中で行う（二重起動を防ぐ）。
        with self._lock:
            if self._running:
                return {'started': False, 'reason': 'running'}
            self._running = True
            self._stage = 1
            self._announced = 1
            self._token = token
            self._result = None
            job = self._next_job()
        self._emit('stage', {'stage': 1, 'text': gui.STAGES[0][1]}, job=job)
        self._emit('log', f'(1/4) {gui.STAGES[0][1]}…')

        def worker():
            try:
                result = generate(request, log=self._log, cancel=token)
            except CancelledError:
                self._finish('cancelled', gui.CANCELLED_MESSAGE, job=job)
            except Exception as exc:
                self._finish('error', {'message': gui.user_error(exc),
                                       'recovery': _generation_recovery(exc, request.output_blend)}, job=job)
            else:
                self._finish('result', str(result), job=job)
                self._finish('done', gui.completion_message(Path(str(result))),
                             job=job, output=str(result))

        self._spawn(worker)
        return {'started': True, 'output': str(request.output_blend), 'job': job}

    def cancel(self) -> bool:
        with self._lock:
            running, token = self._running, self._token
        if running and token is not None:
            token.cancel()
            self._log('[STOP] 中断要求を受け付けました。通信・子プロセスを終了中')
            return True
        return False

    # ── 結果を開く ──────────────────────────────────────────────────

    def _blender_executable(self) -> Path | None:
        return _path_or_none(self._form.get('blender')) or self._scanned_blender

    def open_result(self) -> dict:
        """Hand the saved .blend to the OS and return any launch error."""
        result = self._result
        try:
            if result is None:
                raise FileNotFoundError('開く生成結果がありません。先に街の生成を完了してください。')
            open_saved_blend(result)
        except OSError as exc:
            self._log(str(exc))
            return {'ok': False, 'error': str(exc)}
        return {'ok': True}

    def open_folder(self) -> bool:
        folder = self._result.parent if self._result else Path(
            str(self._form.get('output') or _default_output())).expanduser().parent
        try:
            open_with_default_app(folder)
        except OSError as exc:
            self._log(f'フォルダを開けませんでした: {exc}')
            return False
        return True

    # ── ファイル窓とURL ─────────────────────────────────────────────

    def choose_output(self, current: str | None = None) -> str | None:
        dialogs = self._dialogs
        if dialogs is None:
            return None
        target = Path(str(current or self._form.get('output') or _default_output()))
        try:
            result = dialogs.save(
                _existing_dir(target.parent),
                target.name or 'scene.blend',
                [('Blender (*.blend)', '*.blend'), ('すべてのファイル (*.*)', '*.*')],
            )
        except Exception as exc:
            self._log(f'保存先を選べませんでした: {exc}')
            return None
        path = _first_path(result)
        if not path:
            return None
        if not path.lower().endswith('.blend'):
            path += '.blend'
        return path

    def choose_file(self, kind: str | None = None) -> str | None:
        if kind not in ('blender', 'nusamai'):
            return None
        dialogs = self._dialogs
        if dialogs is None:
            return None
        try:
            result = dialogs.open(
                _existing_dir(Path.home()),
                [('Windows executable (*.exe)', '*.exe'), ('すべてのファイル (*.*)', '*.*')],
            )
        except Exception as exc:
            self._log(f'ファイルを選べませんでした: {exc}')
            return None
        return _first_path(result)

    def open_url(self, kind: str | None = None, lat=None, lon=None) -> bool:
        """``maps`` と ``blender`` だけ。JS から任意の URL は受けない。

        中心点が日本の範囲にあれば、その場所を中心に地図を開く。
        """
        kind = str(kind or '')
        try:
            if kind == 'maps':
                point = _japan_point(lat, lon)
                url = (MAPS_POINT_URL.format(lat=point[0], lon=point[1])
                       if point else MAPS_URL)
            elif kind == 'blender':
                url = _gui().BLENDER_DOWNLOAD_URL
            else:
                return False
            return bool(webbrowser.open(url))
        except Exception:
            return False

    # ── 詳細設定のボタン ────────────────────────────────────────────

    def preview_sources(self, form: dict | None = None) -> dict:
        self._remember(form)
        try:
            request = request_from_form(self._form)
            request.check()
        except Exception as exc:
            self._log(f'[ERROR] {exc}')
            return {'started': False, 'error': str(exc)}
        token = CancelToken()
        with self._lock:
            if self._running:
                return {'started': False, 'reason': 'running'}
            self._running = True
            self._token = token
            self._stage = 0
            self._announced = 0
            job = self._next_job()

        def exec_request(req: AutoRequest):
            sources = preview(req, cancel=token)
            self._log(f'[CRS] 予定EPSG: {req.effective_epsg(sources)}')
            self._log(f'PLATEAU検索: {len(sources)}ファイル / '
                      f'判明サイズ: {sum(s.size or 0 for s in sources)/1048576:.1f} MiB')
            for source in sources:
                self._log(f'  {source.city} 年度{source.year} / {source.mesh} / '
                          f'LOD{source.max_lod} / {(source.size or 0)/1048576:.1f}MiB')
            if req.condition.startswith('r:'):
                selected = projected_selection(req.condition, req.effective_epsg(sources))
                if selected:
                    plan = forecast(selected, req.effective_epsg(sources),
                                    wanted_zoom=req.photo_zoom,
                                    dem_resolution=req.terrain_resolution)
                    self._log(f'[取得計画] 写真z{plan.photo_zoom} {plan.photo_tiles}タイル、'
                              f'{plan.photo_width}x{plan.photo_height}px、'
                              f'DEM {plan.dem_tile_positions}タイル位置'
                              f'(最大{plan.dem_upper_bound_requests}試行)、'
                              f'頂点約{plan.terrain_vertices}')
                    enforce_budget(plan)
            self._log('※ APIの取得単位はGMLファイル。生成時には矩形を投影XYの近似矩形へ切り出します。')

        def worker():
            try:
                exec_request(request)
            except CancelledError:
                self._finish('cancelled', _gui().CANCELLED_MESSAGE, job=job)
            except Exception as exc:
                self._finish('error', _gui().user_error(exc), job=job)
            else:
                self._finish('done', '取得対象の確認が終わりました。', job=job)

        self._spawn(worker)
        return {'started': True, 'job': job}

    def diagnose(self, form: dict | None = None) -> bool:
        self._remember(form)
        if self._running:
            return False
        blender = _path_or_none(self._form.get('blender'))
        nusamai = _path_or_none(self._form.get('nusamai'))
        output = Path(str(self._form.get('output') or _default_output()))
        self._log('[DIAGNOSTICS] Blender/nusamai の --version を確認中')

        def worker():
            try:
                report = inspect_environment(blender=blender, nusamai=nusamai,
                                             output=output, check_versions=True)
            except Exception as exc:
                self._log('[DIAGNOSTICS ERROR] ' + str(exc))
            else:
                self._log('[DIAGNOSTICS] ' + json.dumps(report, ensure_ascii=False))

        self._spawn(worker)
        return True

    def smoke_test(self, form: dict | None = None) -> dict:
        self._remember(form)
        binary = self._blender_executable()
        parent = Path(str(self._form.get('output') or _default_output())).expanduser().resolve().parent / 'diagnostics'
        token = CancelToken()
        with self._lock:
            if self._running:
                return {'started': False, 'reason': 'running'}
            self._running = True
            self._token = token
            self._stage = 0
            self._announced = 0
            job = self._next_job()
        self._log('[SMOKE] 合成データによるBlender保存・再読込を検証')

        def worker():
            try:
                output = run_smoke(blender=binary, output_parent=parent,
                                   log=self._log, cancel=token)
            except CancelledError:
                self._finish('cancelled', 'Blender自己診断を中止しました。', job=job)
            except Exception as exc:
                self._finish('error', f'{type(exc).__name__}: {exc}', job=job)
            else:
                self._emit('smoke_result', str(output), job=job)
                self._finish('done', 'Blender自己診断成功。検証用 .blend を開いて確認できます。', job=job)

        self._spawn(worker)
        return {'started': True, 'job': job}

    def open_manual(self) -> bool:
        try:
            if getattr(sys, 'frozen', False):
                subprocess.Popen([sys.executable, '--manual'], **hidden_process_kwargs())
            else:
                subprocess.Popen([sys.executable, '-m', 'plateau_blend.gui'],
                                 **hidden_process_kwargs())
        except OSError as exc:
            self._log(f'手動OBJモードを起動できませんでした: {exc}')
            return False
        return True

    # ── ログ ────────────────────────────────────────────────────────

    def log_text(self) -> str:
        return '\n'.join(self._log_lines)

    def save_log(self, path: str | None = None) -> str | None:
        text = self.log_text()
        if not path:
            dialogs = self._dialogs
            if dialogs is None:
                return None
            try:
                result = dialogs.save(
                    _existing_dir(Path(str(self._form.get('output') or _default_output())).expanduser().parent),
                    'plateau_bridge_log.txt',
                    [('テキスト (*.txt)', '*.txt'), ('すべてのファイル (*.*)', '*.*')],
                )
            except Exception as exc:
                self._log(f'ログの保存先を選べませんでした: {exc}')
                return None
            path = _first_path(result)
            if not path:
                return None
        try:
            Path(str(path)).expanduser().write_text(text, encoding='utf-8')
        except OSError as exc:
            self._log(f'ログを保存できませんでした: {exc}')
            return None
        self._log(f'ログを保存しました: {path}')
        return str(path)

    # ── JS が 150ms ごとに呼ぶ ──────────────────────────────────────

    def poll(self) -> list:
        events = []
        with self._lock:
            while True:
                try:
                    events.append(self._events.get_nowait())
                except queue.Empty:
                    break
            self._threads = [thread for thread in self._threads if thread.is_alive()]
        return events

    # ── 内部 ────────────────────────────────────────────────────────

    def _remember(self, form: dict | None) -> dict:
        if isinstance(form, dict):
            self._form = {str(key): value for key, value in form.items()
                          if not str(key).startswith('_')}
        return self._form

    def _next_job(self) -> str:
        """鍵を持っている間に呼ぶ。作業ごとの識別子。"""
        self._job_seq += 1
        self._job = f'job-{self._job_seq}'
        return self._job

    def _emit(self, kind: str, data, job: str | None = None) -> None:
        event = {'kind': kind, 'data': data}
        if kind in JOB_EVENTS:
            event['job_id'] = job if job is not None else self._job
        self._events.put(event)

    def _spawn(self, target) -> None:
        thread = threading.Thread(target=target, daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _log(self, line) -> None:
        text = str(line)
        self._emit('log', text)
        stage = None
        job = None
        with self._lock:
            self._log_lines.append(text)
            if self._running:
                job = self._job
                gui = _gui()
                stage = gui.stage_for(text, self._stage)
                if stage == self._announced:
                    stage = None
                else:
                    self._stage = stage
                    self._announced = stage
        if stage:
            self._emit('stage', {'stage': stage, 'text': _gui().STAGES[stage - 1][1]}, job=job)

    def _finish(self, kind: str, message, job: str | None = None, output: str | None = None) -> None:
        with self._lock:
            job = job if job is not None else self._job
            if kind == 'result':
                self._result = Path(str(message))
            if kind in ('done', 'error', 'cancelled') and job == self._job:
                self._running = False
                self._stage = 0
                self._announced = 0
                self._token = None
            data = {'message': str(message), 'output': output} if kind == 'done' else message
            text = message.get('message') if isinstance(message, dict) else message
        self._emit(kind, data, job=job)
        if kind in ('done', 'error', 'cancelled'):
            with self._lock:
                self._log_lines.append(f'[{kind.upper()}] {text}')
            self._emit('log', f'[{kind.upper()}] {text}')


def _index_path() -> Path:
    """開発時は plateau_blend/web、凍結EXEでは _MEIPASS 基準。"""
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'plateau_blend' / 'web' / 'index.html'


def _screen_size() -> tuple[int, int]:
    if sys.platform == 'win32':
        try:
            import ctypes
            return ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            pass
    return 1920, 1080


def _window_size() -> tuple[int, int]:
    try:
        width, height = (int(value) for value in
                         _gui().initial_geometry(*_screen_size()).split('x'))
    except Exception:
        return 900, 780
    return width, height


class _TkDialogs:
    """pywebview の ``create_file_dialog`` の代わり。

    HTTP ハンドラーのスレッドは依頼をキューへ入れ、メインスレッドの待ちループ
    （``serve_one``）が Tk を開いて結果を返す。Tk はメインスレッド以外で触らない。
    """

    def __init__(self):
        self._requests: queue.Queue = queue.Queue()

    def save(self, directory: str, filename: str, file_types: list[tuple[str, str]]) -> str | None:
        return self._ask(('save', directory, filename, file_types))

    def open(self, directory: str, file_types: list[tuple[str, str]]) -> str | None:
        return self._ask(('open', directory, file_types))

    def serve_one(self) -> bool:
        """メインスレッドから呼ぶ。依頼があれば1件だけ実行する。"""
        try:
            request, box = self._requests.get_nowait()
        except queue.Empty:
            return False
        try:
            box['result'] = self._run(request)
        except Exception as exc:
            box['error'] = exc
        finally:
            box['event'].set()
        return True

    def _ask(self, request):
        box = {'event': threading.Event(), 'result': None, 'error': None}
        self._requests.put((request, box))
        if not box['event'].wait(300):
            return None
        if box['error'] is not None:
            raise box['error']
        return box['result']

    @staticmethod
    def _run(request):
        # tkinter はここでだけ読む（モジュール読込時に Tk を要求しない）。
        from tkinter import Tk, filedialog
        kind = request[0]
        root = Tk()
        try:
            root.withdraw()
            root.attributes('-topmost', True)
            root.update()
            if kind == 'save':
                _, directory, filename, file_types = request
                path = filedialog.asksaveasfilename(
                    parent=root, initialdir=directory, initialfile=filename,
                    filetypes=list(file_types))
            else:
                _, directory, file_types = request
                path = filedialog.askopenfilename(
                    parent=root, initialdir=directory, filetypes=list(file_types))
            return str(path) if path else None
        finally:
            try:
                root.destroy()
            except Exception:
                pass


def _edge_candidates(environment=None) -> list[Path]:
    env = os.environ if environment is None else environment
    candidates = []
    for name in ('ProgramFiles(x86)', 'ProgramFiles'):
        base = str(env.get(name) or '').strip()
        if base:
            candidates.append(Path(base) / _EDGE_RELATIVE)
    return candidates


def _registry_edge() -> Path | None:
    try:
        import winreg
        key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe'
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, _ = winreg.QueryValueEx(key, '')
        path = Path(str(value).strip().strip('"'))
        return path if path.is_file() else None
    except Exception:
        return None


def _find_edge(environment=None, registry_lookup=None) -> Path | None:
    """Edge の msedge.exe を探す。レジストリは読むだけ。"""
    for candidate in _edge_candidates(environment):
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    lookup = _registry_edge if registry_lookup is None else registry_lookup
    try:
        return lookup()
    except Exception:
        return None


def _edge_profile() -> Path:
    return app_paths.edge_profile_dir()


def _edge_args(edge: Path, url: str, profile: Path, width: int, height: int) -> list[str]:
    """新しいプロファイルの Edge は Windows のアカウントで自動サインインして同期を始め、
    利用者の拡張を全部入れる（導入時に案内タブを開く物もある。2026-11-05 実測で約65個）。
    画面は拡張を使わないので、同期と拡張は止める。"""
    return [
        str(edge),
        f'--app={url}',
        f'--user-data-dir={profile}',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-sync',
        '--disable-extensions',
        f'--window-size={width},{height}',
    ]


def _launch_edge(edge: Path, url: str, width: int, height: int):
    profile = _edge_profile()
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return subprocess.Popen(_edge_args(edge, url, profile, width, height))


def _open_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _session_outcome(now, started_at, last_contact, *, process=None, process_alive=True,
                     bye_at=None, first_timeout=FIRST_CONTACT_TIMEOUT,
                     idle_timeout=IDLE_TIMEOUT, bye_grace=BYE_GRACE) -> str:
    """今の状態から次の判断を返す。``wait`` / ``closed`` / ``failed``。

    主な終わりの合図は画面の pagehide から届く ``_bye``。届いた後 ``bye_grace`` 秒
    連絡が戻らなければ閉じた、戻れば再読込とみなす。心拍の途切れは最後の保険。
    Edge は起動した msedge.exe が本体を起動し直してすぐ終わることが多く、その瞬間は
    最初の連絡の前にも後にも来うる。なので最初の連絡の後は、プロセスの生死を見ない
    （2026-11-06 Sol レビュー：連絡の後に抜けると、窓が残ったままサーバーを止めていた）。
    """
    if last_contact is None:
        if process is not None and not process_alive:
            return 'failed'
        return 'failed' if now - started_at >= first_timeout else 'wait'
    if bye_at is not None and bye_at >= last_contact and now - bye_at >= bye_grace:
        return 'closed'
    if now - last_contact >= idle_timeout:
        return 'closed'
    return 'wait'


def main() -> bool:
    """画面を開く。開けなければ理由を残して False を返し、呼び手が Tk へ切り替える。"""
    index = _index_path()
    if not index.is_file():
        write_fallback_log(f'画面ファイルが見つかりません: {index}', traceback.format_exc())
        return False

    api = BridgeApi()
    dialogs = _TkDialogs()
    api._dialogs = dialogs
    server = BridgeServer(api, web_dir=index.parent)
    try:
        url = server.start()
    except Exception as exc:
        write_fallback_log(f'ローカルHTTPサーバーを起動できませんでした: {type(exc).__name__}: {exc}',
                           traceback.format_exc())
        return False

    process = None
    started = time.monotonic()
    opened = False
    try:
        edge = _find_edge()
        width, height = _window_size()
        if edge is not None:
            try:
                process = _launch_edge(edge, url, width, height)
            except Exception:
                process = None
        if process is None and not _open_browser(url):
            write_fallback_log(f'Edgeもブラウザも開けませんでした: {url}', traceback.format_exc())
            return False

        while True:
            dialogs.serve_one()
            now = time.monotonic()
            last = server.last_contact
            if last is not None:
                opened = True
            alive = process is not None and process.poll() is None
            if process is not None and not alive and last is None:
                # 同じプロファイルの Edge が既に開いていると、起動した msedge.exe は
                # そちらへ引き渡してすぐ終わる。窓は開くので、以後は心拍で見守る。
                process = None
            outcome = _session_outcome(now, started, last, process=process, process_alive=alive,
                                       bye_at=server.bye_at)
            if outcome == 'failed':
                write_fallback_log('画面から最初の連絡が60秒以内に来ませんでした',
                                   traceback.format_exc())
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                    except Exception:
                        pass
                return False
            if outcome == 'closed':
                break
            time.sleep(0.2)
    except Exception as exc:
        write_fallback_log(f'{type(exc).__name__}: {exc}', traceback.format_exc())
        return False
    finally:
        try:
            if api.running:
                api.cancel()
        except Exception:
            pass
        server.stop()
    return opened


if __name__ == '__main__':
    if not main():
        from .auto_gui import main as auto_main
        auto_main()
