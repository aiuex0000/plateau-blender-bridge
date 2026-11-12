# SPDX-License-Identifier: MIT
"""PLATEAU Bridge（Blender アドオン版）。

Blender 同梱の bpy・numpy と標準ライブラリだけで動く。サイドバーで場所と広さを選ぶと、
国土地理院の地形・航空写真と、PLATEAU の建物（形とテクスチャ）を取り込む。
"""

import os
import shutil
import tempfile
import time
import traceback
import unicodedata

import bpy

from . import atlas
from . import buildings
from . import catalog
from . import job as jobs
from . import jprcs
from . import place as places
from . import terrain

# 4.1 以前の Blender 用。4.2 以降は blender_manifest.toml が使われる。
bl_info = {
    "name": "PLATEAU Bridge",
    "author": "PLATEAU Blender Bridge",
    "version": (0, 2, 2),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー (N) > PLATEAU",
    "description": "PLATEAU の建物と国土地理院の地形・航空写真を取り込む",
    "category": "Import-Export",
}

VERSION = "0.2.2"
MIN_SIDE_M = 50.0
MAX_SIDE_M = 10000.0
CACHE_NAMES = ("gsi_cache", "citygml", "textures", "atlas")
OFFLINE_MESSAGE = (
    "オンラインアクセスが許可されていません。"
    "プリファレンス > システム > ネットワーク で許可してください"
)
CANCELLED_MESSAGE = "中止しました。もう一度「3Dの街を作る」で作り直せます（取得済みのデータは残っています）"

# シーンに残す基準。後から取り込む物を同じ位置へ置くために使う。
KEY_EPSG = "plateau_bridge_epsg"
KEY_ORIGIN = "plateau_bridge_origin_xy"

_active = {"job": None}  # 今走っている取り込み。中止ボタンが触る


def _cache_dir(name):
    try:
        return bpy.utils.extension_path_user(__package__, path=name, create=True)
    except Exception:  # 4.1 以前、または旧形式のアドオンとして入れた時
        return os.path.join(tempfile.gettempdir(), "plateau_bridge_" + name)


def _require_online():
    if not getattr(bpy.app, "online_access", True):
        raise RuntimeError(OFFLINE_MESSAGE)


def _resolve_range(scene, settings):
    """入力欄とシーンの基準から、範囲を決める。平面直角座標 m と、それを包む経度緯度。"""
    spot = places.parse_place(settings.place)
    if KEY_EPSG in scene and KEY_ORIGIN in scene:
        epsg = int(scene[KEY_EPSG])
        origin = tuple(scene[KEY_ORIGIN])
    else:
        epsg = jprcs.zone_for(spot.lon, spot.lat)
        origin = None

    if spot.rect:
        west, south, east, north = spot.rect
        corners = [
            jprcs.to_xy(lon, lat, epsg)
            for lon in (west, (west + east) / 2.0, east)
            for lat in (south, (south + north) / 2.0, north)
        ]
        bounds = (
            min(p[0] for p in corners), min(p[1] for p in corners),
            max(p[0] for p in corners), max(p[1] for p in corners),
        )
    else:
        if spot.size is not None:
            width = height = spot.size
        elif settings.size == "CUSTOM":
            width, height = settings.width, settings.height
        else:
            width = height = float(settings.size)
        cx, cy = jprcs.to_xy(spot.lon, spot.lat, epsg)
        bounds = (cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0)

    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if not (MIN_SIDE_M <= width <= MAX_SIDE_M and MIN_SIDE_M <= height <= MAX_SIDE_M):
        raise ValueError(
            "広さは一辺 %d〜%d m にしてください（今は 東西 %.0f m、南北 %.0f m）"
            % (MIN_SIDE_M, MAX_SIDE_M, width, height)
        )
    if origin is None:
        origin = ((bounds[0] + bounds[2]) / 2.0, (bounds[1] + bounds[3]) / 2.0)

    xmin, ymin, xmax, ymax = bounds
    points = [
        jprcs.to_lonlat(x, y, epsg)
        for x in (xmin, (xmin + xmax) / 2.0, xmax)
        for y in (ymin, (ymin + ymax) / 2.0, ymax)
    ]
    bbox = (
        min(p[0] for p in points), min(p[1] for p in points),
        max(p[0] for p in points), max(p[1] for p in points),
    )
    if spot.rect:
        # 範囲やメッシュで指定された時、建物は指定そのものの範囲で探す。平面へ写して
        # 包み直すと数 m 広がり、隣のメッシュのデータまで取りに行ってしまう
        bbox = spot.rect
    return {"epsg": epsg, "origin": origin, "bounds": bounds, "bbox": bbox, "note": spot.note}


