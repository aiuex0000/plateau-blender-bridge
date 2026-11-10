"""画面をローカルHTTPで配り、``BridgeApi`` を JSON 越しに呼ぶ小さなサーバー。

pywebview（pythonnet 経由の WebView2）を使わず、Edge のアプリ窓や普通のブラウザから
``http://127.0.0.1:<port>/?t=<token>`` を開けるようにする。標準ライブラリだけで動く。

守る境界:

- bind は ``127.0.0.1`` のポート0（OS任せ）。外からは届かない。
- 静的配信は ``web`` ディレクトリ直下の実在ファイルだけ。``..`` や区切り文字は404。
- API は ``POST /api/<name>`` だけ。``X-Bridge-Token`` と ``Host`` の両方を確かめる。
- 呼べるのは ``BridgeApi`` のクラスに定義された、``_`` で始まらない呼び出し可能メソッドだけ。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_BODY_BYTES = 1024 * 1024
BYE_NAME = '_bye'  # 画面の pagehide から届く合図。BridgeApi のメソッドではない
_CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
}
_JSON_TYPE = 'application/json; charset=utf-8'


class _BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, bridge):
        super().__init__(address, handler)
        self.bridge = bridge


class BridgeServer:
    """``BridgeApi`` を 127.0.0.1 の HTTP に載せる。"""

    def __init__(self, api, web_dir=None, token=None):
        self.api = api
        self.web_dir = Path(web_dir) if web_dir is not None else Path(__file__).resolve().parent / 'web'
        self.token = str(token) if token else secrets.token_urlsafe(24)
        self._httpd: _BridgeHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_contact: float | None = None
        self._bye_at: float | None = None
        self._page_served_at: float | None = None

    # ── 起動・終了 ──────────────────────────────────────────────────

    @property
    def port(self) -> int:
        if self._httpd is None:
            return 0
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}/?t={self.token}'

    def start(self) -> str:
        """サーバーを立てて、開くURLを返す。bindできなければ例外。"""
        httpd = _BridgeHTTPServer(('127.0.0.1', 0), _BridgeRequestHandler, self)
        thread = threading.Thread(target=httpd.serve_forever,
                                  kwargs={'poll_interval': 0.1}, daemon=True)
        self._httpd = httpd
        try:
            thread.start()
        except Exception:
            self._httpd = None
            httpd.server_close()
            raise
        self._thread = thread
        return self.url

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        self._thread = None
        if httpd is None:
            return
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass

    # ── 心拍 ────────────────────────────────────────────────────────

    def touch(self) -> None:
        """画面から連絡が来た時刻（monotonic）を更新する。"""
        with self._lock:
            self._last_contact = time.monotonic()

    def mark_bye(self) -> None:
        """画面が閉じる（または再読込する）時の合図。この後に連絡が戻れば再読込。"""
        with self._lock:
            self._bye_at = time.monotonic()

    def mark_page_served(self) -> None:
        """画面（index.html）を返せた。API の連絡がまだでも、窓は開いている。"""
        with self._lock:
            if self._page_served_at is None:
                self._page_served_at = time.monotonic()

    @property
    def page_served_at(self) -> float | None:
        with self._lock:
            return self._page_served_at

    @property
    def last_contact(self) -> float | None:
        with self._lock:
            return self._last_contact

    @property
    def bye_at(self) -> float | None:
        with self._lock:
            return self._bye_at


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = 'PlateauBridge/1.0'
    sys_version = ''

    def log_message(self, *args):
        """標準エラーへ出さない。"""
        return

    # ── 入口 ────────────────────────────────────────────────────────

    def do_GET(self):
        if not self._host_ok():
            return self._send_json(403, {'ok': False, 'error': 'host not allowed'})
        return self._serve_static()

    def do_POST(self):
        if not self._host_ok():
            return self._send_json(403, {'ok': False, 'error': 'host not allowed'})
        return self._serve_api()

    # ── 静的配信 ────────────────────────────────────────────────────

    def _serve_static(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in ('', '/'):
            name = 'index.html'
        elif path.startswith('/'):
            name = path[1:]
        else:
            return self._send_json(404, {'ok': False, 'error': 'not found'})
        if not _safe_name(name):
            return self._send_json(404, {'ok': False, 'error': 'not found'})
        bridge: BridgeServer = self.server.bridge
        root = bridge.web_dir.resolve()
        target = (root / name)
        try:
            if not target.is_file() or target.resolve().parent != root:
                raise OSError('not a file')
            body = target.read_bytes()
        except OSError:
            return self._send_json(404, {'ok': False, 'error': 'not found'})
        content_type = _CONTENT_TYPES.get(target.suffix.lower(), 'application/octet-stream')
        self._send(200, body, content_type)
        if name == 'index.html':
            bridge.mark_page_served()

    # ── API ────────────────────────────────────────────────────────

    def _serve_api(self) -> None:
        bridge: BridgeServer = self.server.bridge
        expected = str(self.headers.get('X-Bridge-Token') or '')
        if not secrets.compare_digest(expected, bridge.token):
            return self._send_json(403, {'ok': False, 'error': 'bad token'})

        path = unquote(urlsplit(self.path).path)
        if not path.startswith('/api/'):
            return self._send_json(404, {'ok': False, 'error': 'not found'})
        name = path[len('/api/'):]
        if name == BYE_NAME:
            # 心拍には数えない（この後に連絡が戻るかどうかで閉じたか再読込かを分ける）。
            bridge.mark_bye()
            return self._send_json(200, {'ok': True, 'result': None})
        if not _safe_name(name):
            return self._send_json(404, {'ok': False, 'error': 'unknown method'})

        # 画面から連絡が来た（心拍）。名前が不正でも「届いた」こと自体は記録する。
        bridge.touch()

        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            length = 0
        if length < 0:
            return self._send_json(400, {'ok': False, 'error': 'bad content length'})
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            return self._send_json(413, {'ok': False, 'error': 'body too large'})

        raw = self.rfile.read(length) if length else b''
        try:
            payload = json.loads(raw.decode('utf-8')) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._send_json(400, {'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
        if not isinstance(payload, dict):
            return self._send_json(400, {'ok': False, 'error': 'body must be an object'})
        args = payload.get('args') or []
        if not isinstance(args, list):
            return self._send_json(400, {'ok': False, 'error': 'args must be a list'})

        method = _public_method(bridge.api, name)
        if method is None:
            return self._send_json(404, {'ok': False, 'error': 'unknown method'})

        try:
            result = method(*args)
        except Exception as exc:  # 画面へ理由を返し、サーバーは落とさない
            return self._send_json(200, {'ok': False,
                                         'error': f'{type(exc).__name__}: {exc}'})
        return self._send_json(200, {'ok': True, 'result': result})

    # ── 返事 ────────────────────────────────────────────────────────

    def _host_ok(self) -> bool:
        bridge: BridgeServer = self.server.bridge
        expected = f'127.0.0.1:{bridge.port}'
        host = str(self.headers.get('Host') or '').strip()
        return host.lower() == expected.lower()

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
        self._send(status, body, _JSON_TYPE)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        # 毎回閉じると明示する。AdGuard など 127.0.0.1 を中継するフィルタが、
        # 閉じた接続を使い回して2回に1回 Failed to fetch になるのを防ぐ（2026-11-10 実機）。
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except OSError:
                pass


def _safe_name(name: str) -> bool:
    """web直下の1ファイル名として安全か。区切り・親参照・空を拒む。"""
    if not name or name in ('.', '..'):
        return False
    if '..' in name or '/' in name or '\\' in name:
        return False
    if '\x00' in name:
        return False
    return True


def _public_method(api, name: str):
    """``api`` のクラスに定義された、呼べる公開メソッドだけを返す。"""
    if not name or name.startswith('_') or not name.isidentifier():
        return None
    attr = getattr(type(api), name, None)
    if attr is None or isinstance(attr, property) or not callable(attr):
        return None
    method = getattr(api, name, None)
    return method if callable(method) else None
