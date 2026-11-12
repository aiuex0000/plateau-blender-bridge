r"""place（場所の文字の読み取り）を確かめる。通信なし。

    "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe" blender_addon/tests/check_place.py
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "place", ROOT / "blender_addon" / "plateau_bridge" / "place.py"
)
place = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(place)


def near(a, b, tolerance=1e-9):
    return abs(a - b) < tolerance


def rejects(text):
    try:
        place.parse_place(text)
    except ValueError:
        return True
    return False


def main():
    spot = place.parse_place("35.6905, 139.6995")
    assert near(spot.lat, 35.6905) and near(spot.lon, 139.6995) and spot.size is None
    assert spot.rect is None and spot.note is None

    spot = place.parse_place("139.6995 35.6905 250")  # 経度が先、空白区切り、一辺つき
    assert near(spot.lat, 35.6905) and near(spot.lon, 139.6995) and spot.size == 250
    assert spot.note and "緯度 35.6905" in spot.note

    spot = place.parse_place("３５．６９０５、１３９．６９９５")  # 全角
    assert near(spot.lat, 35.6905) and near(spot.lon, 139.6995)

    spot = place.parse_place("https://www.google.com/maps/@35.6812,139.7671,17z")
    assert near(spot.lat, 35.6812) and near(spot.lon, 139.7671) and spot.size is None

    spot = place.parse_place("35°41'22.6\"N 139°41'30.1\"E")
    assert near(spot.lat, 35 + 41 / 60 + 22.6 / 3600) and near(spot.lon, 139 + 41 / 60 + 30.1 / 3600)

    spot = place.parse_place("r:139.69,35.68,139.70,35.69")
    assert spot.rect == (139.69, 35.68, 139.70, 35.69) and near(spot.lon, 139.695)
    assert place.parse_place("35.68, 139.69, 35.69, 139.70").rect == (139.69, 35.68, 139.70, 35.69)

    # 3次メッシュ 53394525: 南西の角は 緯度 35.6833..., 経度 139.6875。大きさは 30秒 x 45秒
    west, south, east, north = place.mesh_rect("53394525")
    assert near(west, 139.6875) and near(south, 53 / 1.5 + 4 / 12 + 2 / 120)
    assert near(east - west, 1 / 80) and near(north - south, 1 / 120)
    inner = place.parse_place("m:53394525").rect  # 境界の内側へ約10cm寄せてある
    assert all(abs(a - b) < 2e-6 for a, b in zip(inner, (west, south, east, north)))
    assert inner[0] > west and inner[1] > south and inner[2] < east and inner[3] < north
    half = place.mesh_rect("533945254")  # 4 は北東の4分の1
    assert near(half[0], west + 1 / 160) and near(half[1], south + 1 / 240)

    for bad in ("abc", "35.68", "10, 139", "35.68, 139.69, 35.68, 139.70", "533999", "53394589"[:6]):
        assert rejects(bad), bad
    assert len(place.PRESETS) == 10
    for _name, coords, size in place.PRESETS:
        assert place.parse_place(coords).rect is None and size in (200, 400, 800)
    print("OK")


if __name__ == "__main__":
    main()