def _select_only(context, objects):
    """今回作った物だけを選ぶ。視点合わせが全体に合うようにするため。"""
    for other in context.selected_objects:
        other.select_set(False)
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = objects[-1]


def _show_in_viewports(context):
    """写真が見えるよう、3Dビューをマテリアル表示にして全体を映す。"""
    window = context.window
    if window is None:  # バックグラウンド実行
        return
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces.active
        space.clip_end = max(space.clip_end, 50000.0)
        if space.shading.type in {"SOLID", "WIREFRAME"}:
            try:
                space.shading.type = "MATERIAL"
            except TypeError:
                pass  # Workbench など、マテリアル表示の無いレンダーエンジン
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            continue
        try:
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.view_selected()
        except Exception:
            traceback.print_exc()  # 視点合わせは失敗しても取り込み自体は成功


def _redraw(context):
    window = context.window
    if window is None:
        return
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _wrapped(layout, context, text):
    """文をパネルの幅に合わせて折り返して置く。Blender のラベルは収まらないと途中が省かれる。"""
    scale = context.preferences.system.ui_scale
    # 半角1文字をおよそ 7 画素と見る。全角は2文字分。左右の余白とタブの分を引く
    room = max(20, int((context.region.width - 60.0 * scale) / (7.0 * scale)))
    for line in text.split("\n"):
        row, used = "", 0
        for char in line:
            width = 2 if unicodedata.east_asian_width(char) in "WFA" else 1
            if row and used + width > room:
                layout.label(text=row)
                row, used = "", 0
            row += char
            used += width
        if row:
            layout.label(text=row)


def _preset_items():
    items = [("NONE", "有名な場所から選ぶ", "")]
    for number, (name, _coords, _size) in enumerate(places.PRESETS):
        items.append(("P%d" % number, name, ""))
    return items


def _preset_changed(self, _context):
    if self.preset == "NONE":
        return
    _name, coords, size = places.PRESETS[int(self.preset[1:])]
    self.place = coords
    self.size = str(size)


class PlateauBridgeSettings(bpy.types.PropertyGroup):
    preset: bpy.props.EnumProperty(
        name="有名な場所", description="選ぶと、場所と広さの欄に入ります",
        items=_preset_items(), update=_preset_changed,
    )
    # Blender の数値欄は32bit精度で、経度が1m強ずれる。場所は文字で持つ。
    place: bpy.props.StringProperty(
        name="場所",
        description="中心の 緯度, 経度（, 一辺m）。Googleマップで場所を右クリックし、"
        "出てきた数字をクリックするとコピーできます。地図の URL、4つの数字の範囲、"
        "メッシュコードも使えます",
        default="35.6586, 139.7454",
    )
    size: bpy.props.EnumProperty(
        name="広さ", description="範囲の一辺の長さ",
        items=(
            ("200", "200 m", "駅前や交差点ひとつ分"),
            ("400", "400 m", "街区いくつか分"),
            ("800", "800 m", "駅の周り一帯"),
            ("CUSTOM", "自由", "東西と南北の長さを自分で決めます"),
        ),
        default="400",
    )
    width: bpy.props.FloatProperty(
        name="東西 (m)", default=1000.0, min=MIN_SIDE_M, max=MAX_SIDE_M,
    )
    height: bpy.props.FloatProperty(
        name="南北 (m)", default=1000.0, min=MIN_SIDE_M, max=MAX_SIDE_M,
    )
    show_details: bpy.props.BoolProperty(name="今回の設定をすべて見る", default=False)
    want_terrain: bpy.props.BoolProperty(
        name="地形と航空写真", description="国土地理院の地形と航空写真を取り込みます",
        default=True,
    )
    want_buildings: bpy.props.BoolProperty(
        name="建物", description="PLATEAU の建物を取り込みます", default=True,
    )
    max_lod: bpy.props.IntProperty(
        name="建物の詳しさ（LOD の上限）",
        description="建物ごとに、この数字以下で一番詳しい形を使います。1 は箱、2 は屋根の形つき",
        default=2, min=1, max=3,
    )
    clip_buildings: bpy.props.BoolProperty(
        name="範囲の端で建物を切る",
        description="範囲の線で建物を切り、地形の四角にぴったり収めます。"
        "外すと、範囲に掛かる建物を丸ごと残します（地形からはみ出します）",
        default=True,
    )
    cap_cuts: bpy.props.BoolProperty(
        name="切り口を塞ぐ",
        description="切った建物の断面に面を張ります。建物の中が外と繋がらないので、"
        "流体シミュレーションなどで隙間へ流れ込みません",
        default=True,
    )
    as_obstacle: bpy.props.BoolProperty(
        name="水の障害物にする",
        description="地形と建物に、流体シミュレーションの障害物（エフェクター：コリジョン）を"
        "最初から設定します。水を流した時に、水が建物にぶつかって回り込みます",
        default=True,
    )
    use_textures: bpy.props.BoolProperty(
        name="建物にテクスチャを貼る",
        description="建物の写真テクスチャも取り込みます。画像の取得に時間が掛かります",
        default=True,
    )
    texture_pages: bpy.props.IntProperty(
        name="テクスチャの枚数の上限",
        description="建物のテクスチャを 4096x4096 の画像何枚にまとめるか。"
        "多いほど細かく、重くなります。収まるように全体を同じ倍率で縮めます",
        default=4, min=1, max=16,
    )
    photo_zoom: bpy.props.IntProperty(
        name="航空写真の細かさ",
        description="ズームの数字。範囲が広い時は、64タイルに収まるズームまで自動で下げます",
        default=18, min=14, max=18,
    )
    terrain_resolution: bpy.props.IntProperty(
        name="地形の細かさ", description="長い辺を何分割するか",
        default=100, min=24, max=256,
    )


