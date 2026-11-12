"""jprcs（純Python の平面直角座標変換）を pyproj と既存の系選択に突き合わせる。

pyproj が入った環境で実行する。pytest は要らない。

    .build-venv/Scripts/python.exe blender_addon/tests/check_jprcs.py
"""

import importlib.util
import pathlib
import random
import sys

import pyproj

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from plateau_blend import crs  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "jprcs", ROOT / "blender_addon" / "plateau_bridge" / "jprcs.py"
)
jprcs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(jprcs)


def check_transforms():
    worst_m = worst_deg = 0.0
    for code, _lat0, _lon0, (west, south, east, north) in jprcs.ZONES:
        rng = random.Random(code)
        forward = pyproj.Transformer.from_crs(4326, code, always_xy=True)
        inverse = pyproj.Transformer.from_crs(code, 4326, always_xy=True)
        points = [(west, south), (east, north), (west, north), (east, south)]
        points += [(rng.uniform(west, east), rng.uniform(south, north)) for _ in range(300)]
        for lon, lat in points:
            expected = forward.transform(lon, lat)
            x, y = jprcs.to_xy(lon, lat, code)
            worst_m = max(worst_m, abs(x - expected[0]), abs(y - expected[1]))
            expected_lonlat = inverse.transform(x, y)
            back = jprcs.to_lonlat(x, y, code)
            worst_deg = max(
                worst_deg,
                abs(back[0] - expected_lonlat[0]),
                abs(back[1] - expected_lonlat[1]),
                abs(back[0] - lon),
                abs(back[1] - lat),
            )
    print("transform: worst %.3e m, %.3e deg over 19 zones" % (worst_m, worst_deg))
    assert worst_m < 1e-4, worst_m
    assert worst_deg < 1e-9, worst_deg


def check_zone_selection():
    rng = random.Random(0)
    inside = outside = 0
    for _ in range(5000):
        lon, lat = rng.uniform(122.0, 155.0), rng.uniform(20.0, 46.0)
        try:
            expected = crs.japanese_zone_from_bbox(lon - 1e-6, lat - 1e-6, lon + 1e-6, lat + 1e-6)
        except ValueError:
            expected = None
        try:
            actual = "EPSG:%d" % jprcs.zone_for(lon, lat)
        except ValueError:
            actual = None
        assert actual == expected, (lon, lat, actual, expected)
        inside += expected is not None
        outside += expected is None
    print("zone selection: %d inside, %d outside, all equal" % (inside, outside))
    assert inside > 500


def check_rejects_other_crs():
    try:
        jprcs.to_xy(139.0, 35.0, 4326)
    except ValueError:
        return
    raise AssertionError("EPSG:4326 を受け付けてしまった")


if __name__ == "__main__":
    check_transforms()
    check_zone_selection()
    check_rejects_other_crs()
    print("OK")
