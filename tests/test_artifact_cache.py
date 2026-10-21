import json
import tempfile
import unittest
from pathlib import Path
from plateau_blend.artifact_cache import mark, valid
from plateau_blend.crs import mesh_center, resolve_epsg, zone_from_sources
from plateau_blend.catalog import Source


class CacheAndMeshTests(unittest.TestCase):
    def test_tamper_and_truncated_metadata_trigger_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'photo.png'; p.write_bytes(b'a'*12)
            meta=Path(td)/'photo.json'
            key=[[0,0,1,1],'EPSG:6674',16]
            self.assertFalse(valid(meta,key,[p]))
            mark(meta,key,[p],{'zoom':16})
            self.assertTrue(valid(meta,key,[p]))
            self.assertFalse(valid(meta,[[0,0,2,2],'EPSG:6674',16],[p]))
            p.write_bytes(b'b'*12)
            self.assertFalse(valid(meta,key,[p]))
            p.write_bytes(b'a'*12)
            self.assertTrue(valid(meta,key,[p]))
            meta.write_text('{not a valid json',encoding='utf-8')
            self.assertFalse(valid(meta,key,[p]))

    def test_auto_zone_from_meshes_and_name_result(self):
        sample=Source('bldg','Ikeda','52350401',2025,'https://example.test/data.gml',100,2)
        self.assertEqual(resolve_epsg('AUTO','g:池田市',[sample]),'EPSG:6674')
        self.assertEqual(resolve_epsg('AUTO','m:52350401'),'EPSG:6674')
        self.assertEqual(resolve_epsg('AUTO','r:135.42,34.81,135.43,34.82'),'EPSG:6674')
        tokyo=Source('bldg','Tokyo','533945',2025,'https://example.test/x.gml',100,2)
        with self.assertRaisesRegex(ValueError,'複数'):
            zone_from_sources([sample,tokyo])
        self.assertAlmostEqual(mesh_center('5235')[0],135.5)
        with self.assertRaises(ValueError):mesh_center('52359901')

if __name__=='__main__': unittest.main()
