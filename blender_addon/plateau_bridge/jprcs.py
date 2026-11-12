# SPDX-License-Identifier: MIT
"""平面直角座標系（JGD2011、EPSG:6669〜6687）と緯度経度の変換。純Python。

式は国土地理院「測量計算サイト」の計算式（Krüger 級数、n の5〜6次まで）。
戻り値と引数の平面座標は (東向き m, 北向き m) の順で、Blender の X・Y に合わせる。
公式の呼び名では X が北向き、Y が東向きなので、順番に注意する。
"""

import math

_A = 6378137.0  # GRS80 長半径
_F = 298.257222101  # GRS80 逆扁平率
_M0 = 0.9999  # 原点の縮尺係数

# (EPSG, 原点緯度, 原点経度, 適用範囲 (西, 南, 東, 北))。EPSG の定義（PROJ 由来）。
ZONES = (
    (6669, 33.0, 129.5, (128.17, 26.96, 130.46, 34.74)),
    (6670, 33.0, 131.0, (129.76, 30.18, 132.05, 33.99)),
    (6671, 36.0, 132.0 + 10.0 / 60.0, (130.81, 33.72, 133.49, 36.38)),
    (6672, 33.0, 133.5, (131.95, 32.69, 134.81, 34.45)),
    (6673, 36.0, 134.0 + 20.0 / 60.0, (133.13, 34.13, 135.47, 35.71)),
    (6674, 36.0, 136.0, (134.86, 33.4, 136.99, 36.33)),
    (6675, 36.0, 137.0 + 10.0 / 60.0, (136.22, 34.51, 137.84, 37.58)),
    (6676, 36.0, 138.5, (137.32, 34.54, 139.91, 38.58)),
    (6677, 36.0, 139.0 + 50.0 / 60.0, (138.4, 29.31, 141.11, 37.98)),
    (6678, 40.0, 140.0 + 50.0 / 60.0, (139.49, 37.73, 142.14, 41.58)),
    (6679, 44.0, 140.25, (139.34, 41.34, 141.46, 43.42)),
    (6680, 44.0, 142.25, (140.89, 42.15, 143.61, 45.54)),
    (6681, 44.0, 144.25, (142.61, 41.87, 145.87, 44.4)),
    (6682, 26.0, 142.0, (141.2, 24.67, 142.33, 27.8)),
    (6683, 26.0, 127.5, (126.63, 26.02, 128.4, 26.91)),
    (6684, 26.0, 124.0, (122.83, 23.98, 125.51, 24.94)),
    (6685, 26.0, 131.0, (131.12, 24.4, 131.38, 26.01)),
    (6686, 20.0, 136.0, (136.02, 20.37, 136.16, 20.48)),
    (6687, 26.0, 154.0, (153.91, 24.22, 154.05, 24.35)),
)
_ORIGINS = {code: (lat0, lon0) for code, lat0, lon0, _extent in ZONES}

_N = 1.0 / (2.0 * _F - 1.0)
_N2, _N3, _N4, _N5, _N6 = (_N ** k for k in range(2, 7))

_A0 = 1.0 + _N2 / 4.0 + _N4 / 64.0
_ARC = (
    -3.0 / 2.0 * (_N - _N3 / 8.0 - _N5 / 64.0),
    15.0 / 16.0 * (_N2 - _N4 / 4.0),
    -35.0 / 48.0 * (_N3 - 5.0 / 16.0 * _N5),
    315.0 / 512.0 * _N4,
    -693.0 / 1280.0 * _N5,
)
_ALPHA = (
    _N / 2.0 - 2.0 / 3.0 * _N2 + 5.0 / 16.0 * _N3 + 41.0 / 180.0 * _N4 - 127.0 / 288.0 * _N5,
    13.0 / 48.0 * _N2 - 3.0 / 5.0 * _N3 + 557.0 / 1440.0 * _N4 + 281.0 / 630.0 * _N5,
    61.0 / 240.0 * _N3 - 103.0 / 140.0 * _N4 + 15061.0 / 26880.0 * _N5,
    49561.0 / 161280.0 * _N4 - 179.0 / 168.0 * _N5,
    34729.0 / 80640.0 * _N5,
)
_BETA = (
    _N / 2.0 - 2.0 / 3.0 * _N2 + 37.0 / 96.0 * _N3 - 1.0 / 360.0 * _N4 - 81.0 / 512.0 * _N5,
    1.0 / 48.0 * _N2 + 1.0 / 15.0 * _N3 - 437.0 / 1440.0 * _N4 + 46.0 / 105.0 * _N5,
    17.0 / 480.0 * _N3 - 37.0 / 840.0 * _N4 - 209.0 / 4480.0 * _N5,
    4397.0 / 161280.0 * _N4 - 11.0 / 504.0 * _N5,
    4583.0 / 161280.0 * _N5,
)
_DELTA = (
    2.0 * _N - 2.0 / 3.0 * _N2 - 2.0 * _N3 + 116.0 / 45.0 * _N4 + 26.0 / 45.0 * _N5
    - 2854.0 / 675.0 * _N6,
    7.0 / 3.0 * _N2 - 8.0 / 5.0 * _N3 - 227.0 / 45.0 * _N4 + 2704.0 / 315.0 * _N5
    + 2323.0 / 945.0 * _N6,
    56.0 / 15.0 * _N3 - 136.0 / 35.0 * _N4 - 1262.0 / 105.0 * _N5 + 73814.0 / 2835.0 * _N6,
    4279.0 / 630.0 * _N4 - 332.0 / 35.0 * _N5 - 399572.0 / 14175.0 * _N6,
    4174.0 / 315.0 * _N5 - 144838.0 / 6237.0 * _N6,
    601676.0 / 22275.0 * _N6,
)
_SCALE = _M0 * _A / (1.0 + _N)
_A_BAR = _SCALE * _A0
_E2N = 2.0 * math.sqrt(_N) / (1.0 + _N)


