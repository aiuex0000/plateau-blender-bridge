# SPDX-License-Identifier: MIT
"""取り込みのうち、時間の掛かる通信と CityGML の読込みを裏のスレッドで進める。

裏のスレッドは Blender のデータ（bpy.data）に触らない。進み具合を言葉と割合で持ち、
表（Blender の画面）がタイマーで読む。画像の読込みとメッシュ化は、終わった後に表で行う。
"""

import os
import threading
import time
import traceback

from . import buildings
from . import catalog
from . import citygml
from . import terrain
from . import textures
from . import tiles

_BATCH = 200  # 画像を何枚ずつ取るか。区切りごとに進み具合を出し、取り消しを見る


class Cancelled(Exception):
    """利用者が取り消した。"""


class Job:
    def __init__(self, *, epsg, bounds, bbox, cache_dir, want_terrain, want_buildings,
                 photo_zoom, max_lod, use_textures):
        self.epsg = epsg
        self.bounds = bounds  # 平面直角座標（m）
        self.bbox = bbox  # 経度緯度（西, 南, 東, 北）
        self.cache_dir = cache_dir  # 名前 -> 保存フォルダ を返す関数
        self.want_terrain = want_terrain
        self.want_buildings = want_buildings
        self.photo_zoom = photo_zoom
        self.max_lod = max_lod
        self.use_textures = use_textures

        # 進み具合。表が読む
        self.stage = "準備しています"
        self.fraction = 0.0
        self.done = False
        self.cancelled = False
        self.error = None

        # 結果。done になってから表が読む
        self.tile_cache = None
        self.sources = []
        self.data = None
        self.texture_paths = None  # data.images と同じ並びの、画像ファイルの場所
        self.fetch_seconds = 0.0
        self.downloaded_bytes = 0

        self._cancel = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.run, name="plateau-bridge", daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def _say(self, stage, fraction):
        if self._cancel.is_set():
            raise Cancelled()
        self.stage = stage
        self.fraction = fraction

    def run(self):
        try:
            # 割合の配分: 地形 0〜0.1、建物 0.1〜0.95、残りは表での組み立て
            if self.want_terrain:
                self._fetch_tiles(0.0, 0.1 if self.want_buildings else 0.9)
            if self.want_buildings:
                self._fetch_buildings(0.1 if self.want_terrain else 0.0, 0.95)
            self._say("Blender に組み立てています", 0.95)
        except Cancelled:
            self.cancelled = True
        except Exception as error:  # 理由は表でパネルに出す
            traceback.print_exc()
            self.error = error
        finally:
            self.done = True

    def _fetch_tiles(self, start, end):
        tick = time.perf_counter()
        self.tile_cache = tiles.TileCache(self.cache_dir("gsi_cache"), max_tiles=400)
        keys = terrain.photo_keys(self.bounds, self.epsg, self.photo_zoom)[1]
        keys += terrain.dem_keys(self.bounds, self.epsg)
        for i in range(0, len(keys), 16):
            self._say(
                "地図の写真と標高を取得しています（%d/%d）" % (i, len(keys)),
                start + (end - start) * i / len(keys),
            )
            self.tile_cache.prefetch(keys[i:i + 16])
        self.downloaded_bytes += self.tile_cache.downloaded_bytes
        self.fetch_seconds += time.perf_counter() - tick

    def _fetch_buildings(self, start, end):
        span = end - start
        tick = time.perf_counter()
        self._say("PLATEAU のデータを探しています", start)
        self.sources = catalog.select_sources(catalog.fetch_catalog(self.bbox))
        count = len(self.sources)
        self.fetch_seconds += time.perf_counter() - tick

        # 取得と読込みで建物の区間の 6 割、画像で 4 割（画像を取らない時は全部）
        share = 0.6 if self.use_textures else 1.0
        self.data = citygml.Buildings()
        for number, source in enumerate(self.sources):
            base = start + span * share * number / count
            step = span * share / count

            def progress(done, total, number=number, base=base, step=step):
                size = " / %.0f MiB" % (total / 1048576.0) if total else ""
                self._say(
                    "建物のデータを取得しています（%d/%d、%.0f MiB%s）"
                    % (number + 1, count, done / 1048576.0, size),
                    base + step * 0.7 * (done / total if total else 0.0),
                )

            tick = time.perf_counter()
            path, fresh = catalog.download(
                source, self.cache_dir("citygml"), progress=progress
            )
            self.fetch_seconds += time.perf_counter() - tick
            if fresh:
                self.downloaded_bytes += os.path.getsize(path)
            self._say("建物を読んでいます（%d/%d）" % (number + 1, count), base + step * 0.7)
            citygml.read_buildings(
                path, self.bbox, max_lod=self.max_lod, triangulate=buildings.triangulate,
                out=self.data, source=source.url if self.use_textures else None,
            )
        if not self.data.building_ids:
            raise ValueError("指定した範囲に建物がありません。場所と範囲を確かめてください")
        unexpected = sorted(s for s in self.data.srs_names if not s.endswith("/6697"))
        if unexpected:
            raise ValueError("想定外の座標系のデータです: %s" % ", ".join(unexpected))
        if self.use_textures and self.data.images:
            self._fetch_textures(start + span * share, end)

    def _fetch_textures(self, start, end):
        tick = time.perf_counter()
        store = textures.TextureStore(self.cache_dir("textures"))
        images = self.data.images
        paths = {}
        for i in range(0, len(images), _BATCH):
            self._say(
                "建物の写真を取得しています（%d/%d 枚）" % (i, len(images)),
                start + (end - start) * i / len(images),
            )
            by_source = {}
            for gml_url, reference in images[i:i + _BATCH]:
                by_source.setdefault(gml_url, []).append(reference)
            for gml_url, references in by_source.items():
                for reference, path in store.fetch(gml_url, references).items():
                    paths[(gml_url, reference)] = path
        self.texture_paths = [paths.get(key) for key in images]
        self.downloaded_bytes += store.downloaded_bytes
        self.fetch_seconds += time.perf_counter() - tick
