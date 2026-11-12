# SPDX-License-Identifier: MIT
"""CityGML から読んだ建物の面を、Blender のメッシュにする。"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from . import jprcs

_M_PER_DEG = 111320.0


def _tessellate(local):
    """輪の並び（点は m の x, y, z。最初の点の近くを原点にしてあること）を三角形に割る。

    返すのは、輪を順につないだ点の並びの中の番号3つの組。向きは外周に合わせる。
    """
    nx = ny = nz = 0.0
    outer = local[0]
    for i, a in enumerate(outer):  # Newell の方法で外周の法線
        b = outer[(i + 1) % len(outer)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    flat = [p for ring in local for p in ring]
    triangles = []
    for a, b, c in tessellate_polygon([[Vector(p[:3]) for p in ring] for ring in local]):
        pa, pb, pc = flat[a], flat[b], flat[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        facing = (uy * vz - uz * vy) * nx + (uz * vx - ux * vz) * ny + (ux * vy - uy * vx) * nz
        triangles.append((a, b, c) if facing >= 0.0 else (a, c, b))
    return triangles


def triangulate(rings):
    """穴のある面（[外周, 穴, ...]、点は 緯度・経度・標高）を三角形に割る。"""
    lat0, lon0, h0 = rings[0][0]
    scale = math.cos(math.radians(lat0)) * _M_PER_DEG
    # mathutils は32bit精度。面の最初の点からの相対 m にしてから渡す
    return _tessellate([
        [((p[1] - lon0) * scale, (p[0] - lat0) * _M_PER_DEG, p[2] - h0) for p in ring]
        for ring in rings
    ])


def _clip(mesh, rect, cap):
    """範囲 rect（x最小, y最小, x最大, y最大）の線で建物を切り、外側を捨てる。

    Blender の二等分（bisect）を4回掛ける。cap なら、切り口の閉じた輪郭に面を張って塞ぐ
    （建物の中が外と繋がらないように。流体シミュレーションなどで隙間へ流れ込まない）。
    返すのは (切り口に張った面の数, 残った面の数)。
    """
    xmin, ymin, xmax, ymax = rect
    planes = (
        ((xmin, 0.0, 0.0), (-1.0, 0.0, 0.0)),
        ((xmax, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ((0.0, ymin, 0.0), (0.0, -1.0, 0.0)),
        ((0.0, ymax, 0.0), (0.0, 1.0, 0.0)),
    )
    bm = bmesh.new()
    bm.from_mesh(mesh)
    caps = 0
    for point, normal in planes:
        cut = bmesh.ops.bisect_plane(
            bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=0.0005,
            plane_co=point, plane_no=normal, clear_outer=True,
        )
        if not cap:
            continue
        edges = [item for item in cut["geom_cut"] if isinstance(item, bmesh.types.BMEdge)]
        if not edges:
            continue
        outward = Vector(normal)
        filled = bmesh.ops.triangle_fill(bm, use_beauty=True, edges=edges, normal=normal)
        for face in filled["geom"]:
            if not isinstance(face, bmesh.types.BMFace):
                continue
            face.material_index = 0  # 切り口は灰色
            face.smooth = False
            face.normal_update()
            if face.normal.dot(outward) < 0.0:
                face.normal_flip()
            caps += 1
    remaining = len(bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    return caps, remaining

def build_mesh(data, epsg, origin, name, atlas=None, clip=None, cap=True):
    """citygml.Buildings → メッシュ。頂点は origin からの相対 m、Z は標高。

    atlas（atlas.Atlas）を渡すと、UV をアトラスの中の位置へ直してテクスチャを貼る。
    clip（平面直角座標の x最小, y最小, x最大, y最大）を渡すと、その範囲の線で建物を切る。
    cap なら切り口を塞ぐ。
    """
    coords = np.array(data.coords, dtype=np.float64).reshape(-1, 3)
    east, north = jprcs.to_xy_many(coords[:, 1], coords[:, 0], epsg)
    positions = np.column_stack([east - origin[0], north - origin[1], coords[:, 2]])
    sizes = np.array(data.face_sizes, dtype=np.int32)
    indices = np.array(data.face_indices, dtype=np.int32)
    face_images = np.array(data.face_images, dtype=np.int64)
    uvs = np.array(data.loop_uvs, dtype=np.float64).reshape(-1, 2)

    starts = np.zeros(len(sizes), dtype=np.int32)
    np.cumsum(sizes[:-1], out=starts[1:])
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(positions))
    mesh.vertices.foreach_set("co", positions.astype(np.float32).ravel())
    mesh.loops.add(len(indices))
    mesh.loops.foreach_set("vertex_index", indices)
    mesh.polygons.add(len(sizes))
    mesh.polygons.foreach_set("loop_start", starts)
    mesh.polygons.foreach_set("loop_total", sizes)
    # 直接組んだメッシュは既定でスムーズになる。建物は面ごとに平らに見せる
    mesh.polygons.foreach_set("use_smooth", np.zeros(len(sizes), dtype=bool))
    mesh.materials.append(_material())
    textured = 0
    if atlas is not None and atlas.images:
        textured = _apply_atlas(mesh, atlas, sizes, face_images, uvs)
    mesh.update(calc_edges=True)
    repaired = mesh.validate()  # つぶれた面などを取り除く
    caps = 0
    if clip is not None:
        rect = (clip[0] - origin[0], clip[1] - origin[1], clip[2] - origin[0], clip[3] - origin[1])
        caps, remaining = _clip(mesh, rect, cap)
        if not remaining:
            bpy.data.meshes.remove(mesh)
            raise ValueError("指定した範囲の中に建物の面がありません")
        mesh.update()
    heights = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", heights)
    return mesh, {
        "faces": len(mesh.polygons),
        "vertices": len(mesh.vertices),
        "repaired": bool(repaired),
        "textured": textured,
        "caps": caps,
        "min": float(heights[2::3].min()),
        "max": float(heights[2::3].max()),
    }


def _apply_atlas(mesh, atlas, sizes, face_images, uvs):
    """面ごとの画像の番号と UV を、アトラスのページと位置へ直して入れる。貼れた面の数を返す。"""
    # 画像の番号 -> ページ、UV の原点と大きさ。貼れない画像はページ -1
    table = np.full((len(atlas.rects) + 1, 5), -1.0, dtype=np.float64)
    for number, rect in enumerate(atlas.rects):
        if rect is not None:
            table[number] = rect
    face_rects = table[face_images]  # -1 は最後の行（貼らない）
    loop_rects = np.repeat(face_rects, sizes, axis=0)
    uvs = np.clip(uvs, 0.0, 1.0) * loop_rects[:, 3:5] + loop_rects[:, 1:3]
    uvs[loop_rects[:, 0] < 0] = 0.0

    layer = mesh.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", uvs.astype(np.float32).ravel())
    for page, image in enumerate(atlas.images):
        mesh.materials.append(_atlas_material(image, page))
    slots = (face_rects[:, 0] + 1).astype(np.int32)  # 0 は灰色、1 以降がアトラス
    mesh.polygons.foreach_set("material_index", slots)
    return int((slots > 0).sum())


def _atlas_material(image, page):
    material = bpy.data.materials.new("PLATEAU_Texture_%d" % page)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.location = (-600, 0)
    shader.location = (-300, 0)
    shader.inputs["Roughness"].default_value = 0.9
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def _material():
    name = "PLATEAU_Building"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        material.diffuse_color = (0.8, 0.8, 0.8, 1.0)  # ソリッド表示の色
        material.use_nodes = True
        shader = next(
            (n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None
        )
        if shader is not None:
            shader.inputs["Roughness"].default_value = 0.9
            shader.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    return material
