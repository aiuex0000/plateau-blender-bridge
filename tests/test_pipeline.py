import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from plateau_blend.config import Config
from plateau_blend.pipeline import validate, run, report



def fake_blender_binary(root: Path, script: str) -> Path:
    """Use an executable .cmd on Windows; Unix can run the python shebang."""
    path=root/'mock_blender.py'
    path.write_text(script,encoding='utf-8')
    if sys.platform == 'win32':
        launcher=root/'mock_blender.cmd'
        launcher.write_text('@echo off\r\n"' + sys.executable + '" "' + str(path) + '" %*\r\n',encoding='utf-8')
        return launcher
    path.chmod(0o755)
    return path

def example(d: Path, *, black=False):
    d.mkdir(parents=True, exist_ok=True)
    building=d/'b.obj'; terrain=d/'t.obj'; png=d/'image.png'; pgw=d/'image.pgw'
    building.write_text('v 1001 2001 3\nv 1002 2001 3\nv 1001 2002 4\nf 1 2 3\n')
    terrain.write_text('v 1000 2000 0\nv 1010 2000 0\nv 1000 2010 1\nv 1010 2010 0\nf 1 2 3\nf 2 4 3\n')
    Image.new('RGB',(32,32),(0,0,0) if black else (100,180,110)).save(png)
    # Image runs 999.5..1030.5 in x and 1999.5..2030.5 in y.
    pgw.write_text('1\n0\n0\n1\n1000\n2000\n')
    return {'blender_executable':str(d/'blender_fake'), 'building_objs':[str(building)],
            'terrain_objs':[str(terrain)], 'aerial_png':str(png), 'world_file':str(pgw),
            'epsg':'EPSG:6674','image_epsg':'EPSG:6674','output_blend':str(d/'out.blend'),
            'imagery_attribution':'国土地理院','imagery_terms_url':'https://maps.gsi.go.jp/help/use.html'}


def load_config(d, payload):
    f=d/'config.json'; f.write_text(json.dumps(payload),encoding='utf-8')
    return Config.load(f)


class PipelineTests(unittest.TestCase):
    def test_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); cfg=load_config(d, example(d))
            r=report(validate(cfg))
            self.assertEqual(r['buildings_vertices'],3)
            self.assertEqual(r['terrain_vertices'],4)
            self.assertEqual(r['image_terrain_coverage_sample'],1.0)
            self.assertEqual(r['origin_xy'],[1005.0,2005.0])

    def test_wrong_image_epsg_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); data=example(d); data['image_epsg']='EPSG:4326'
            with self.assertRaisesRegex(ValueError, 'EPSG一致'):
                load_config(d,data)

    def test_partial_photo_fails_then_explicit_allows(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); data=example(d)
            (d/'image.pgw').write_text('1\n0\n0\n1\n2000\n2000\n')
            cfg=load_config(d,data)
            with self.assertRaisesRegex(ValueError,'覆いません'): validate(cfg)
            data['allow_partial_imagery']=True
            cfg=load_config(d,data)
            self.assertEqual(validate(cfg)['coverage'],0)

    def test_black_photo_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); cfg=load_config(d, example(d,black=True))
            with self.assertRaisesRegex(ValueError,'黒/透明'): validate(cfg)

    def test_corrupt_reopen_does_not_publish_a_final_blend(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);data=example(d)
            binary=fake_blender_binary(d,"""#!/usr/bin/env python3
import sys,json,pathlib
p=pathlib.Path(sys.argv[-1]);scene=json.loads((p.parent/'manifest.json').read_text(encoding='utf-8'))
if 'verify_' not in p.name:
 pathlib.Path(scene['target_blend']).write_bytes(b'BLENDER-mock-not-a-real-blend')
else:
 p.write_text(json.dumps({'schema':1,'loaded_blend':scene['target_blend'],
   'provenance_present':True,'crs':scene['crs'],'origin_xyz':scene['origin_xyz'],
   'buildings_bbox':scene['buildings'][0]['expected_bbox_local'],
   'terrain_bbox':scene['terrain'][0]['expected_bbox_local'],
   'buildings_mesh_count':1,'terrain_mesh_count':1,'terrain_uv_mesh_count':1,
   'file_images':1,'packed_file_images':0,
   'terrain_textured_mesh_count':1,'terrain_bad_uv_mesh_count':0,
   'building_textured_sources':[]}))
""")
            data['blender_executable']=str(binary)
            with self.assertRaisesRegex(RuntimeError,'再読込検証'):
                run(load_config(d,data),log=lambda *_:None)
            self.assertFalse((d/'out.blend').exists())
            self.assertTrue(list((d/'out.work').glob('building_*.blend')))
            self.assertTrue((d/'out.work'/'last_verify.log').exists())

    def test_mock_process_creates_blend_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); data=example(d)
            fake=fake_blender_binary(d,"""#!/usr/bin/env python3
import json,sys,pathlib
p=pathlib.Path(sys.argv[-1]); m=json.loads((p.parent/'manifest.json').read_text(encoding='utf-8'))
assert m['terrain'] and m['buildings']
if 'verify_' in p.name:
  def union(kind):
    b=[x['expected_bbox_local'] for x in m[kind]]
    return [min(x[i] for x in b) if i<3 else max(x[i] for x in b) for i in range(6)]
  report={'schema':1,'loaded_blend':m['target_blend'],'provenance_present':True,
          'crs':m['crs'],'origin_xyz':m['origin_xyz'],'buildings_bbox':union('buildings'),
          'terrain_bbox':union('terrain'),'buildings_mesh_count':1,'terrain_mesh_count':1,
          'terrain_uv_mesh_count':1,'file_images':1,'packed_file_images':1,
          'terrain_textured_mesh_count':1,'terrain_bad_uv_mesh_count':0,
          'building_textured_sources':[]}
  p.write_text(json.dumps(report))
else:
  for entry in m['terrain']+m['buildings']:
    assert pathlib.Path(entry['path']).is_file()
  pathlib.Path(m['target_blend']).write_bytes(b'BLENDER-FAKE')
""")
            data['blender_executable']=str(fake)
            config=load_config(d,data)
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [d/'b.obj',d/'t.obj',d/'image.png',d/'image.pgw']}
            log=[]
            output=run(config,log=log.append)
            self.assertTrue(output.is_file())
            self.assertTrue(output.with_suffix('.provenance.json').exists())
            self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
            with self.assertRaises(FileExistsError): run(config,log=log.append)
            run(config,overwrite_output=True,log=log.append)
            self.assertTrue(any('[CACHE]' in s for s in log))

if __name__=='__main__': unittest.main()
