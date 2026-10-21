"""Performance and correctness regressions for offline large-file operation."""
from __future__ import annotations
import math
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from PIL import Image
from pyproj import Transformer
from plateau_blend.clip import crop_obj, cached_clip
from plateau_blend.runtime import CancelledError
from plateau_blend.gsi import fractional_tile, reproject_photo, PHOTO
from plateau_blend.geo import WorldFile


class SyntheticTiles:
    """Delivers unique per-pixel photos without network calls."""
    def __init__(self):
        self.cancel=None
        self.images={}
        self.tile_attempts=0

    def get(self, layer, z, x, y, required=False):
        assert layer == PHOTO and required
        key=(layer,z,x,y)
        if key not in self.images:
            self.tile_attempts += 1
            # every tile has a distinct color and a pixel gradient
            im=Image.new('RGB',(256,256))
            px=im.load()
            for row in range(256):
                for col in range(256):
                    px[col,row]=((col+x*17)%256,(row+y*13)%256,(x+3*y)%256)
            self.images[key]=im
        return self.images[key]


class ScalingTests(unittest.TestCase):
    def test_orthophoto_vectorized_matches_geographic_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            epsg='EPSG:6674'
            project=Transformer.from_crs(4326,epsg,always_xy=True)
            cx,cy=project.transform(135.430,34.820)
            bounds=(cx-320,cy-260,cx+320,cy+260)
            tiles=SyntheticTiles()
            result=reproject_photo(tiles,bounds,epsg,root/'p.png',root/'p.pgw',
                                   zoom=16,max_dimension=93,log=lambda *a:None)
            world=WorldFile.read(root/'p.pgw')
            inverse=Transformer.from_crs(epsg,4326,always_xy=True)
            with Image.open(root/'p.png') as im:
                for row in range(im.height):
                    for col in range(im.width):
                        x,y=world.xy(col,row)
                        lon,lat=inverse.transform(x,y)
                        fx,fy=fractional_tile(lon,lat,result['zoom'])
                        tx,ty=math.floor(fx),math.floor(fy)
                        exp=tiles.get(PHOTO,result['zoom'],tx,ty,True).getpixel(
                            (min(255,int((fx-tx)*256)),min(255,int((fy-ty)*256))))
                        self.assertEqual(im.getpixel((col,row)),exp,(col,row))
            self.assertGreater(result['image_size'][0],20)

    def test_large_obj_spooled_without_python_vertex_array(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            src=root/'big.obj'
            with src.open('w') as out:
                for i in range(120_000):
                    out.write(f'v {i} 2 3\n')
                out.write('f 1 2 3\n')
            tracemalloc.start()
            try:
                result=crop_obj(src,root/'out.obj',(-10,-10,200000,10),'xyz')
                _,peak=tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            # A Python list of 120k 3-tuples would require much more memory.
            self.assertLess(peak,9*1024*1024,peak)
            self.assertEqual(result['faces_input'],1)
            self.assertEqual(result['vertices_output'],0) # all points are collinear
            self.assertFalse((root/'out.obj').exists())

    def test_cancel_during_spooling_never_records_ready(self):
        class CancelAfter:
            def __init__(self):self.calls=0
            def check(self):
                self.calls+=1
                if self.calls>=2:raise CancelledError('stop')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'large.obj'
            with src.open('w') as out:
                for i in range(20000):out.write(f'v {i} 1 2\n')
                out.write('f 1 2 3\n')
            with self.assertRaises(CancelledError):
                cached_clip(src,root/'cache',(0,0,30000,3),'xyz',lambda *_:None,
                            cancel=CancelAfter())
            self.assertFalse(list((root/'cache').rglob('clip_ready.json')))
            self.assertFalse(list((root/'cache').rglob('*.obj')))

class SubprocessStreamingTests(unittest.TestCase):
    def test_chatty_child_streams_complete_log(self):
        import sys
        from plateau_blend.runtime import run_logged
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            count=[]
            cmd=[sys.executable,'-u','-c','for i in range(4000): print(f"line-{i}")']
            run_logged(cmd,root/'child.log',count.append)
            self.assertEqual(len(count),4000)
            self.assertEqual(count[0],'line-0')
            self.assertEqual(count[-1],'line-3999')
            self.assertEqual((root/'child.log').read_text().count('\n'),4000)

class TileRecoveryTests(unittest.TestCase):
    def test_corrupt_tile_cache_is_downloaded_again(self):
        import io
        from plateau_blend.gsi import GsiTiles
        class Response:
            def __init__(self,raw,url):
                self.raw=io.BytesIO(raw);self.url=url
            def __enter__(self):return self
            def __exit__(self,*args):self.raw.close()
            def geturl(self):return self.url
            def read(self,n=-1):return self.raw.read(n)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cached=root/PHOTO/'16'/'51200'/'26000.jpg'
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b'not a valid tile')
            calls=[]
            buf=io.BytesIO()
            Image.new('RGB',(256,256),(21,42,63)).save(buf,format='JPEG')
            def opener(req,timeout):
                calls.append(req.full_url)
                return Response(buf.getvalue(),req.full_url)
            tiles=GsiTiles(root,opener=opener)
            tile=tiles.get(PHOTO,16,51200,26000,required=True)
            self.assertEqual(len(calls),1)
            self.assertTrue(cached.is_file())
            self.assertEqual(tile.size,(256,256))
            self.assertEqual(tiles.get(PHOTO,16,51200,26000,required=True).size,(256,256))
            self.assertEqual(len(calls),1)

    def test_wrong_dimensions_never_cached(self):
        import io
        from plateau_blend.gsi import GsiTiles
        class Response:
            def __init__(self,raw,url):self.raw=io.BytesIO(raw);self.url=url
            def __enter__(self):return self
            def __exit__(self,*a):self.raw.close()
            def geturl(self):return self.url
            def read(self,n=-1):return self.raw.read(n)
        with tempfile.TemporaryDirectory() as td:
            buf=io.BytesIO()
            Image.new('RGB',(32,32),(2,3,4)).save(buf,format='JPEG')
            def opener(req,timeout):return Response(buf.getvalue(),req.full_url)
            tiles=GsiTiles(Path(td),opener=opener)
            with self.assertRaisesRegex(ValueError,'256x256'):
                tiles.get(PHOTO,16,51200,26000,required=True)
            self.assertFalse(list(Path(td).rglob('*.part')))
            self.assertFalse(list(Path(td).rglob('*.jpg')))

