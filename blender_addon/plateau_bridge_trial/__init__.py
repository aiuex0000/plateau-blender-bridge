# SPDX-License-Identifier: MIT
"""PLATEAU Bridge 試作（段0）。

ボタン1つで地理院タイル（全国最新写真）を1枚取り、実寸の板に貼る。
目的は、会社PC（スマートアプリコントロール有効）の Blender で
「アドオンが入るか」「通信できるか」「同梱の部品が読めるか」を確かめること。
Blender 同梱の bpy と標準ライブラリだけで動く。
"""

import importlib
import math
import os
import platform
import tempfile
import traceback
import urllib.request

import bpy

# 4.1 以前の Blender 用。4.2 以降は blender_manifest.toml が使われる。
bl_info = {
    "name": "PLATEAU Bridge 試作",
    "author": "PLATEAU Blender Bridge",
    "version": (0, 0, 1),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー (N) > PLATEAU",
    "description": "地理院タイルを1枚取り込み、板に貼る動作確認用の試作",
    "category": "Import-Export",
}

VERSION = "0.0.1"

# 東京駅付近。ズーム16のタイル1枚（一辺およそ500m）。
ZOOM = 16
LON = 139.7671
LAT = 35.6812
TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
ATTRIBUTION = "出典：国土地理院（地理院タイル）"
EARTH_CIRCUMFERENCE_M = 40075016.686

# この先の段で使う同梱部品。読めるかだけを確かめる。
PROBE_MODULES = ("ssl", "_elementtree", "_hashlib", "numpy")


def _tile_xy(lon, lat, zoom):
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def _tile_size_m(lat, zoom):
    return EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat)) / (2 ** zoom)


def _probe_modules():
    results = []
    for name in PROBE_MODULES:
        try:
            module = importlib.import_module(name)
            if name == "numpy":
                module.zeros(4).sum()
            results.append("%s=OK" % name)
        except Exception as error:  # 読めない理由をそのまま報告に残す
            results.append("%s=NG（%s: %s）" % (name, type(error).__name__, error))
    return results


def _fetch_tile(x, y):
    url = TILE_URL.format(z=ZOOM, x=x, y=y)
    request = urllib.request.Request(
        url, headers={"User-Agent": "PLATEAU-Blender-Bridge-Addon/" + VERSION}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2000001)
    if len(data) > 2000000:
        raise ValueError("タイルが2MBを超えています")
    return data


def _load_image(data, name):
    path = os.path.join(tempfile.gettempdir(), "plateau_bridge_trial_tile.jpg")
    with open(path, "wb") as file:
        file.write(data)
    try:
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name
        if image.size[0] == 0:
            bpy.data.images.remove(image)
            raise ValueError("受け取ったデータを画像として読めませんでした")
        image.pack()  # .blend に埋め込み、一時ファイルに頼らない
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return image


def _make_plane(context, image, size, name):
    half = size / 2.0
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(
        [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0), (-half, half, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(uv_layer.data, ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
        loop.uv = uv
    mesh.update()

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.location = (-600, 0)
    shader.location = (-300, 0)
    shader.inputs["Roughness"].default_value = 1.0
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    mesh.materials.append(material)

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj


def _show_in_viewports(context):
    """写真が見えるよう、3Dビューをマテリアル表示にして板全体を映す。"""
    window = context.window
    if window is None:  # バックグラウンド実行
        return
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces.active
        if space.shading.type in {"SOLID", "WIREFRAME"}:
            space.shading.type = "MATERIAL"
        space.clip_end = max(space.clip_end, 5000.0)
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            continue
        try:
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.view_selected()
        except Exception:
            traceback.print_exc()  # 視点合わせは失敗しても取り込み自体は成功


class PLATEAUTRIAL_OT_fetch_tile(bpy.types.Operator):
    bl_idname = "plateau_trial.fetch_tile"
    bl_label = "地理院タイルを1枚取り込む"
    bl_description = "東京駅付近の航空写真タイルを1枚取り、実寸の板に貼ります"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        lines = [
            "PLATEAU Bridge 試作 %s" % VERSION,
            "Blender %s / Python %s / %s %s"
            % (
                bpy.app.version_string,
                platform.python_version(),
                platform.system(),
                platform.version(),
            ),
            "部品: " + "、".join(_probe_modules()),
        ]
        succeeded = False
        try:
            if not getattr(bpy.app, "online_access", True):
                raise RuntimeError(
                    "オンラインアクセスが許可されていません。"
                    "プリファレンス > システム > ネットワーク で許可してください"
                )
            x, y = _tile_xy(LON, LAT, ZOOM)
            data = _fetch_tile(x, y)
            lines.append("通信: OK（%d バイト）" % len(data))
            name = "GSI_photo_%d_%d_%d" % (ZOOM, x, y)
            image = _load_image(data, name)
            size = _tile_size_m(LAT, ZOOM)
            obj = _make_plane(context, image, size, name)
            _show_in_viewports(context)
            lines.append(
                "板: OK（%s、一辺 %.1f m、画像 %d x %d）"
                % (obj.name, size, image.size[0], image.size[1])
            )
            succeeded = True
        except Exception as error:
            traceback.print_exc()
            lines.append("失敗: %s: %s" % (type(error).__name__, error))

        lines.append("結果: " + ("成功" if succeeded else "失敗"))
        context.window_manager.plateau_trial_result = "\n".join(lines)
        if succeeded:
            self.report({"INFO"}, "地理院タイルを取り込みました")
            return {"FINISHED"}
        self.report({"ERROR"}, lines[-2])
        return {"CANCELLED"}


class PLATEAUTRIAL_OT_copy_result(bpy.types.Operator):
    bl_idname = "plateau_trial.copy_result"
    bl_label = "結果をコピー"
    bl_description = "下に出ている結果をクリップボードへコピーします"

    def execute(self, context):
        context.window_manager.clipboard = context.window_manager.plateau_trial_result
        self.report({"INFO"}, "結果をコピーしました")
        return {"FINISHED"}


class PLATEAUTRIAL_PT_panel(bpy.types.Panel):
    bl_idname = "PLATEAUTRIAL_PT_panel"
    bl_label = "PLATEAU Bridge 試作"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PLATEAU"

    def draw(self, context):
        layout = self.layout
        layout.operator(PLATEAUTRIAL_OT_fetch_tile.bl_idname, icon="IMPORT")
        layout.label(text=ATTRIBUTION)
        result = context.window_manager.plateau_trial_result
        if result:
            box = layout.box()
            for line in result.split("\n"):
                box.label(text=line)
            layout.operator(PLATEAUTRIAL_OT_copy_result.bl_idname, icon="COPYDOWN")


_CLASSES = (
    PLATEAUTRIAL_OT_fetch_tile,
    PLATEAUTRIAL_OT_copy_result,
    PLATEAUTRIAL_PT_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.plateau_trial_result = bpy.props.StringProperty(
        name="試作の結果", options={"SKIP_SAVE"}
    )


def unregister():
    del bpy.types.WindowManager.plateau_trial_result
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
