# SPDX-License-Identifier: MIT
"""建物テクスチャのURL解決、保存、上限をネットワークなしで確認する。"""

import hashlib
import importlib
import pathlib
import tempfile
import types
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
pkg = types.ModuleType("plateau_bridge")
pkg.__path__ = [str(ROOT / "blender_addon" / "plateau_bridge")]
sys.modules["plateau_bridge"] = pkg
textures = importlib.import_module("plateau_bridge.textures")

GML = (
    "https://assets.cms.plateau.reearth.io/assets/84/48ebed-93d8-4196-bdda-"
    "e1db9590d3d1/13104_shinjuku-ku_pref_2025_citygml_1_op/udx/bldg/"
    "53394526_bldg_6697_op.gml"
)
REFERENCE = (
    "53394526_bldg_6697_appearance/RoofSurfaceTexture_00002747_00002747_13104.jpg"
)
BASE = (
    "https://assets.cms.plateau.reearth.io/assets/84/48ebed-93d8-4196-bdda-"
    "e1db9590d3d1/13104_shinjuku-ku_pref_2025_citygml_1_op/"
)
IMAGES = {
    ".jpg": b"\xff\xd8" + b"jpeg-image!!",
    ".png": b"\x89PNG" + b"png-image!!",
    ".tif": b"II*\0" + b"tiff-image!",
}


class FakeResponse:
    def __init__(self, url, data):
        self.url = url
        self.data = data
        self.position = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if size < 0:
            size = len(self.data)
        chunk = self.data[self.position:self.position + size]
        self.position += len(chunk)
        return chunk


class FakeOpener:
    def __init__(self, bodies, final_urls=None):
        self.bodies = bodies
        self.final_urls = final_urls or {}
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, request, timeout):
        with self.lock:
            self.calls.append((request.full_url, timeout, request.headers))
        url = request.full_url
        return FakeResponse(self.final_urls.get(url, url), self.bodies[url])


def expect_value_error(function):
    try:
        function()
    except ValueError:
        return
    raise AssertionError("ValueError が発生しませんでした")


def check_resolution():
    expected_relative = (
        "udx/bldg/53394526_bldg_6697_appearance/"
        "RoofSurfaceTexture_00002747_00002747_13104.jpg"
    )
    expected_url = BASE + expected_relative
    assert textures.resolve_asset(GML, REFERENCE) == (expected_url, expected_relative)
    for reference in ("../../../x.jpg", "https://evil.example/x.jpg", "x%20y.jpg",
                      "x.gif"):
        expect_value_error(lambda value=reference: textures.resolve_asset(GML, value))
    expect_value_error(lambda: textures.resolve_asset(
        GML.replace("assets.cms.plateau.reearth.io", "evil.example"), "x.jpg"
    ))
    print("resolve: example and 5 rejection cases OK")


def check_fetch():
    references = ["one.jpg", "two.png", "three.tif"]
    urls = [BASE + "udx/bldg/" + reference for reference in references]
    bodies = {url: IMAGES[pathlib.Path(url).suffix] for url in urls}
    opener = FakeOpener(bodies)
    with tempfile.TemporaryDirectory() as folder:
        store = textures.TextureStore(folder, opener=opener, workers=3)
        paths = store.fetch(GML, references)
        assert set(paths) == set(references)
        assert all(pathlib.Path(path).is_absolute() for path in paths.values())
        assert [pathlib.Path(paths[ref]).read_bytes() for ref in references] == [
            bodies[url] for url in urls
        ]
        assert len(opener.calls) == 3
        assert all(call[1] == 30 for call in opener.calls)
        assert store.downloaded_count == 3
        assert store.downloaded_bytes == sum(map(len, bodies.values()))
        assert store.total_bytes == store.downloaded_bytes
        store.fetch(GML, references)
        assert len(opener.calls) == 3
        assert store.downloaded_count == 3
        assert store.downloaded_bytes == sum(map(len, bodies.values()))
        assert store.total_bytes == sum(map(len, bodies.values()))
    print("fetch: 3 images saved; repeat used cache")


def check_failures_and_limits():
    with tempfile.TemporaryDirectory() as folder:
        html_url = BASE + "udx/bldg/bad.jpg"
        html_opener = FakeOpener({html_url: b"<html>filter response</html>"})
        store = textures.TextureStore(folder, opener=html_opener)
        expect_value_error(lambda: store.fetch(GML, ["bad.jpg"]))
        digest = hashlib.sha256(GML.encode("utf-8")).hexdigest()[:20]
        assert not list((pathlib.Path(folder) / digest).rglob("*.part"))

    with tempfile.TemporaryDirectory() as folder:
        url = BASE + "udx/bldg/redirect.jpg"
        opener = FakeOpener({url: IMAGES[".jpg"]}, {url: BASE + "udx/other.jpg"})
        store = textures.TextureStore(folder, opener=opener)
        expect_value_error(lambda: store.fetch(GML, ["redirect.jpg"]))
        digest = hashlib.sha256(GML.encode("utf-8")).hexdigest()[:20]
        assert not list((pathlib.Path(folder) / digest).rglob("*.part"))

    with tempfile.TemporaryDirectory() as folder:
        bodies = {
            BASE + "udx/bldg/a.jpg": IMAGES[".jpg"],
            BASE + "udx/bldg/b.jpg": IMAGES[".jpg"],
            BASE + "udx/bldg/c.jpg": IMAGES[".jpg"],
        }
        opener = FakeOpener(bodies)
        store = textures.TextureStore(folder, max_images=2, opener=opener)
        store.fetch(GML, ["a.jpg"])
        store.fetch(GML, ["b.jpg"])
        expect_value_error(lambda: store.fetch(GML, ["c.jpg"]))
        assert len(opener.calls) == 2

    with tempfile.TemporaryDirectory() as folder:
        url = BASE + "udx/bldg/a.jpg"
        opener = FakeOpener({url: IMAGES[".jpg"]})
        store = textures.TextureStore(folder, max_total_mb=0, opener=opener)
        expect_value_error(lambda: store.fetch(GML, ["a.jpg"]))

    with tempfile.TemporaryDirectory() as folder:
        url = BASE + "udx/bldg/a.jpg"
        opener = FakeOpener({url: IMAGES[".jpg"]})
        store = textures.TextureStore(folder, max_file_mb=0, opener=opener)
        expect_value_error(lambda: store.fetch(GML, ["a.jpg"]))
        digest = hashlib.sha256(GML.encode("utf-8")).hexdigest()[:20]
        assert not list((pathlib.Path(folder) / digest).rglob("*.part"))
    print("rejects: HTML, redirect, max_images, max_total_mb, max_file_mb")
    print("failure cleanup: no .part residue")


if __name__ == "__main__":
    check_resolution()
    check_fetch()
    check_failures_and_limits()
    print("OK")
