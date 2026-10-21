"""Tests for source-safe projected XY geometry clipping."""
import json
import tempfile
import unittest
from pathlib import Path
from plateau_blend.clip import Corner, clip_polygon, crop_obj, cached_clip
from plateau_blend.obj import inspect_obj
from plateau_blend.auto import projected_selection
from pyproj import Transformer


class ClipTests(unittest.TestCase):
    def test_clips_with_uv_normals_preserves_material(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); src=root/'city.obj'; result=root/'clip'/'city.obj'
            (root/'city.mtl').write_text('newmtl roof\nKd 1 0 0\n')
            src.write_text('''mtllib city.mtl
            o Tower
            usemtl roof
            v -10 -10 5
            v 10 -10 5
            v 10 10 5
            v -10 10 5
            vt 0 0
            vt 1 0
            vt 1 1
            vt 0 1
            vn 0 0 1
            f 1/1/1 2/2/1 3/3/1 4/4/1
            '''.replace('            ',''))
            previous=src.read_bytes()
            data=crop_obj(src,result,(-2,-3,2,3),'xyz')
            self.assertEqual(src.read_bytes(),previous)
            self.assertEqual(data['faces_output'],1)
            parsed=inspect_obj(result)
            self.assertEqual(parsed.bbox[:2],(-2.,-3.))
            self.assertEqual(parsed.bbox[3:5],(2.,3.))
            content=result.read_text()
            self.assertIn('usemtl roof',content)
            self.assertIn('vn 0 0 1',content)
            self.assertIn('vt 0.4 0.35',content)
            self.assertTrue(list(result.parent.glob('material_*.mtl')))
            self.assertIn('f ',content)

    def test_clip_swapped_axes_and_no_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'a.obj'
            src.write_text('v 10 20 1\nv 10 30 2\nv 20 30 2\nf 1 2 3\n')
            out=root/'out.obj'
            stats=crop_obj(src,out,(19,9,31,21),'yxz')
            self.assertEqual(stats['faces_output'],1)
            self.assertEqual(inspect_obj(out).bbox[:2],(20.,10.))
            other=root/'other.obj'
            stats=crop_obj(src,other,(40,40,50,50),'yxz')
            self.assertEqual(stats['vertices_output'],0)
            self.assertFalse(other.exists())

    def test_cached_clip_and_source_edit_invalidation(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'sample.obj'
            src.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            first,stat=cached_clip(src,root/'cache',(-1,-1,2,2),'xyz',lambda *_:None)
            second,stat2=cached_clip(src,root/'cache',(-1,-1,2,2),'xyz',lambda *_:None)
            self.assertEqual(first,second)
            src.write_text('v 0 0 0\nv 2 0 0\nv 0 2 0\nf 1 2 3\n')
            third,_=cached_clip(src,root/'cache',(-1,-1,2,2),'xyz',lambda *_:None)
            self.assertNotEqual(first,third)

    def test_projected_selection_always_xy(self):
        region='r:135.429,34.819,135.431,34.821'
        bounds=projected_selection(region,'EPSG:6674')
        x,y=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
        self.assertTrue(bounds[0]<x<bounds[2] and bounds[1]<y<bounds[3])
        self.assertIsNone(projected_selection('g:池田市','EPSG:6674'))

    def test_vertical_wall_not_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'wall.obj'
            src.write_text('v 0 0 0\nv 0 4 0\nv 0 4 8\nv 0 0 8\nf 1 2 3 4\n')
            d=crop_obj(src,root/'wall_clipped.obj',(-1,1,1,3),'xyz')
            self.assertEqual(d['faces_output'],1)
            self.assertEqual(inspect_obj(root/'wall_clipped.obj').bbox,(0.,1.,0.,0.,3.,8.))

if __name__=='__main__':unittest.main()

class AutoCRSTests(unittest.TestCase):
    def test_auto_epsg_for_ikeda_and_other_regions(self):
        from plateau_blend.crs import japanese_zone_from_bbox, resolve_epsg
        from plateau_blend.auto import AutoRequest
        self.assertEqual(japanese_zone_from_bbox(135.429,34.819,135.431,34.821),'EPSG:6674')
        self.assertEqual(japanese_zone_from_bbox(139.75,35.68,139.76,35.69),'EPSG:6677')
        req=AutoRequest('r:135.429,34.819,135.431,34.821','AUTO',Path('scene.blend'))
        req.check()
        self.assertEqual(req.effective_epsg(),'EPSG:6674')
        with self.assertRaises(ValueError):resolve_epsg('AUTO','g:池田市')
        with self.assertRaises(ValueError):japanese_zone_from_bbox(10,10,11,11)
