"""B案（一問ずつ）の画面。pywebview の窓と、手書き HTML/JS から呼ばれる Python 側。

このモジュール自身は tkinter も webview も import しない。Tk の文言・純粋な補助関数は
``auto_gui`` が正本なので初回利用時に読み込み、pywebview は ``main()`` の中だけで import する。

イベントは ``{"kind": ..., "data": ...}``。作業に紐づく ``stage`` / ``result`` / ``done`` /
``error`` / ``cancelled`` には ``job_id`` を添える。JS は自分の ``job_id`` 以外のイベントで
画面の状態を変えない（ログには出す）。
"""
from __future__ import annotations

import dataclasses
import json
import os
import queue
import re
import subprocess
import sys
import threading
import unicodedata
import webbrowser
from pathlib import Path

from .auto import (AutoRequest, _is_lat, _is_lon, center_condition, generate, preview,
                   projected_selection)
from .catalog import NoPlateauData
from .diagnostics import inspect_environment
from .discovery import blender_candidates, find_nusamai
from .resources import enforce_budget, forecast
from .runtime import CancelToken, CancelledError, hidden_process_kwargs
from .smoke import run_smoke
from . import __version__

MAPS_URL = 'https://www.google.com/maps'
MAPS_POINT_URL = 'https://www.google.com/maps/@{lat},{lon},16z'
JOB_EVENTS = ('stage', 'result', 'done', 'error', 'cancelled')
_APP_FOLDER_NAME = '作った街'
_NUM = re.compile(r'-?\d+(?:\.\d+)?')
_MAP_POINT = re.compile(r'@-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?')
_DMS = re.compile(r'(\d+)\s*°\s*(\d+)\s*[\'′]\s*(\d+(?:\.\d+)?)\s*(?:"|″|\'\')?\s*([NSEW])')
_GUI = None


def _gui():
    """auto_gui は Tk を import するので、必要になった時だけ読む（この module の先頭では読まない）。"""
    global _GUI
    if _GUI is None:
        from . import auto_gui
        _GUI = auto_gui
    return _GUI


def _default_output() -> Path:
    return Path.home() / 'Documents' / 'PLATEAU' / 'scene.blend'