class ConverterCacheTests(unittest.TestCase):
    def test_converter_cache_detects_modified_obj_and_mtl(self):
        from unittest.mock import patch
        from plateau_blend.auto import converter_objects
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            gml=root/'data.gml';gml.write_text('<CityModel/>')
            binary=root/'nusamai.exe';binary.write_bytes(b'binary')
            calls=[]
            def mocked_run(cmd,logpath,log,cancel=None):
                calls.append(cmd)
                dest=Path(cmd[cmd.index('--output')+1])
                dest.mkdir(parents=True,exist_ok=True)
                (dest/'city.obj').write_text('mtllib city.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
                (dest/'city.mtl').write_text('newmtl roof\nKd 1 0 0\n')
            with patch('plateau_blend.auto.run_logged',mocked_run):
                paths=converter_objects(binary,[gml],root/'out','EPSG:6674','min_lod',lambda *_:None)
                self.assertEqual(len(calls),1)
                again=converter_objects(binary,[gml],root/'out','EPSG:6674','min_lod',lambda *_:None)
                self.assertEqual(again,paths)
                self.assertEqual(len(calls),1)
                paths[0].write_text(paths[0].read_text().replace('v 1 0 0','v 2 0 0'))
                converter_objects(binary,[gml],root/'out','EPSG:6674','min_lod',lambda *_:None)
                self.assertEqual(len(calls),2)
                (paths[0].parent/'city.mtl').write_text('newmtl roof\nKd 0 0 1\n')
                converter_objects(binary,[gml],root/'out','EPSG:6674','min_lod',lambda *_:None)
                self.assertEqual(len(calls),3)
