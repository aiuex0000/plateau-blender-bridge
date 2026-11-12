# SPDX-License-Identifier: MIT
"""PLATEAU の CityGML（建物）を読み、範囲に掛かる建物の面を取り出す。bpy に依存しない。

nusamai（CityGML → OBJ）の代わり。ファイル全体を木にせず、建物1棟ごとに読んで捨てる。
座標は PLATEAU の並び（緯度、経度、標高 m）のまま返す。平面への変換は呼ぶ側で行う。
"""

import xml.etree.ElementTree as ET


class Buildings:
    """読み取った建物の面。頂点は建物ごとに重複をまとめてある。"""

    def __init__(self):
        self.coords = []  # 緯度、経度、標高 を頂点の数だけ並べたもの
        self.face_sizes = []  # 面ごとの頂点数
        self.face_indices = []  # 面の頂点番号を、面の順に並べたもの
        self.building_ids = []  # 取り込んだ建物の gml:id
        self.members_total = 0
        self.lod_counts = {}  # 使った LOD -> 棟数
        self.holes_triangulated = 0
        self.holes_dropped = 0
        self.srs_names = set()
        # テクスチャ。画像は (CityGML の出どころ, imageURI) の組で持つ
        self.images = []
        self.face_images = []  # 面ごとの images の番号。貼る画像が無い面は -1
        self.loop_uvs = []  # face_indices と同じ順で、u, v を並べたもの
        self._image_index = {}

    @property
    def vertex_count(self):
        return len(self.coords) // 3


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _lod_of(local):
    """lod2MultiSurface のような要素名から LOD の数字を返す。違えば None。"""
    if len(local) > 4 and local.startswith("lod") and local[3].isdigit():
        return int(local[3])
    return None


def _ring(element):
    """exterior / interior の中の輪 → ((緯度, 経度, 標高) の並び, 輪の gml:id)。閉じる点は外す。"""
    texts = []
    ring_id = None
    for node in element.iter():
        local = _local(node.tag)
        if local in ("posList", "pos") and node.text:
            texts.append(node.text)
        elif local == "LinearRing":
            for key, value in node.attrib.items():
                if _local(key) == "id":
                    ring_id = value
    values = [float(v) for v in " ".join(texts).split()]
    if len(values) % 3:
        raise ValueError("座標が3次元（緯度 経度 標高）ではありません")
    points = [tuple(values[i:i + 3]) for i in range(0, len(values), 3)]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points, ring_id


def _polygon(element):
    """gml:Polygon → ([外周, 穴, 穴, ...], 輪ごとの gml:id)。外周が面にならなければ None。"""
    outer = None
    holes = []
    for child in element:
        local = _local(child.tag)
        if local == "exterior":
            outer = _ring(child)
        elif local == "interior":
            ring = _ring(child)
            if len(ring[0]) >= 3:
                holes.append(ring)
    if outer is None or len(outer[0]) < 3:
        return None
    both = [outer] + holes
    return [ring[0] for ring in both], [ring[1] for ring in both]


def _texture_targets(element, ring_uvs):
    """app:ParameterizedTexture → 輪の gml:id ごとに (imageURI, UV の文字列) を覚える。"""
    reference = None
    for child in element:
        if _local(child.tag) == "imageURI" and child.text:
            reference = child.text.strip()
    if not reference:
        return
    for node in element.iter():
        if _local(node.tag) == "textureCoordinates" and node.text and node.get("ring"):
            ring_uvs[node.get("ring").lstrip("#")] = (reference, node.text)


