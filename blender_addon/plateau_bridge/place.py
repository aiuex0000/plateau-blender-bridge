# SPDX-License-Identifier: MIT
"""利用者が入れた「場所」の文字を読む。bpy に依存しない。

exe 版（plateau_blend/auto.py の center_condition）と同じ寛容さで受ける。日本の中だけ。
"""

import re
import unicodedata

# 有名な場所（表示名, 緯度と経度, 一辺 m）。exe 版の PLACE_PRESETS と同じ
PRESETS = (
    ("東京タワー", "35.6586, 139.7454", 400),
    ("東京スカイツリー", "35.7101, 139.8107", 400),
    ("新宿駅西口", "35.6905, 139.6995", 200),
    ("渋谷スクランブル交差点", "35.6595, 139.7005", 200),
    ("東京駅 丸の内", "35.6812, 139.7671", 400),
    ("富士山 山頂", "35.3606, 138.7274", 800),
    ("横浜 みなとみらい", "35.4575, 139.6320", 400),
    ("大阪城", "34.6873, 135.5262", 400),
    ("京都駅", "34.9858, 135.7588", 400),
    ("広島 原爆ドーム", "34.3955, 132.4536", 400),
)

_URL = re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
_DMS = re.compile(r"(\d+)\s*°\s*(\d+)\s*['′]\s*(\d+(?:\.\d+)?)\s*(?:\"|″|'')?\s*([NSEW])")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_INSET = 1e-6


def _is_lat(value):
    return 20.0 <= value <= 46.0


def _is_lon(value):
    return 122.0 <= value <= 154.0


class Place:
    """読み取った場所。rect（西, 南, 東, 北）があれば範囲はそれ、無ければ中心と一辺。"""

    def __init__(self, lon, lat, size=None, rect=None, note=None):
        self.lon = lon
        self.lat = lat
        self.size = size  # 入力に一辺 m が書かれていた時だけ
        self.rect = rect
        self.note = note  # 利用者へ知らせたい読み替え


def mesh_rect(mesh):
    """地域メッシュコード（3次メッシュとその分割、8〜10桁）→ 西, 南, 東, 北。"""
    if not re.fullmatch(r"[0-9]{8,10}", mesh):
        raise ValueError("メッシュコードは8〜10桁の数字で入れてください: %s" % mesh)
    if mesh[4] not in "01234567" or mesh[5] not in "01234567":
        raise ValueError("2次メッシュの番号が正しくありません: %s" % mesh)
    lat = int(mesh[:2]) * 2.0 / 3.0
    lon = 100.0 + int(mesh[2:4])
    dlat, dlon = 2.0 / 3.0 / 8.0, 1.0 / 8.0
    lat += int(mesh[4]) * dlat
    lon += int(mesh[5]) * dlon
    dlat, dlon = dlat / 10.0, dlon / 10.0
    lat += int(mesh[6]) * dlat
    lon += int(mesh[7]) * dlon
    for digit in mesh[8:]:
        if digit not in "1234":
            raise ValueError("分割メッシュの番号が正しくありません: %s" % mesh)
        dlat, dlon = dlat / 2.0, dlon / 2.0
        lat += (digit in "34") * dlat
        lon += (digit in "24") * dlon
    return lon, lat, lon + dlon, lat + dlat


def parse_place(text):
    """「緯度, 経度（, 一辺m）」、地図の URL、度分秒、4つの数字の矩形、メッシュコードを読む。"""
    shown = text.strip()[:60]
    text = unicodedata.normalize("NFKC", text).strip()
    for prefix in ("r:", "m:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if re.fullmatch(r"[0-9]{8,10}", text):
        west, south, east, north = mesh_rect(text)
        # 境界線ちょうどだと隣のメッシュまで掛かるので、わずかに内側へ寄せる（約10cm）
        west, south, east, north = west + _INSET, south + _INSET, east - _INSET, north - _INSET
        return Place((west + east) / 2.0, (south + north) / 2.0, rect=(west, south, east, north))

    url = _URL.search(text)
    dms = _DMS.findall(text)
    if url:
        numbers = [float(url.group(1)), float(url.group(2))]
    elif len(dms) == 2:
        numbers = [int(d) + int(m) / 60.0 + float(s) / 3600.0 for d, m, s, _ in dms]
    else:
        numbers = [float(n) for n in _NUMBER.findall(text)]

    if len(numbers) == 4:
        a, b, c, d = numbers
        if _is_lon(a) and _is_lat(b) and _is_lon(c) and _is_lat(d):
            west, south, east, north = a, b, c, d
        elif _is_lat(a) and _is_lon(b) and _is_lat(c) and _is_lon(d):
            south, west, north, east = a, b, c, d
        else:
            raise ValueError("4つの数字を範囲として読めません: 「%s」" % shown)
        west, east = sorted((west, east))
        south, north = sorted((south, north))
        if east - west < 1e-5 or north - south < 1e-5:
            raise ValueError("範囲に幅がありません: 「%s」" % shown)
        return Place((west + east) / 2.0, (south + north) / 2.0, rect=(west, south, east, north))

    if len(numbers) not in (2, 3):
        raise ValueError(
            "場所は「緯度, 経度」か「緯度, 経度, 一辺m」で入れてください（入力: 「%s」）" % shown
        )
    lat, lon = numbers[0], numbers[1]
    note = None
    if _is_lon(lat) and _is_lat(lon):
        lat, lon = lon, lat
        note = "経度・緯度の順と判断して、緯度 %g・経度 %g として使います" % (lat, lon)
    if not (_is_lat(lat) and _is_lon(lon)):
        raise ValueError("日本の緯度・経度ではありません（入力: 「%s」）" % shown)
    return Place(lon, lat, size=numbers[2] if len(numbers) == 3 else None, note=note)
