"""Pure logic: validate an independent Blender-readback report, without bpy."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from plateau_blend.verification import validate_report
from plateau_blend.smoke import make_smoke_inputs, run_smoke
from plateau_blend.pipeline import validate, _publish_verified_blend


def _scene():
    return {'target_blend':'C:/work/stage.blend','crs':'EPSG:6674',
            'origin_xyz':[100,200,0],
            'buildings':[{'expected_bbox_local':[0,1,5,20,25,40]}],
            'terrain':[{'expected_bbox_local':[-10,-10,0,50,50,2]}]}


def _report():
    s=_scene()
    return {'schema':1,'loaded_blend':s['target_blend'],'provenance_present':True,
            'crs':s['crs'],'origin_xyz':s['origin_xyz'],
            'buildings_bbox':s['buildings'][0]['expected_bbox_local'].copy(),
            'terrain_bbox':s['terrain'][0]['expected_bbox_local'].copy(),
            'buildings_mesh_count':2,'terrain_mesh_count':1,
            'terrain_uv_mesh_count':1,'file_images':2,'packed_file_images':2,
            'terrain_textured_mesh_count':1,'terrain_bad_uv_mesh_count':0,'building_textured_sources':[]}


class VerifyTests(unittest.TestCase):
    def test_good_scene(self):
        validate_report(_report(),_scene())

    def test_wrong_scene_or_bad_crs_rejected(self):
        for field,value in [('loaded_blend','other.blend'),('crs','EPSG:4326'),('provenance_present',False)]:
            with self.subTest(field=field):
                r=_report();r[field]=value
                with self.assertRaises(ValueError):validate_report(r,_scene())

    def test_missing_uv_or_unpackaged_texture_rejected(self):
        for field,value in [('terrain_uv_mesh_count',0),('packed_file_images',1),('file_images',0)]:
            with self.subTest(field=field):
                r=_report();r[field]=value
                with self.assertRaises(ValueError):validate_report(r,_scene())

    def test_geometry_drift_rejected(self):
        r=_report();r['terrain_bbox'][0]-=10
        with self.assertRaisesRegex(ValueError,'座標'):validate_report(r,_scene())
        r=_report();r['buildings_bbox'][2]+=float('nan')
        with self.assertRaises(ValueError):validate_report(r,_scene())

    def test_exclusive_publish_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);stage=root/'stage.blend';dest=root/'scene.blend'
            stage.write_bytes(b'BLENDER-new');dest.write_bytes(b'BLENDER-old')
            with self.assertRaises(FileExistsError):_publish_verified_blend(stage,dest,overwrite=False)
            self.assertEqual(dest.read_bytes(),b'BLENDER-old')
            self.assertTrue(stage.exists())
            _publish_verified_blend(stage,dest,overwrite=True)
            self.assertEqual(dest.read_bytes(),b'BLENDER-new')

    def test_exclusive_publish_preserves_staged_on_link_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);stage=root/'stage.blend';dest=root/'scene.blend'
            stage.write_bytes(b'BLENDER-new')
            with patch('plateau_blend.pipeline.os.link',side_effect=OSError('unsupported')):
                with self.assertRaises(OSError):_publish_verified_blend(stage,dest,overwrite=False)
            self.assertTrue(stage.is_file())
            self.assertFalse(dest.exists())

    def test_smoke_input_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); exe=d/'blender.exe';exe.write_bytes(b'placeholder')
            cfg=make_smoke_inputs(d/'smoke',exe)
            info=validate(cfg)
            self.assertEqual(info['coverage'],1)
            self.assertGreater(info['building_bbox'][3],500000)
            self.assertTrue((d/'smoke'/'facade.png').is_file())
            with self.assertRaises(FileExistsError):make_smoke_inputs(d/'smoke',exe)

    def test_smoke_keeps_folder_and_invokes_production_writer(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);exe=root/'blender.exe';exe.write_bytes(b'x')
            called=[]
            def stub(cfg,*,log,cancel):
                called.append(cfg)
                return cfg.output_blend
            with patch('plateau_blend.smoke.run',side_effect=stub):
                out=run_smoke(blender=exe,output_parent=root/'diagnostics',log=lambda *_:None)
            self.assertEqual(out,called[0].output_blend)
            self.assertTrue(out.parent.joinpath('aerial.png').is_file())

if __name__=='__main__':unittest.main()