def _snapshot(settings):
    """走っている間に欄を変えられても困らないよう、始める時の設定を写し取る。"""
    return {
        name: getattr(settings, name)
        for name in ("want_terrain", "want_buildings", "max_lod", "use_textures",
                     "texture_pages", "photo_zoom", "terrain_resolution",
                     "clip_buildings", "cap_cuts", "as_obstacle")
    }


def _make_obstacle(obj):
    """流体の障害物（エフェクター：コリジョン）にする。"""
    modifier = obj.modifiers.new("Fluid", "FLUID")
    modifier.fluid_type = "EFFECTOR"
    modifier.effector_settings.effector_type = "COLLISION"


def _assemble(context, job, target, options):
    """裏で集めた物を、Blender のデータに組み立てる。結果の行を返す。"""
    started = time.perf_counter()
    scene = context.scene
    epsg, origin, bounds = target["epsg"], target["origin"], target["bounds"]
    lines = ["座標系: EPSG:%d / 原点 東 %.3f m、北 %.3f m" % (epsg, origin[0], origin[1])]
    created = []
    if target["note"]:
        lines.append(target["note"])

    if options["want_terrain"]:
        pixels = terrain.TilePixels(job.tile_cache)
        photo = terrain.build_photo(
            pixels, bounds, epsg, "GSI_Photo", wanted_zoom=options["photo_zoom"]
        )
        try:
            mesh, info = terrain.build_terrain(
                pixels, bounds, epsg, origin, photo, "GSI_Terrain",
                resolution=options["terrain_resolution"],
            )
        except Exception:
            bpy.data.images.remove(photo.image)
            raise
        created.append(bpy.data.objects.new("GSI_Terrain", mesh))
        context.collection.objects.link(created[-1])
        lines.append(
            "写真: ズーム %d、%d タイル、%d x %d 画素"
            % (photo.zoom, photo.tile_count, photo.width, photo.height)
        )
        lines.append(
            "地形: %d x %d 点、標高 %.1f〜%.1f m"
            % (info["samples"][0], info["samples"][1], info["min"], info["max"])
        )
        if info["sea_ratio"]:
            lines.append(
                "海など標高の無い地点 %.1f%% は 0 m にしました" % (100.0 * info["sea_ratio"])
            )

    if options["want_buildings"]:
        data = job.data
        packed = None
        if job.texture_paths:
            packed = atlas.build(
                job.texture_paths, "PLATEAU_Atlas", _cache_dir("atlas"),
                max_pages=options["texture_pages"],
            )
        mesh, info = buildings.build_mesh(
            data, epsg, origin, "PLATEAU_Buildings", atlas=packed,
            clip=bounds if options["clip_buildings"] else None, cap=options["cap_cuts"],
        )
        created.append(bpy.data.objects.new("PLATEAU_Buildings", mesh))
        context.collection.objects.link(created[-1])
        years = "・".join(str(year) for year in sorted({s.year for s in job.sources}))
        cities = "・".join(sorted({s.city for s in job.sources}))
        lods = "、".join("LOD%d %d 棟" % item for item in sorted(data.lod_counts.items()))
        lines.append("データ: %s（%s 年度）、CityGML %d ファイル" % (cities, years, len(job.sources)))
        lines.append(
            "建物: %d 棟（%s）、%d 面、標高 %.1f〜%.1f m"
            % (len(data.building_ids), lods, info["faces"], info["min"], info["max"])
        )
        if options["clip_buildings"]:
            lines.append(
                "範囲の端で建物を切り、切り口を塞ぎました" if options["cap_cuts"]
                else "範囲の端で建物を切りました（切り口は開いたまま）"
            )
        if packed is not None:
            line = "テクスチャ: 画像 %d 枚を %d 枚にまとめ、大きさは元の %d%%" % (
                packed.used, len(packed.images), round(100.0 * packed.scale)
            )
            if packed.failed:
                line += "（読めなかった画像 %d 枚）" % packed.failed
            lines.append(line)

    if options["as_obstacle"] and created:
        for obj in created:
            _make_obstacle(obj)
        lines.append("地形と建物を、水の障害物（エフェクター）にしました")

    scene[KEY_EPSG] = epsg
    scene[KEY_ORIGIN] = origin
    _select_only(context, created)
    _show_in_viewports(context)
    lines.append(
        "時間: 取得 %.0f 秒（新しく %.1f MiB）、組み立て %.0f 秒"
        % (job.fetch_seconds, job.downloaded_bytes / 1048576.0, time.perf_counter() - started)
    )
    return lines


