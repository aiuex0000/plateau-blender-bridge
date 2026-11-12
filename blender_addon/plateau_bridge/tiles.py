# SPDX-License-Identifier: MIT
"""地理院タイルの取得と保存。標準ライブラリだけで動き、bpy に依存しない。

画像の中身を読むのは Blender 側（terrain.py）で行う。ここはタイル番号の計算、
通信、ディスクへの保存までを受け持つ。
"""

import concurrent.futures
import math
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://cyberjapandata.gsi.go.jp/xyz"
HOST = "cyberjapandata.gsi.go.jp"
PHOTO = "seamlessphoto"
DEM_SOURCES = ("dem5a_png", "dem5b_png", "dem5c_png", "dem_png")
ATTRIBUTION = "出典：国土地理院（地理院タイル：全国最新写真・標高タイル）"
TERMS = "https://maps.gsi.go.jp/help/use.html"

TILE_PIXELS = 256
_MAX_BYTES = 2000000
_MAGIC = {"jpg": b"\xff\xd8", "png": b"\x89PNG"}
_USER_AGENT = "PLATEAU-Blender-Bridge-Addon/0.1"


def _safe_url(url):
    parts = urllib.parse.urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == HOST
        and not parts.username
        and not parts.password
        and parts.port in (None, 443)
    )


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_url(newurl):
            raise ValueError("地理院タイルの外部ホストへのリダイレクトを拒否しました")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPEN = urllib.request.build_opener(_SafeRedirect()).open


def fractional_tile(lon, lat, zoom):
    """緯度経度 → タイル座標（小数）。x は東向き、y は南向きに増える。"""
    if not (-180.0 <= lon <= 180.0 and -85.05112878 < lat < 85.05112878):
        raise ValueError("WebMercator の範囲外です")
    n = 2 ** zoom
    return (
        (lon + 180.0) / 360.0 * n,
        (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n,
    )


def decode_dem(r, g, b):
    """標高タイルの画素 → 標高 m。無効値は None。"""
    value = (r << 16) + (g << 8) + b
    if value == 8388608:
        return None
    return (value - 16777216 if value > 8388608 else value) * 0.01


def _extension(layer):
    return "jpg" if layer == PHOTO else "png"


def _download(layer, z, x, y):
    """タイルの中身を返す。地理院に無いタイル（404）は None。"""
    extension = _extension(layer)
    url = "%s/%s/%d/%d/%d.%s" % (BASE, layer, z, x, y, extension)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _OPEN(request, timeout=30) as response:
            if not _safe_url(response.geturl()):
                raise ValueError("地理院タイルが許可外のホストへ転送されました")
            data = response.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if len(data) > _MAX_BYTES:
        raise ValueError("タイルが2MBを超えています: " + url)
    if not data.startswith(_MAGIC[extension]):
        # 通信フィルタが HTML の案内ページを返した時などに、原因が分かるよう止める
        raise ValueError("画像ではないデータを受け取りました（通信フィルタの可能性）: " + url)
    return data


class TileCache:
    """タイルをディスクへ保存し、ファイルの場所を返す。"""

    def __init__(self, root, max_tiles=160):
        self.root = root
        self.max_tiles = max_tiles
        self.attempts = 0
        self.downloaded = 0
        self.downloaded_bytes = 0
        self._paths = {}  # (layer, z, x, y) -> ファイルの場所、無いタイルは None

    def _path(self, key):
        layer, z, x, y = key
        return os.path.join(self.root, layer, str(z), str(x), "%d.%s" % (y, _extension(layer)))

    def _check(self, key):
        layer, z, x, y = key
        if layer != PHOTO and layer not in DEM_SOURCES:
            raise ValueError("未許可の地理院レイヤーです: %s" % layer)
        if z < 0 or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise ValueError("タイル座標が範囲外です")

    def _store(self, key, data):
        if data is None:
            self._paths[key] = None
            return
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        partial = path + ".part"
        with open(partial, "wb") as file:
            file.write(data)
        os.replace(partial, path)
        self._paths[key] = path
        self.downloaded += 1
        self.downloaded_bytes += len(data)

    def _pending(self, keys):
        pending = []
        for key in keys:
            if key in self._paths or key in pending:
                continue
            self._check(key)
            self.attempts += 1
            if self.attempts > self.max_tiles:
                raise ValueError("地理院タイルの取得数が上限（%d）に達しました" % self.max_tiles)
            path = self._path(key)
            if os.path.isfile(path):
                self._paths[key] = path
            else:
                pending.append(key)
        return pending

    def prefetch(self, keys):
        """まだ手元に無いタイルを、4本まで並行して取る。"""
        pending = self._pending(keys)
        if not pending:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for key, data in zip(pending, pool.map(lambda k: _download(*k), pending)):
                self._store(key, data)

    def get(self, layer, z, x, y):
        """タイルのファイルの場所。地理院に無いタイルは None。"""
        key = (layer, z, x, y)
        if key not in self._paths:
            self.prefetch([key])
        return self._paths[key]

    def discard(self, layer, z, x, y):
        """壊れた保存ファイルを捨て、次回に取り直せるようにする。"""
        key = (layer, z, x, y)
        path = self._paths.pop(key, None)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
