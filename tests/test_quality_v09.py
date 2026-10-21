"""v0.9 scene-quality regression tests without Blender/HTTP."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from plateau_blend.gsi import make_terrain
from plateau_blend.obj import has_used_diffuse_texture
from plateau_blend.verification import validate_report


class QualityTests(unittest.TestCase):
    def _scene(self):
        return {'target_blend':'C:/scene.work/staged.blend', 'crs':'EPSG:6674',
                'origin_xyz':[100.0,200.0,0.0],
                'buildings':[{'path':'C:/model/painted.obj','expected_bbox_local':[0,0,0,10,10,20],
                              'requires_diffuse_texture':True},
                             {'path':'C:/model/plain.obj','expected_bbox_local':[11,0,0,21,10,20],
                              'requires_diffuse_texture':False}],
                'terrain':[{'expected_bbox_local':[-2,-2,0,25,20,2]}]}

    def _report(self,scene):
        return {'schema':1, 'loaded_blend':scene['target_blend'], 'provenance_present':True,
                'crs':scene['crs'], 'origin_xyz':scene['origin_xyz'],
                'buildings_bbox':[0,0,0,21,10,20], 'terrain_bbox':[-2,-2,0,25,20,2],
                'buildings_mesh_count':2, 'terrain_mesh_count':1, 'terrain_uv_mesh_count':1,
                'terrain_textured_mesh_count':1, 'terrain_bad_uv_mesh_count':0,
                'file_images':2,'packed_file_images':2,
                'building_textured_sources':['C:/model/painted.obj']}

    def test_textured_building_and_valid_terrain(self):
        scene=self._scene();validate_report(self._report(scene),scene)

    def test_building_texture_disconnected_rejected(self):
        scene=self._scene();report=self._report(scene)
        report['building_textured_sources']=['C:/model/plain.obj']
        with self.assertRaisesRegex(ValueError,'建物の拡散反射'):
            validate_report(report,scene)

    def test_terrain_texture_disconnected_rejected(self):
        scene=self._scene();report=self._report(scene)
        report['terrain_textured_mesh_count']=0
        with self.assertRaisesRegex(ValueError,'地形マテリアル'):
            validate_report(report,scene)

    def test_invalid_or_degenerate_uv_rejected(self):
        scene=self._scene();report=self._report(scene)
        report['terrain_bad_uv_mesh_count']=1
        with self.assertRaisesRegex(ValueError,'地形UV'):
            validate_report(report,scene)

    def test_absent_new_report_fields_rejected(self):
        scene=self._scene();report=self._report(scene)
        del report['terrain_textured_mesh_count']
        with self.assertRaises(ValueError):validate_report(report,scene)

    def test_unused_mtl_diffuse_map_not_required(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td); obj=folder/'s.obj';mtl=folder/'s.mtl'
            (folder/'texture.png').write_bytes(b'fake')
            obj.write_text('mtllib s.mtl\nv 0 0 0\nusemtl plain\nf 1 1 1\n',encoding='utf-8')
            mtl.write_text('newmtl textured\nmap_Kd texture.png\nnewmtl plain\nKd 0.5 0.5 0.5\n',encoding='utf-8')
            self.assertFalse(has_used_diffuse_texture(obj))
            obj.write_text(obj.read_text(encoding='utf-8').replace('usemtl plain','usemtl textured'),encoding='utf-8')
            self.assertTrue(has_used_diffuse_texture(obj))

    def test_missing_used_diffuse_texture_rejected_early(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td);obj=folder/'s.obj';mtl=folder/'s.mtl'
            obj.write_text('mtllib s.mtl\nusemtl painted\nf 1 1 1\n',encoding='utf-8')
            mtl.write_text('newmtl painted\nmap_Kd missing.png\n',encoding='utf-8')
            with self.assertRaises(FileNotFoundError):has_used_diffuse_texture(obj)

    def test_single_missing_dem_sample_is_not_replaced_with_zero(self):
        class FakeTiles:
            cancel=None
            def __init__(self):self.count=0
            def height(self, lon,lat,zoom):
                self.count+=1
                return None if self.count==50 else 10.0
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/'terrain.obj'
            # A 24x24 terrain has 625 samples. One missing point is 99.84%
            # coverage, previously allowed despite leaving a fabricated 0m vertex.
            with self.assertRaisesRegex(ValueError,'欠落頂点'):
                make_terrain(FakeTiles(),(0,0,100,100),'EPSG:6674',out,resolution=24,log=lambda *_:None)
            self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