class PLATEAUBRIDGE_OT_generate(bpy.types.Operator):
    bl_idname = "plateau_bridge.generate"
    bl_label = "3Dの街を作る"
    bl_description = "場所と広さに合わせて、地形・航空写真・建物を取り込みます"
    bl_options = {"REGISTER", "UNDO"}

    def _fail(self, context, message):
        context.window_manager.plateau_bridge_result = "%s\n結果: 失敗" % message
        self.report({"ERROR"}, message)
        return {"CANCELLED"}

    def _begin(self, context):
        settings = context.scene.plateau_bridge
        if not (settings.want_terrain or settings.want_buildings):
            raise ValueError("「地形と航空写真」か「建物」の少なくとも片方を選んでください")
        _require_online()
        self._target = _resolve_range(context.scene, settings)
        self._options = _snapshot(settings)
        return jobs.Job(
            epsg=self._target["epsg"], bounds=self._target["bounds"],
            bbox=self._target["bbox"], cache_dir=_cache_dir, **{
                key: self._options[key]
                for key in ("want_terrain", "want_buildings", "photo_zoom",
                            "max_lod", "use_textures")
            },
        )

    def _end(self, context, job):
        """裏の仕事が終わった後。組み立てて、結果をパネルへ残す。"""
        manager = context.window_manager
        manager.plateau_bridge_running = False
        _active["job"] = None
        if job.cancelled:
            manager.plateau_bridge_result = CANCELLED_MESSAGE
            self.report({"INFO"}, "中止しました")
            return {"CANCELLED"}
        if job.error is not None:
            return self._fail(context, "失敗: %s" % job.error)
        try:
            lines = _assemble(context, job, self._target, self._options)
        except Exception as error:
            traceback.print_exc()
            return self._fail(context, "失敗: %s: %s" % (type(error).__name__, error))
        header = "街ができました！（PLATEAU Bridge %s / Blender %s）" % (
            VERSION, bpy.app.version_string
        )
        manager.plateau_bridge_result = "\n".join([header] + lines + ["結果: 成功"])
        self.report({"INFO"}, "街ができました！")
        return {"FINISHED"}

    def execute(self, context):
        """画面なしで呼んだ時。終わるまで待つ。"""
        try:
            job = self._begin(context)
        except Exception as error:
            return self._fail(context, "失敗: %s" % error)
        job.run()
        return self._end(context, job)

    def invoke(self, context, _event):
        """ボタンから。裏で進め、画面は止めない。"""
        if _active["job"] is not None:
            self.report({"WARNING"}, "取り込みの途中です")
            return {"CANCELLED"}
        try:
            job = self._begin(context)
        except Exception as error:
            return self._fail(context, "失敗: %s" % error)
        self._job = job
        _active["job"] = job
        manager = context.window_manager
        manager.plateau_bridge_running = True
        manager.plateau_bridge_result = ""
        job.start()
        self._timer = manager.event_timer_add(0.25, window=context.window)
        manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        job = self._job
        manager = context.window_manager
        manager.plateau_bridge_stage = job.stage
        manager.plateau_bridge_progress = job.fraction
        _redraw(context)
        if not job.done:
            return {"PASS_THROUGH"}
        manager.event_timer_remove(self._timer)
        result = self._end(context, job)
        _redraw(context)
        return result


