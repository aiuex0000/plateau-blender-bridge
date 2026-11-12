# SPDX-License-Identifier: MIT
"""地理院タイルから、地形メッシュと航空写真のテクスチャを Blender の中に作る。

画像の中身は Blender 自身の画像読込みで取り出す（Pillow の代わり）。
写真は画素を投影し直さず、タイルを1枚に並べ、地形の頂点ごとに UV を計算する。
"""

import array
import math

import bpy

from . import jprcs
from . import tiles as gsi

_T = gsi.TILE_PIXELS
DEM_ZOOM = 14
MAX_PHOTO_TILES = 64


class TilePixels:
    """保存済みタイルの画素を読む。行は上から、値は 0〜1 の RGBA。"""

    def __init__(self, cache):
        self.cache = cache
        self._buffers = {}

    def load(self, layer, z, x, y, keep=True):
        key = (layer, z, x, y)
        if key in self._buffers:
            return self._buffers[key]
        path = self.cache.get(layer, z, x, y)
        buffer = None if path is None else self._read(path, key)
        if keep:
            self._buffers[key] = buffer
        return buffer

    def _read(self, path, key):
        image = bpy.data.images.load(path, check_existing=False)
        try:
            if tuple(image.size) != (_T, _T):
                self.cache.discard(*key)
                raise ValueError("地理院タイルを 256x256 の画像として読めませんでした: %s" % (key,))
            if image.channels != 4 or image.is_float:
                raise ValueError("想定外の画像形式です（%d チャンネル）: %s" % (image.channels, key))
            buffer = array.array("f", bytes(_T * _T * 4 * 4))
            image.pixels.foreach_get(buffer)
        finally:
            bpy.data.images.remove(image)
        return buffer

    def rgb(self, layer, z, px, py):
        """全体の画素座標 (px, py) の RGB（0〜255）。タイルが無ければ None。"""
        tx, ty = px // _T, py // _T
        buffer = self.load(layer, z, tx, ty)
        if buffer is None:
            return None
        # Blender の画素は下の行から並ぶ
        i = ((_T - 1 - (py - ty * _T)) * _T + (px - tx * _T)) * 4
        return (
            int(buffer[i] * 255.0 + 0.5),
            int(buffer[i + 1] * 255.0 + 0.5),
            int(buffer[i + 2] * 255.0 + 0.5),
        )

    def height(self, lon, lat, z=DEM_ZOOM):
        """標高 m。画素の中心どうしを双一次補間する。どの標高タイルにも無ければ None。"""
        a, b = gsi.fractional_tile(lon, lat, z)
        gx, gy = a * _T - 0.5, b * _T - 0.5
        x0, y0 = math.floor(gx), math.floor(gy)
        fx, fy = gx - x0, gy - y0
        for layer in gsi.DEM_SOURCES:
            if layer == "dem_png" and z > 14:
                continue
            corners = []
            for dy in (0, 1):
                for dx in (0, 1):
                    rgb = self.rgb(layer, z, x0 + dx, y0 + dy)
                    corners.append(None if rgb is None else gsi.decode_dem(*rgb))
            h00, h10, h01, h11 = corners
            if None not in corners:
                return (h00 * (1 - fx) + h10 * fx) * (1 - fy) + (h01 * (1 - fx) + h11 * fx) * fy
            # 無効値の隣では、傾きを作らず一番近い有効な画素を使う
            nearest = (h11 if fx >= 0.5 else h01) if fy >= 0.5 else (h10 if fx >= 0.5 else h00)
            if nearest is not None:
                return nearest
        return None


def _tile_span(bounds, epsg, zoom):
    """範囲（平面直角座標）を覆うタイル座標（小数）の最小・最大。"""
    xmin, ymin, xmax, ymax = bounds
    points = []
    for x in (xmin, (xmin + xmax) / 2.0, xmax):
        for y in (ymin, (ymin + ymax) / 2.0, ymax):
            lon, lat = jprcs.to_lonlat(x, y, epsg)
            points.append(gsi.fractional_tile(lon, lat, zoom))
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def photo_zoom(bounds, epsg, wanted=18):
    """希望のズームから下げていき、64タイル以内に収まる最初のズームを返す。"""
    for zoom in range(wanted, 13, -1):
        fx0, fy0, fx1, fy1 = _tile_span(bounds, epsg, zoom)
        count = (math.floor(fx1) - math.floor(fx0) + 1) * (math.floor(fy1) - math.floor(fy0) + 1)
        if count <= MAX_PHOTO_TILES:
            return zoom
    raise ValueError("航空写真の範囲が大きすぎます（ズーム14で64タイル超）")


def photo_keys(bounds, epsg, wanted_zoom=18):
    """範囲を覆う写真タイルの (ズーム, タイルの並び)。"""
    zoom = photo_zoom(bounds, epsg, wanted_zoom)
    fx0, fy0, fx1, fy1 = _tile_span(bounds, epsg, zoom)
    return zoom, [
        (gsi.PHOTO, zoom, tx, ty)
        for ty in range(math.floor(fy0), math.floor(fy1) + 1)
        for tx in range(math.floor(fx0), math.floor(fx1) + 1)
    ]


