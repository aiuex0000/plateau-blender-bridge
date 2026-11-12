# 街をローポリにする.py
#
# PLATEAU Bridge で作った街を、軽いローポリ（積み木のような形）にします。
# 建物は1つずつ外側を包む単純な形にしてパステル色で塗り、地形は粗く間引いて単色にします。
# 性能の弱いPCで水を流す時や、「軽い版」の見本を作る時に使います。
#
# 使い方: Blender の「Scripting」タブでこのファイルを開き、▶（スクリプト実行）を押す。
# 元に戻せないので、先に .blend を別名で保存しておいてください（Ctrl+Z では戻せます）。
#
# 水の障害物（エフェクター）の設定は、そのまま残ります。

import colorsys
import random

import bmesh
import bpy

# ---- ここの数字を変えると、見た目が変わります ----
TERRAIN_RATIO = 0.03   # 地形をどこまで間引くか（0.03 = 3% の面数に）
COLOR_SATURATION = 0.45  # 建物の色の鮮やかさ（0 で白、1 で原色）
SEED = 7               # 色の組み合わせ。数字を変えると別の色になる
# ----------------------------------------------------


def building_islands(mesh):
    """つながった面のまとまり（だいたい建物1棟ぶん）ごとに、頂点の位置を集める。"""
    src = bmesh.new()
    src.from_mesh(mesh)
    src.faces.ensure_lookup_table()
    seen, islands = set(), []
    for face in src.faces:
        if face.index in seen:
            continue
        stack, verts = [face], set()
        seen.add(face.index)
        while stack:
            current = stack.pop()
            verts.update(v.co.copy().freeze() for v in current.verts)
            for edge in current.edges:
                for other in edge.link_faces:
                    if other.index not in seen:
                        seen.add(other.index)
                        stack.append(other)
        islands.append(verts)
    src.free()
    return islands


def lowpoly_buildings(obj):
    random.seed(SEED)
    out = bmesh.new()
    color = out.loops.layers.color.new("Col")
    for verts in building_islands(obj.data):
        if len(verts) < 4:
            continue
        tmp = bmesh.new()
        for co in verts:
            tmp.verts.new(co)
        result = bmesh.ops.convex_hull(tmp, input=tmp.verts)
        for item in result["geom_interior"] + result["geom_unused"]:
            if isinstance(item, bmesh.types.BMVert) and item.is_valid:
                tmp.verts.remove(item)
        bmesh.ops.dissolve_limit(tmp, angle_limit=0.09, verts=tmp.verts[:], edges=tmp.edges[:])
        rgb = colorsys.hsv_to_rgb(random.random(), COLOR_SATURATION, 0.92)
        vmap = {v: out.verts.new(v.co) for v in tmp.verts}
        for face in tmp.faces:
            try:
                new_face = out.faces.new([vmap[v] for v in face.verts])
            except ValueError:
                continue
            for loop in new_face.loops:
                loop[color] = (*rgb, 1.0)
        tmp.free()
    bmesh.ops.recalc_face_normals(out, faces=out.faces)
    mesh = bpy.data.meshes.new("LowPoly_Buildings")
    out.to_mesh(mesh)
    out.free()
    obj.data = mesh

    mat = bpy.data.materials.new("Pastel")
    mat.use_nodes = True
    tree = mat.node_tree
    bsdf = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.8
    vcol = tree.nodes.new("ShaderNodeVertexColor")
    vcol.layer_name = "Col"
    tree.links.new(vcol.outputs[0], bsdf.inputs["Base Color"])
    mesh.materials.append(mat)
    for poly in mesh.polygons:
        poly.use_smooth = False
    return len(mesh.polygons)


def lowpoly_terrain(obj):
    modifier = obj.modifiers.new("Decimate", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = TERRAIN_RATIO
    obj.modifiers.move(len(obj.modifiers) - 1, 0)  # 水の設定より前で間引く
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    mat = bpy.data.materials.new("Ground")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.52, 1.0)
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return len(obj.data.polygons)


def main():
    buildings = bpy.data.objects.get("PLATEAU_Buildings")
    terrain = bpy.data.objects.get("GSI_Terrain")
    if buildings is None and terrain is None:
        raise RuntimeError("街が見つかりません。先に PLATEAU Bridge で街を作ってください。")
    if buildings is not None:
        print("建物: %d 面にしました" % lowpoly_buildings(buildings))
    if terrain is not None:
        print("地形: %d 面にしました" % lowpoly_terrain(terrain))


if __name__ == "__main__":
    main()