class PLATEAUBRIDGE_OT_cancel(bpy.types.Operator):
    bl_idname = "plateau_bridge.cancel"
    bl_label = "中止する"
    bl_description = "取り込みを中止します。取得済みのデータは残ります"

    def execute(self, context):
        job = _active["job"]
        if job is not None:
            job.cancel()
            context.window_manager.plateau_bridge_stage = "中止しています"
        return {"FINISHED"}


class PLATEAUBRIDGE_OT_check_place(bpy.types.Operator):
    bl_idname = "plateau_bridge.check_place"
    bl_label = "この場所を確かめる"
    bl_description = "この場所に PLATEAU の建物データがあるかと、その量を確かめます"

    def execute(self, context):
        manager = context.window_manager
        try:
            _require_online()
            target = _resolve_range(context.scene, context.scene.plateau_bridge)
            sources = catalog.select_sources(catalog.fetch_catalog(target["bbox"]))
        except Exception as error:
            manager.plateau_bridge_check = str(error)
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        cities = "・".join(sorted({s.city for s in sources}))
        years = "・".join(str(year) for year in sorted({s.year for s in sources}))
        known = sum(s.size or 0 for s in sources) / 1048576.0
        amount = "約%.0fMB" % known if known >= 1.0 else "容量は取得するまで分かりません"
        manager.plateau_bridge_check = (
            "%s（%s年度）のデータがあります\nCityGML %d件・%s"
            % (cities, years, len(sources), amount)
        )
        return {"FINISHED"}


class PLATEAUBRIDGE_OT_clear_origin(bpy.types.Operator):
    bl_idname = "plateau_bridge.clear_origin"
    bl_label = "基準を解除"
    bl_description = "このシーンの座標系と原点の記録を消します。次に取り込む範囲の中心が新しい原点になります"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        for key in (KEY_EPSG, KEY_ORIGIN):
            if key in context.scene:
                del context.scene[key]
        return {"FINISHED"}


class PLATEAUBRIDGE_OT_copy_result(bpy.types.Operator):
    bl_idname = "plateau_bridge.copy_result"
    bl_label = "結果をコピー"
    bl_description = "下に出ている結果をクリップボードへコピーします"

    def execute(self, context):
        context.window_manager.clipboard = context.window_manager.plateau_bridge_result
        self.report({"INFO"}, "結果をコピーしました")
        return {"FINISHED"}


