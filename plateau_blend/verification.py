"""Pure-Python validation of Blender's independent save/reopen report.

No bpy dependency: the report can be unit-tested outside Blender. Blender itself
computes the measurements after reopening the staged .blend file.
"""
from __future__ import annotations
import math


def _union(items: list[dict]) -> list[float]:
    if not items:
        raise ValueError('検証対象のメッシュがありません')
    boxes=[i['expected_bbox_local'] for i in items]
    if any(len(b)!=6 or not all(math.isfinite(float(v)) for v in b) for b in boxes):
        raise ValueError('期待バウンディングボックスが不正')
    return [min(b[i] for b in boxes) if i<3 else max(b[i] for b in boxes) for i in range(6)]


def validate_report(report: dict, scene: dict) -> None:
    """Fail before publishing an incomplete or geometrically damaged .blend."""
    if not isinstance(report, dict) or report.get('schema')!=1:
        raise ValueError('Blender再読込の検証レポート形式が不正')
    if report.get('loaded_blend') != scene['target_blend']:
        raise ValueError('検証した.blendが保存対象と一致しません')
    if not report.get('provenance_present'):
        raise ValueError('保存済み.blendに出典・座標メタデータがありません')
    if report.get('crs') != scene['crs'] or report.get('origin_xyz') != scene['origin_xyz']:
        raise ValueError('保存済み.blendのCRS/原点が一致しません')
    for kind in ('buildings','terrain'):
        actual=report.get(kind+'_bbox')
        expected=_union(scene[kind])
        if not isinstance(actual,list) or len(actual)!=6 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in actual):
            raise ValueError(f'{kind}: 再読込後の座標範囲がありません')
        # Blender mesh coordinates are float32; tolerance scales with scene *extent*,
        # not absolute geographic offsets (which have already been rebased).
        extent=max(1.0,expected[3]-expected[0],expected[4]-expected[1],expected[5]-expected[2])
        tolerance=max(0.05,extent*2e-5)
        if any(abs(a-e)>tolerance for a,e in zip(actual,expected)):
            raise ValueError(f'{kind}: 再読込後の座標が不一致。actual={actual}, expected={expected}')
        if not isinstance(report.get(kind+'_mesh_count'),int) or report[kind+'_mesh_count']<1:
            raise ValueError(f'{kind}: 保存後にメッシュが見つかりません')
    if not isinstance(report.get('terrain_uv_mesh_count'),int) or report['terrain_uv_mesh_count']<1:
        raise ValueError('地形の航空写真UVがありません')
    images=report.get('file_images')
    packed=report.get('packed_file_images')
    if not isinstance(images,int) or images<1 or not isinstance(packed,int) or packed!=images:
        raise ValueError('画像が保存後に消失したか、.blend内に埋め込まれていません')

    # v0.9: Merely having embedded image datablocks and a UV layer is insufficient.
    # All terrain meshes must have a nondegenerate finite UV map *and* a packed
    # image texture connected to a material graph.
    terrain_mesh_count = report['terrain_mesh_count']
    if (type(report.get('terrain_textured_mesh_count')) is not int or
            report['terrain_textured_mesh_count'] != terrain_mesh_count):
        raise ValueError('地形マテリアルに画像テクスチャが接続されていません')
    if (type(report.get('terrain_bad_uv_mesh_count')) is not int or
            report['terrain_bad_uv_mesh_count'] != 0 or
            report['terrain_uv_mesh_count'] != terrain_mesh_count):
        raise ValueError('地形UVの非有限値・縮退・欠落が検出されました')
    # A textured building OBJ needs a linked diffuse texture on *that source's*
    # imported objects; counting unrelated packed images is not sufficient.
    expected = {str(item['path']) for item in scene['buildings'] if item.get('requires_diffuse_texture')}
    actual = report.get('building_textured_sources')
    if not isinstance(actual, list) or not all(isinstance(p, str) for p in actual):
        raise ValueError('建物テクスチャの再読込結果がありません')
    if not expected.issubset(set(actual)):
        raise ValueError('建物の拡散反射テクスチャがBlenderに反映されていません: '+str(sorted(expected-set(actual))))
