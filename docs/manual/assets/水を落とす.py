# 水を落とす.py
#
# PLATEAU Bridge で作った街の上から、水の塊を落とします。
# 使い方: Blender の「Scripting」タブでこのファイルを開き、▶（スクリプト実行）を押す。
# そのあと、タイムラインの再生ボタン（またはスペースキー）で水が動きます。
#
# 何度実行しても、前の水を消してから作り直します。
# 防災の予測ではなく、物理演算で水の動きを体験するためのものです。

import bmesh
import bpy
from mathutils import Vector

# ---- ここの数字を変えると、水の様子が変わります ----
RESOLUTION = 64      # 水の細かさ。64 は軽い、96 はふつう、128 はきれい（時間がかかる）
SECONDS = 4          # 何秒ぶん計算するか
WATER_SIZE = 0.3     # 水の塊の大きさ（街の一辺に対する割合）
DROP_HEIGHT = 25.0   # 水の塊を、地面から何 m の高さに置くか
WATER_DEPTH = 15.0   # 水の塊の厚み（m）
SPEED_UP = 3.0       # 早回しの倍率。1 で実際の速さ
# ----------------------------------------------------

DOMAIN = "Water_Domain"
SOURCE = "Water_Source"
CITY = ("GSI_Terrain", "PLATEAU_Buildings")


def remove_old():
    for name in (DOMAIN, SOURCE):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)


def city_objects():
    found = [bpy.data.objects[n] for n in CITY if n in bpy.data.objects]
    if not found:
        raise RuntimeError("街が見つかりません。先に PLATEAU Bridge で街を作ってください。")
    return found


def make_obstacle(obj):
    """障害物（エフェクター）が付いていなければ付ける。"""
    if any(m.type == "FLUID" for m in obj.modifiers):
        return
    modifier = obj.modifiers.new("Fluid", "FLUID")
    modifier.fluid_type = "EFFECTOR"
    modifier.effector_settings.effector_type = "COLLISION"


def bounds(objects):
    xs, ys, zs = [], [], []
    for obj in objects:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            xs.append(world.x); ys.append(world.y); zs.append(world.z)
    return min(xs), max(xs), min(ys), max(ys), min(zs)


def add_cube(name, location, size):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh); bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.scale = size
    return obj


def water_material():
    mat = bpy.data.materials.get("Water") or bpy.data.materials.new("Water")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (0.03, 0.22, 0.45, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.08
    return mat


def main():
    scene = bpy.context.scene
    remove_old()
    city = city_objects()
    for obj in city:
        make_obstacle(obj)

    x0, x1, y0, y1, ground = bounds(city)
    width, depth = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    height = DROP_HEIGHT + WATER_DEPTH + 10.0

    # 水を計算する範囲（ドメイン）
    domain = add_cube(DOMAIN, (cx, cy, ground - 1.0 + height / 2), (width, depth, height))
    fluid = domain.modifiers.new("Fluid", "FLUID")
    fluid.fluid_type = "DOMAIN"
    settings = fluid.domain_settings
    settings.domain_type = "LIQUID"
    settings.resolution_max = RESOLUTION
    settings.use_mesh = True
    settings.time_scale = SPEED_UP
    settings.use_collision_border_bottom = True
    frames = int(SECONDS * scene.render.fps)
    settings.cache_frame_start = 1
    settings.cache_frame_end = frames
    settings.cache_type = "REPLAY"  # 再生するだけで計算される（最初の1回は少し遅い）
    domain.data.materials.append(water_material())
    domain.display_type = "WIRE"

    # 水の塊（フロー）
    side = min(width, depth) * WATER_SIZE
    source = add_cube(
        SOURCE, (cx, cy, ground + DROP_HEIGHT + WATER_DEPTH / 2), (side, side, WATER_DEPTH)
    )
    flow = source.modifiers.new("Fluid", "FLUID")
    flow.fluid_type = "FLOW"
    flow.flow_settings.flow_type = "LIQUID"
    flow.flow_settings.flow_behavior = "GEOMETRY"
    source.hide_render = True
    source.display_type = "WIRE"

    scene.frame_start, scene.frame_end = 1, frames
    scene.frame_set(1)
    print("水の準備ができました。再生ボタンを押してください（%d フレーム）" % frames)


main()