class PLATEAUBRIDGE_OT_clear_cache(bpy.types.Operator):
    bl_idname = "plateau_bridge.clear_cache"
    bl_label = "取得済みのデータを消す"
    bl_description = (
        "このアドオンが保存した CityGML・画像・地図タイルを消します。"
        "取り込み済みの街は消えません。次に同じ場所を作る時は、取り直しになります"
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if _active["job"] is not None:
            self.report({"WARNING"}, "取り込みの途中は消せません")
            return {"CANCELLED"}
        freed = 0
        for name in CACHE_NAMES:
            folder = _cache_dir(name)
            # 自分の保存フォルダだけを消す。名前が合わない場所には触らない
            if not folder or not os.path.basename(os.path.normpath(folder)).endswith(name):
                continue
            if not os.path.isdir(folder):
                continue
            for root, _dirs, files in os.walk(folder):
                freed += sum(os.path.getsize(os.path.join(root, file)) for file in files)
            shutil.rmtree(folder)
        self.report({"INFO"}, "取得済みのデータを消しました（%.0f MiB）" % (freed / 1048576.0))
        return {"FINISHED"}


class PLATEAUBRIDGE_PT_panel(bpy.types.Panel):
    bl_idname = "PLATEAUBRIDGE_PT_panel"
    bl_label = "PLATEAU Bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PLATEAU"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.plateau_bridge
        manager = context.window_manager
        running = manager.plateau_bridge_running

        column = layout.column(align=True)
        column.enabled = not running
        column.label(text="どこの街を作りますか？")
        column.prop(settings, "preset", text="")
        column.label(text="中心の 緯度, 経度（, 一辺m）")
        column.prop(settings, "place", text="")
        column.operator("wm.url_open", text="Googleマップで探す", icon="URL").url = (
            "https://www.google.com/maps"
        )

        column = layout.column(align=True)
        column.enabled = not running
        column.label(text="どのくらいの広さにしますか？")
        column.row(align=True).prop(settings, "size", expand=True)
        if settings.size == "CUSTOM":
            column.prop(settings, "width")
            column.prop(settings, "height")

        column = layout.column(align=True)
        column.enabled = not running
        column.operator(PLATEAUBRIDGE_OT_check_place.bl_idname, icon="VIEWZOOM")
        _wrapped(column, context, manager.plateau_bridge_check)

        box = layout.box()
        box.enabled = not running
        box.prop(
            settings, "show_details", emboss=False,
            icon="TRIA_DOWN" if settings.show_details else "TRIA_RIGHT",
        )
        if settings.show_details:
            column = box.column(align=True)
            column.prop(settings, "want_terrain")
            column.prop(settings, "want_buildings")
            column = box.column(align=True)
            column.prop(settings, "max_lod")
            column.prop(settings, "clip_buildings")
            row = column.row()
            row.enabled = settings.clip_buildings
            row.prop(settings, "cap_cuts")
            column.prop(settings, "as_obstacle")
            column.prop(settings, "use_textures")
            row = column.row()
            row.enabled = settings.use_textures
            row.prop(settings, "texture_pages")
            column.prop(settings, "photo_zoom")
            column.prop(settings, "terrain_resolution")
            if KEY_EPSG in scene and KEY_ORIGIN in scene:
                box.label(text="このシーンの座標系: EPSG:%d" % scene[KEY_EPSG])
                box.operator(PLATEAUBRIDGE_OT_clear_origin.bl_idname, icon="X")
            box.operator(PLATEAUBRIDGE_OT_clear_cache.bl_idname, icon="TRASH")

        if running:
            column = layout.column(align=True)
            if hasattr(column, "progress"):
                column.progress(
                    factor=manager.plateau_bridge_progress,
                    text="%d%%" % round(100.0 * manager.plateau_bridge_progress),
                )
            _wrapped(column, context, manager.plateau_bridge_stage)
            column.operator(PLATEAUBRIDGE_OT_cancel.bl_idname, icon="CANCEL")
        else:
            row = layout.row()
            row.scale_y = 1.6
            row.operator(PLATEAUBRIDGE_OT_generate.bl_idname, icon="IMPORT")

        column = layout.column(align=True)
        column.label(text="出典：国土地理院（地理院タイル）")
        column.label(text=catalog.ATTRIBUTION)

        result = manager.plateau_bridge_result
        if result:
            _wrapped(layout.box().column(align=True), context, result)
            layout.operator(PLATEAUBRIDGE_OT_copy_result.bl_idname, icon="COPYDOWN")


_CLASSES = (
    PlateauBridgeSettings,
    PLATEAUBRIDGE_OT_generate,
    PLATEAUBRIDGE_OT_cancel,
    PLATEAUBRIDGE_OT_check_place,
    PLATEAUBRIDGE_OT_clear_origin,
    PLATEAUBRIDGE_OT_copy_result,
    PLATEAUBRIDGE_OT_clear_cache,
    PLATEAUBRIDGE_PT_panel,
)
_MANAGER_PROPS = {
    "plateau_bridge_result": bpy.props.StringProperty(options={"SKIP_SAVE"}),
    "plateau_bridge_check": bpy.props.StringProperty(options={"SKIP_SAVE"}),
    "plateau_bridge_stage": bpy.props.StringProperty(options={"SKIP_SAVE"}),
    "plateau_bridge_progress": bpy.props.FloatProperty(options={"SKIP_SAVE"}, min=0.0, max=1.0),
    "plateau_bridge_running": bpy.props.BoolProperty(options={"SKIP_SAVE"}),
}


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.plateau_bridge = bpy.props.PointerProperty(type=PlateauBridgeSettings)
    for name, prop in _MANAGER_PROPS.items():
        setattr(bpy.types.WindowManager, name, prop)


def unregister():
    job = _active["job"]
    if job is not None:
        job.cancel()
        _active["job"] = None
    for name in _MANAGER_PROPS:
        delattr(bpy.types.WindowManager, name)
    del bpy.types.Scene.plateau_bridge
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
