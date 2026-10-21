"""Runs inside Blender (bpy). Invoked by external CPython pipeline via --python."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import bpy
from mathutils import Vector


def arguments():
    if '--' not in sys.argv:
        raise RuntimeError('-- manifest.json を指定してください')
    return Path(sys.argv[sys.argv.index('--') + 1])


def bbox_world(objects):
    coords = [o.matrix_world @ Vector(corner) for o in objects if o.type == 'MESH' for corner in o.bound_box]
    if not coords:
        raise ValueError('OBJからメッシュがインポートされませんでした')
    return [min(c[i] for c in coords) for i in range(3)] + [max(c[i] for c in coords) for i in range(3)]


def verify_bounds(actual, expected):
    # This catches accidentally using Blender default -Z forward/Y up.
    scale = max(1.0, *(abs(v) for v in expected))
    tolerance = max(0.02, scale * 0.00001)
    if any(abs(x-y) > tolerance for x,y in zip(actual, expected)):
        raise ValueError(f'OBJインポート後の座標範囲が不一致。軸設定を確認: actual={actual}, expected={expected}')


def import_obj(item, collection):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=item['path'], forward_axis='Y', up_axis='Z',
                          use_split_objects=False, use_split_groups=False, validate_meshes=True)
    objs = [o for o in bpy.data.objects if o not in before]
    verify_bounds(bbox_world(objs), item['expected_bbox_local'])
    for o in objs:
        o['PLATEAU_SOURCE_OBJ'] = item['path']
        for col in list(o.users_collection):
            col.objects.unlink(o)
        collection.objects.link(o)
    print(f'[IMPORTED] {item["path"]} : {len(objs)} objects', flush=True)
    return objs


def local_uv(x, y, wf, width, height):
    a,d,b,e,c,f = wf
    dx,dy = x-c,y-f
    det = a*e-b*d
    col = (e*dx-b*dy)/det
    row = (-d*dx+a*dy)/det
    return ((col + 0.5)/width, 1.0-(row + 0.5)/height)


def terrain_material(scene):
    mat = bpy.data.materials.new('PLATEAU | 国土地理院航空写真')
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get('Principled BSDF')
    if bsdf is None:
        raise RuntimeError('Principled BSDF がありません')
    bsdf.inputs['Roughness'].default_value = 1.0
    image = bpy.data.images.load(scene['aerial_png'], check_existing=True)
    image.colorspace_settings.name = 'sRGB'
    node = nodes.new('ShaderNodeTexImage')
    node.image = image
    node.interpolation = 'Linear'
    mat.node_tree.links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
    if scene['pack_image']:
        image.pack()
    return mat


def add_terrain_uv(obj, wf, wh, mat):
    if obj.type != 'MESH':
        return
    mesh = obj.data
    uv = mesh.uv_layers.get('Aerial UV') or mesh.uv_layers.new(name='Aerial UV')
    mesh.uv_layers.active = uv
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            co = obj.matrix_world @ mesh.vertices[mesh.loops[li].vertex_index].co
            uv.data[li].uv = local_uv(float(co.x), float(co.y), wf, wh[0], wh[1])
    mesh.materials.clear()
    mesh.materials.append(mat)
    # Flat-shaded grid triangles on diagonal slopes read as comb teeth; terrain is a smooth surface.
    mesh.polygons.foreach_set('use_smooth', [True] * len(mesh.polygons))


def main():
    scene = json.loads(arguments().read_text(encoding='utf-8'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    root = bpy.context.scene.collection
    buildings = bpy.data.collections.new('01 | 建物')
    terrain = bpy.data.collections.new('02 | 地形（航空写真）')
    root.children.link(buildings); root.children.link(terrain)
    for item in scene['buildings']:
        import_obj(item, buildings)
    mat = terrain_material(scene)
    for item in scene['terrain']:
        objs = import_obj(item, terrain)
        for obj in objs:
            add_terrain_uv(obj, scene['world_file_local'], scene['image_size'], mat)
    # Ensure the .blend remains portable even when the generated work cache is moved.
    if scene['pack_image']:
        for image in bpy.data.images:
            if image.source == 'FILE' and not image.packed_file:
                try:
                    image.pack()
                except (RuntimeError, OSError) as exc:
                    raise RuntimeError(f'必須テクスチャを内包できません: {image.filepath}') from exc
            if image.source == 'FILE' and not image.packed_file:
                raise RuntimeError(f'テクスチャが外部参照のままです: {image.filepath}')
    # Use the factory-startup renderer (EEVEE on typical Blender installs).
    bpy.context.scene.render.resolution_x = 1280
    bpy.context.scene.render.resolution_y = 720
    bpy.context.scene.render.resolution_percentage = 50
    bpy.context.scene.world.color = (0.12,0.12,0.12)
    bpy.context.scene.unit_settings.system = 'METRIC'
    # Provenance accompanies the portable .blend, not just the sidecar manifest.
    provenance = bpy.data.texts.new('PLATEAU_PROVENANCE.json')
    provenance.write(json.dumps(scene, ensure_ascii=False, indent=2))
    center = Vector((0,0,0))
    for area in (bpy.context.screen.areas if bpy.context.screen else []):
        if area.type == 'VIEW_3D':
            area.spaces.active.region_3d.view_location = center
            area.spaces.active.region_3d.view_distance = 1500.0
            area.spaces.active.clip_end = 100000.0
            area.spaces.active.shading.type = 'MATERIAL'
    out = str(Path(scene['target_blend']))
    bpy.ops.wm.save_as_mainfile(filepath=out, check_existing=False, compress=True)
    print('[SAVED] ' + out, flush=True)


if __name__ == '__main__':
    main()