def read_buildings(path, bbox, max_lod=2, triangulate=None, out=None, source=None):
    """範囲 bbox（西, 南, 東, 北 の経度緯度）に掛かる建物の面を読む。

    建物ごとに、max_lod 以下で一番高い LOD を使う。範囲に少しでも掛かる建物は丸ごと残す。
    triangulate は、穴のある面を三角形に割る関数（輪の並び → 頂点番号3つの組の並び）。
    渡さなければ、穴は埋めて外周だけを面にする。
    out に前のファイルの結果を渡すと続きに足す。同じ gml:id の建物は二度入れない
    （区の境のメッシュは、両方の区のデータに同じ建物が入っているため）。
    source（この CityGML の URL など）を渡すと、テクスチャの画像と UV も読む。
    """
    if out is None:
        out = Buildings()
    lod_stack = []
    polygons = {}  # LOD -> その LOD の面の並び（今読んでいる建物の分）
    building_id = None
    root = None
    seen = set(out.building_ids)
    ring_uvs = {}  # 輪の gml:id -> (imageURI, UV の文字列)
    pending = []  # (面の番号, 最初のループの番号, ループごとの (輪の gml:id, 輪の中の順番))

    for event, element in ET.iterparse(path, events=("start", "end")):
        local = _local(element.tag)
        if event == "start":
            if root is None:
                root = element
                if local != "CityModel":
                    raise ValueError("CityGML のルート要素が違います: %s" % element.tag)
            lod = _lod_of(local)
            if lod is not None:
                lod_stack.append(lod)
            elif local == "cityObjectMember":
                polygons = {}
                building_id = None
            elif building_id is None and local in ("Building", "BuildingPart"):
                for key, value in element.attrib.items():
                    if _local(key) == "id":
                        building_id = value
            continue

        if local == "Polygon":
            if lod_stack:
                polygon = _polygon(element)
                if polygon:
                    polygons.setdefault(lod_stack[-1], []).append(polygon)
            element.clear()
        elif local == "ParameterizedTexture":
            if source is not None:
                _texture_targets(element, ring_uvs)
            element.clear()
        elif _lod_of(local) is not None:
            lod_stack.pop()
        elif local == "Envelope":
            if element.get("srsName"):
                out.srs_names.add(element.get("srsName"))
        elif local == "cityObjectMember":
            out.members_total += 1
            usable = [lod for lod in polygons if 1 <= lod <= max_lod]
            if usable and not (building_id and building_id in seen):
                lod = max(usable)
                if _add_building(out, building_id, lod, polygons[lod], bbox, triangulate, pending):
                    seen.add(building_id)
            polygons = {}
            element.clear()
            if root is not None:
                root.clear()
    _apply_textures(out, source, ring_uvs, pending)
    return out


def _apply_textures(out, source, ring_uvs, pending):
    """読み終えたファイルの分について、面に画像の番号を、ループに UV を入れる。"""
    parsed = {}  # 輪の gml:id -> (imageURI, [u, v, u, v, ...])
    for face, loop_start, corners in pending:
        uvs = []
        reference = None
        for ring_id, order in corners:
            entry = parsed.get(ring_id)
            if entry is None and ring_id in ring_uvs:
                image, text = ring_uvs[ring_id]
                entry = parsed[ring_id] = (image, [float(v) for v in text.split()])
            if entry is None or 2 * order + 1 >= len(entry[1]):
                uvs = None
                break
            if reference is None:
                reference = entry[0]
            uvs.extend(entry[1][2 * order:2 * order + 2])
        if not uvs:
            continue
        key = (source, reference)
        number = out._image_index.get(key)
        if number is None:
            number = out._image_index[key] = len(out.images)
            out.images.append(key)
        out.face_images[face] = number
        out.loop_uvs[2 * loop_start:2 * loop_start + len(uvs)] = uvs


def _add_building(out, building_id, lod, faces, bbox, triangulate, pending):
    west, south, east, north = bbox
    lats = [p[0] for rings, _ids in faces for p in rings[0]]
    lons = [p[1] for rings, _ids in faces for p in rings[0]]
    if min(lons) > east or max(lons) < west or min(lats) > north or max(lats) < south:
        return False

    index_of = {}
    base = out.vertex_count
    coords = out.coords

    def index(point):
        found = index_of.get(point)
        if found is None:
            found = index_of[point] = base + len(index_of)
            coords.extend(point)
        return found

    def add_face(loop, corners):
        pending.append((len(out.face_sizes), len(out.face_indices), corners))
        out.face_sizes.append(len(loop))
        out.face_indices.extend(loop)
        out.face_images.append(-1)
        out.loop_uvs.extend([0.0] * (2 * len(loop)))

    for rings, ring_ids in faces:
        if len(rings) > 1 and triangulate is not None:
            flat = [index(point) for ring in rings for point in ring]
            where = [(ring_ids[r], k) for r, ring in enumerate(rings) for k in range(len(ring))]
            for a, b, c in triangulate(rings):
                add_face((flat[a], flat[b], flat[c]), (where[a], where[b], where[c]))
            out.holes_triangulated += 1
            continue
        if len(rings) > 1:
            out.holes_dropped += 1
        loop = [index(point) for point in rings[0]]
        add_face(loop, [(ring_ids[0], k) for k in range(len(loop))])
    out.building_ids.append(building_id or "")
    out.lod_counts[lod] = out.lod_counts.get(lod, 0) + 1
    return True