def dem_keys(bounds, epsg):
    """標高の読取りが触りうる標高タイルの並び（全レイヤー）。先にまとめて取るために使う。"""
    fx0, fy0, fx1, fy1 = _tile_span(bounds, epsg, DEM_ZOOM)
    edge = 2.0 / _T  # 補間で隣の画素を読む分
    return [
        (layer, DEM_ZOOM, tx, ty)
        for layer in gsi.DEM_SOURCES
        for ty in range(math.floor(fy0 - edge), math.floor(fy1 + edge) + 1)
        for tx in range(math.floor(fx0 - edge), math.floor(fx1 + edge) + 1)
    ]


class Photo:
    """並べた航空写真と、緯度経度から UV を出すための情報。"""

    def __init__(self, image, zoom, left, top, width, height, tile_count):
        self.image = image
        self.zoom = zoom
        self.left = left  # 画像の左上の、全体の画素座標
        self.top = top
        self.width = width
        self.height = height
        self.tile_count = tile_count

    def uv(self, lon, lat):
        fx, fy = gsi.fractional_tile(lon, lat, self.zoom)
        return (fx * _T - self.left) / self.width, 1.0 - (fy * _T - self.top) / self.height


def build_photo(pixels, bounds, epsg, name, wanted_zoom=18):
    """範囲を覆う写真タイルを1枚の画像に並べ、.blend に埋め込む。"""
    zoom, keys = photo_keys(bounds, epsg, wanted_zoom)
    fx0, fy0, fx1, fy1 = _tile_span(bounds, epsg, zoom)
    tx0, ty0, tx1, ty1 = math.floor(fx0), math.floor(fy0), math.floor(fx1), math.floor(fy1)
    # 範囲の外は2画素の余白だけ残して切り落とす
    left = max(tx0 * _T, math.floor(fx0 * _T) - 2)
    top = max(ty0 * _T, math.floor(fy0 * _T) - 2)
    right = min((tx1 + 1) * _T, math.ceil(fx1 * _T) + 2)
    bottom = min((ty1 + 1) * _T, math.ceil(fy1 * _T) + 2)
    width, height = right - left, bottom - top

    pixels.cache.prefetch(keys)

    out = array.array("f", bytes(width * height * 4 * 4))
    for layer, z, tx, ty in keys:
        tile = pixels.load(layer, z, tx, ty, keep=False)
        if tile is None:
            raise ValueError("航空写真のタイルがありません: z%d/%d/%d" % (z, tx, ty))
        col0, col1 = max(tx * _T, left), min((tx + 1) * _T, right)
        row0, row1 = max(ty * _T, top), min((ty + 1) * _T, bottom)
        span = (col1 - col0) * 4
        for row in range(row0, row1):
            source = ((_T - 1 - (row - ty * _T)) * _T + (col0 - tx * _T)) * 4
            target = ((height - 1 - (row - top)) * width + (col0 - left)) * 4
            out[target:target + span] = tile[source:source + span]

    image = bpy.data.images.new(name, width, height, alpha=False)
    image.pixels.foreach_set(out)
    image.update()
    image.pack()
    return Photo(image, zoom, left, top, width, height, len(keys))


def build_terrain(pixels, bounds, epsg, origin, photo, name, resolution=100):
    """範囲を格子に切り、標高を載せた地形メッシュを作る。頂点は origin からの相対 m。"""
    xmin, ymin, xmax, ymax = bounds
    longest = max(xmax - xmin, ymax - ymin)
    nx = max(2, math.ceil(resolution * (xmax - xmin) / longest))
    ny = max(2, math.ceil(resolution * (ymax - ymin) / longest))

    vertices = []
    vertex_uvs = []
    missing = 0
    for j in range(ny + 1):
        y = ymin + (ymax - ymin) * j / ny
        for i in range(nx + 1):
            x = xmin + (xmax - xmin) * i / nx
            lon, lat = jprcs.to_lonlat(x, y, epsg)
            height = pixels.height(lon, lat)
            if height is None:
                missing += 1
                height = 0.0
            vertices.append((x - origin[0], y - origin[1], height))
            vertex_uvs.append(photo.uv(lon, lat))
    # 標高タイルは海が無効値。海として 0 m で通し、割合を結果に出す（2026-11-11 マスター判断）
    if missing == len(vertices):
        raise ValueError("範囲のどこでも標高を取れませんでした。場所を確かめてください")

    faces = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append((a, a + 1, a + nx + 2))
            faces.append((a, a + nx + 2, a + nx + 1))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    flat = []
    for face in faces:
        for index in face:
            flat.extend(vertex_uvs[index])
    uv_layer.data.foreach_set("uv", flat)
    mesh.polygons.foreach_set("use_smooth", [True] * len(faces))
    mesh.update()
    mesh.materials.append(_photo_material(photo.image, name))

    heights = [v[2] for v in vertices]
    return mesh, {
        "samples": (nx + 1, ny + 1),
        "min": min(heights),
        "max": max(heights),
        "sea_ratio": missing / len(vertices),
    }


def _photo_material(image, name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.extension = "EXTEND"
    texture.location = (-600, 0)
    shader.location = (-300, 0)
    shader.inputs["Roughness"].default_value = 1.0
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material