def _meridian_arc(lat0_rad):
    total = _A0 * lat0_rad
    for j, coefficient in enumerate(_ARC, start=1):
        total += coefficient * math.sin(2.0 * j * lat0_rad)
    return _SCALE * total


def _origin(epsg):
    try:
        lat0, lon0 = _ORIGINS[int(epsg)]
    except (KeyError, ValueError):
        raise ValueError("平面直角座標系（EPSG:6669〜6687）ではありません: %r" % (epsg,))
    return math.radians(lat0), math.radians(lon0)


def zone_for(lon, lat):
    """地点を含む適用範囲の系のうち、原点が最も近い系の EPSG 番号を返す。"""
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError("緯度経度が不正です")
    candidates = []
    for code, lat0, lon0, (west, south, east, north) in ZONES:
        if west <= lon <= east and south <= lat <= north:
            score = ((lon - lon0) * math.cos(math.radians(lat))) ** 2 + (lat - lat0) ** 2
            candidates.append((score, code))
    if not candidates:
        raise ValueError("この地点は平面直角座標系19系の定義範囲の外です")
    return min(candidates)[1]


def to_xy(lon, lat, epsg):
    """緯度経度（度）→ 平面直角座標 (東向き m, 北向き m)。"""
    lat0, lon0 = _origin(epsg)
    phi = math.radians(lat)
    dlam = math.radians(lon) - lon0
    sin_phi = math.sin(phi)
    t = math.sinh(math.atanh(sin_phi) - _E2N * math.atanh(_E2N * sin_phi))
    t_bar = math.sqrt(1.0 + t * t)
    xi = math.atan2(t, math.cos(dlam))
    eta = math.atanh(math.sin(dlam) / t_bar)
    north = xi
    east = eta
    for j, alpha in enumerate(_ALPHA, start=1):
        north += alpha * math.sin(2.0 * j * xi) * math.cosh(2.0 * j * eta)
        east += alpha * math.cos(2.0 * j * xi) * math.sinh(2.0 * j * eta)
    return _A_BAR * east, _A_BAR * north - _meridian_arc(lat0)


def to_xy_many(lons, lats, epsg):
    """to_xy の配列版。numpy の配列（度）を受け、(東向き m, 北向き m) の配列を返す。"""
    import numpy as np  # Blender 同梱。建物の頂点をまとめて変換する時だけ使う

    lat0, lon0 = _origin(epsg)
    phi = np.radians(np.asarray(lats, dtype=np.float64))
    dlam = np.radians(np.asarray(lons, dtype=np.float64)) - lon0
    sin_phi = np.sin(phi)
    t = np.sinh(np.arctanh(sin_phi) - _E2N * np.arctanh(_E2N * sin_phi))
    xi = np.arctan2(t, np.cos(dlam))
    eta = np.arctanh(np.sin(dlam) / np.sqrt(1.0 + t * t))
    north = xi.copy()
    east = eta.copy()
    for j, alpha in enumerate(_ALPHA, start=1):
        north += alpha * np.sin(2.0 * j * xi) * np.cosh(2.0 * j * eta)
        east += alpha * np.cos(2.0 * j * xi) * np.sinh(2.0 * j * eta)
    return _A_BAR * east, _A_BAR * north - _meridian_arc(lat0)


def to_lonlat(east, north, epsg):
    """平面直角座標 (東向き m, 北向き m) → 緯度経度（度）。"""
    lat0, lon0 = _origin(epsg)
    xi = (north + _meridian_arc(lat0)) / _A_BAR
    eta = east / _A_BAR
    xi1 = xi
    eta1 = eta
    for j, beta in enumerate(_BETA, start=1):
        xi1 -= beta * math.sin(2.0 * j * xi) * math.cosh(2.0 * j * eta)
        eta1 -= beta * math.cos(2.0 * j * xi) * math.sinh(2.0 * j * eta)
    chi = math.asin(math.sin(xi1) / math.cosh(eta1))
    phi = chi
    for j, delta in enumerate(_DELTA, start=1):
        phi += delta * math.sin(2.0 * j * chi)
    lam = lon0 + math.atan2(math.sinh(eta1), math.cos(xi1))
    return math.degrees(lam), math.degrees(phi)
