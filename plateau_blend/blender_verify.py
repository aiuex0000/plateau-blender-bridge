"""Run within Blender to independently reopen/inspect the staged .blend.

Usage: blender --background --factory-startup stage.blend --python-exit-code 12
       --python blender_verify.py -- report.json
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path
import bpy
from mathutils import Vector


def _bbox(objects):
    vertices=[obj.matrix_world @ Vector(p) for obj in objects for p in obj.bound_box]
    if not vertices:
        raise ValueError('保存後のメッシュが空です')
    return [min(p[i] for p in vertices) for i in range(3)]+[max(p[i] for p in vertices) for i in range(3)]


def _linked_packed_image(mesh_obj, require_base_color=False):
    for material in mesh_obj.data.materials:
        if not material or not material.use_nodes or not material.node_tree:
            continue
        tree=material.node_tree
        for link in tree.links:
            node=link.from_node
            if (node.type=='TEX_IMAGE' and node.image is not None and
                    node.image.source=='FILE' and node.image.packed_file and
                    (not require_base_color or
                     (link.to_node.type=='BSDF_PRINCIPLED' and link.to_socket.name=='Base Color'))):
                return True
    return False


def _invalid_uv(mesh_obj):
    mesh=mesh_obj.data
    if not mesh.polygons or not mesh.uv_layers:
        return True
    layer=mesh.uv_layers.active or mesh.uv_layers[0]
    if len(layer.data)!=len(mesh.loops) or len(layer.data)==0:
        return True
    lo_u=lo_v=float('inf')
    hi_u=hi_v=float('-inf')
    for entry in layer.data:
        u,v=entry.uv
        if not math.isfinite(u) or not math.isfinite(v):
            return True
        lo_u=min(lo_u,u);lo_v=min(lo_v,v)
        hi_u=max(hi_u,u);hi_v=max(hi_v,v)
    return (hi_u-lo_u)<1e-8 or (hi_v-lo_v)<1e-8


def main():
    if '--' not in sys.argv:
        raise ValueError('検証結果の保存先が必要')
    report_path=Path(sys.argv[sys.argv.index('--')+1])
    metadata=bpy.data.texts.get('PLATEAU_PROVENANCE.json')
    provenance=json.loads(metadata.as_string()) if metadata else None
    buildings=bpy.data.collections.get('01 | 建物')
    terrain=bpy.data.collections.get('02 | 地形（航空写真）')
    if buildings is None or terrain is None:
        raise ValueError('必要なBlenderコレクションがありません')
    b=[o for o in buildings.all_objects if o.type=='MESH']
    t=[o for o in terrain.all_objects if o.type=='MESH']
    images=[im for im in bpy.data.images if im.source=='FILE']
    # A UV map must actually have loops; an empty layer is not usable.
    uv_count=sum(1 for o in t if o.data.polygons and
                 any(len(layer.data)>0 for layer in o.data.uv_layers))
    textured_building_sources=sorted({str(obj.get('PLATEAU_SOURCE_OBJ')) for obj in b
                                      if _linked_packed_image(obj) and obj.get('PLATEAU_SOURCE_OBJ')})
    report={
        'schema':1,'loaded_blend':str(Path(bpy.data.filepath)),
        'provenance_present':isinstance(provenance,dict),
        'crs': provenance.get('crs') if provenance else None,
        'origin_xyz': provenance.get('origin_xyz') if provenance else None,
        'buildings_bbox':_bbox(b), 'terrain_bbox':_bbox(t),
        'buildings_mesh_count':len(b),'terrain_mesh_count':len(t),
        'terrain_uv_mesh_count':uv_count,
        'terrain_textured_mesh_count':sum(_linked_packed_image(obj, require_base_color=True) for obj in t),
        'terrain_bad_uv_mesh_count':sum(_invalid_uv(obj) for obj in t),
        'building_textured_sources':textured_building_sources,
        'file_images':len(images),
        'packed_file_images':sum(1 for im in images if im.packed_file),
    }
    part=report_path.with_name(report_path.name+'.part')
    part.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    part.replace(report_path)
    print('[VERIFY] Blender reopen report: '+str(report_path),flush=True)


if __name__=='__main__':
    main()