def _app_folder() -> Path:
    """凍結EXEでは実行ファイルの隣、開発時は repo 側の ``作った街``。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / _APP_FOLDER_NAME
    return Path(__file__).resolve().parent.parent / _APP_FOLDER_NAME


def _path_or_none(value) -> Path | None:
    text = str(value or '').strip()
    return Path(text) if text else None


def _first_path(result) -> str | None:
    """pywebview の file dialog は SAVE で文字列、OPEN で tuple を返す。どちらも受ける。"""
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


def request_from_form(form: dict, notes: list[str] | None = None) -> AutoRequest:
    """画面の辞書から ``AutoRequest`` を作る（auto_gui.build_request と同じ規則）。"""
    form = form or {}
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
    output = str(form.get('output') or '').strip()
    if not output:
        raise ValueError('保存先を指定してください')
    clip = form.get('clip', True)
    if isinstance(clip, str):
        clip = clip.strip().lower() not in ('', '0', 'false', 'off', 'no')
    return AutoRequest(
        condition=condition,
        epsg=str(form.get('epsg') or 'AUTO').strip() or 'AUTO',
        output_blend=Path(output).expanduser().resolve(),
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


class BridgeApi:
    """JS から ``window.pywebview.api.<名前>(...)`` で呼ばれる面。

    pywebview は ``_`` で始まらない属性とメソッドを JS へ見せるので、窓・キュー・
    スレッド・CancelToken はすべて ``_`` 付きで持つ。
    """

    def __init__(self):
        self._window = None
        self._file_dialog = None
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
        return {'documents': str(_default_output().parent), 'app': str(_app_folder())}

    def use_output_folder(self, kind: str | None = None, current: str | None = None) -> dict | None:
        """``documents`` / ``app`` へ保存場所を切り替える。書けなければドキュメントへ戻す。"""
        kind = str(kind or '')
        if kind not in ('documents', 'app'):
            return None
        raw = str(current or '').strip()
        name = Path(raw).name if raw else _default_output().name
        if not name:
            name = _default_output().name
        if not name.lower().endswith('.blend'):
            name += '.blend'
        folder = _default_output().parent if kind == 'documents' else _app_folder()
        if _folder_writable(folder):
            return {'output': str(folder / name), 'folder': str(folder), 'kind': kind}
        fallback = _default_output().parent
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
                self._finish('error', gui.user_error(exc), job=job)
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

    def open_result(self) -> bool:
        if not self._result:
            return False
        blender = self._blender_executable()
        if blender is None:
            self._log('Blenderが見つかりません。詳細設定でBlender本体を指定してください。')
            return False
        try:
            subprocess.Popen([str(blender), str(self._result)], **hidden_process_kwargs())
        except OSError as exc:
            self._log(f'Blenderを起動できませんでした: {exc}')
            return False
        return True

    def open_folder(self) -> bool:
        folder = self._result.parent if self._result else Path(
            str(self._form.get('output') or _default_output())).expanduser().parent
        try:
            if sys.platform == 'win32':
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(folder)], **hidden_process_kwargs())
            else:
                subprocess.Popen(['xdg-open', str(folder)], **hidden_process_kwargs())
        except OSError as exc:
            self._log(f'フォルダを開けませんでした: {exc}')
            return False
        return True

    # ── ファイル窓とURL ─────────────────────────────────────────────

    def choose_output(self, current: str | None = None) -> str | None:
        window, dialog = self._window, self._file_dialog
        if window is None or dialog is None:
            return None
        target = Path(str(current or self._form.get('output') or _default_output()))
        try:
            result = window.create_file_dialog(
                dialog.SAVE,
                directory=_existing_dir(target.parent),
                save_filename=target.name or 'scene.blend',
                file_types=('Blender (*.blend)', 'すべてのファイル (*.*)'),
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
        window, dialog = self._window, self._file_dialog
        if window is None or dialog is None:
            return None
        try:
            result = window.create_file_dialog(
                dialog.OPEN,
                directory=_existing_dir(Path.home()),
                allow_multiple=False,
                file_types=('Windows executable (*.exe)', 'すべてのファイル (*.*)'),
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
            window, dialog = self._window, self._file_dialog
            if window is None or dialog is None:
                return None
            try:
                result = window.create_file_dialog(
                    dialog.SAVE,
                    directory=_existing_dir(Path(str(self._form.get('output') or _default_output())).expanduser().parent),
                    save_filename='plateau_bridge_log.txt',
                    file_types=('テキスト (*.txt)', 'すべてのファイル (*.*)'),
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
        self._emit(kind, data, job=job)
        if kind in ('done', 'error', 'cancelled'):
            with self._lock:
                self._log_lines.append(f'[{kind.upper()}] {message}')
            self._emit('log', f'[{kind.upper()}] {message}')


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


def main() -> bool:
    """窓を開く。開けなければ例外を出さずに False を返し、呼び手が Tk へ切り替える。"""
    index = _index_path()
    if not index.is_file():
        return False
    try:
        import webview
    except Exception:
        return False
    api = BridgeApi()
    width, height = _window_size()
    try:
        window = webview.create_window(
            f'PLATEAU → Blender | 自動生成 v{__version__.rsplit(".", 1)[0]}',
            url=str(index),
            js_api=api,
            width=width,
            height=height,
            min_size=(730, 580),
            background_color='#FFF8F5',
        )
        api._window = window
        api._file_dialog = webview.FileDialog
        webview.start()
    except Exception:
        api._window = None
        api._file_dialog = None
        return False
    return True


if __name__ == '__main__':
    if not main():
        from .auto_gui import main as auto_main
        auto_main()
