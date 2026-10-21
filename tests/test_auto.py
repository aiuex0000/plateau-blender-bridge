import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from pyproj import Transformer
from plateau_blend.catalog import citygml_query, select_sources, safe_source_url
from plateau_blend.gsi import GsiTiles, decode_dem, fractional_tile, make_terrain, reproject_photo, photo_zoom
from plateau_blend.auto import AutoRequest, _locate_axis, generate
from plateau_blend.discovery import blender_candidates
from plateau_blend.config import Config
from plateau_blend.pipeline import validate


class Response(io.BytesIO):
    def __init__(self,data,url):super().__init__(data);self.url=url
    def geturl(self):return self.url


class AutoTests(unittest.TestCase):
    def test_catalog_url_conditions_and_filter(self):
        self.assertIn('types=bldg',citygml_query('g:池田市'))
        self.assertIn('r:135.0,34.1,135.1,34.2',citygml_query('r:135.0,34.1,135.1,34.2'))
        with self.assertRaises(ValueError):citygml_query('r:135.1,34.2,135.0,34.1')
        with self.assertRaises(ValueError):safe_source_url('http://evil.test/a.gml')
        with self.assertRaises(ValueError):safe_source_url('https://assets.cms.plateau.reearth.io.evil.test/x')

    def test_catalog_newer_city_only_and_budget(self):
        src=lambda year: {'cityCode':'27204','cityName':'池田市','year':year,
                          'files':{'bldg':[{'url':f'https://assets.cms.plateau.reearth.io/{year}.gml',
                                            'code':'52350401','maxLod':2,'fileSize':100}]}}
        result=select_sources([src(2023),src(2025)])
        self.assertEqual(result[0].year,2025)
        with self.assertRaises(ValueError):select_sources([src(2025)],max_known_mb=0)
        bad=src(2025);bad['files']['bldg'][0]['code']='../../x'
        with self.assertRaises(ValueError):select_sources([bad])

    def test_catalog_counts_ward_border_twins_once(self):
        ward=lambda code,size: {'cityCode':code,'cityName':code,'year':2025,
                                'files':{'bldg':[{'url':f'https://assets.cms.plateau.reearth.io/{code}.gml',
                                                  'code':'53394525','maxLod':2,'fileSize':size}]}}
        self.assertEqual(len(select_sources([ward('13104',100),ward('13113',100)])),1)
        self.assertEqual(len(select_sources([ward('13104',100),ward('13113',101)])),2)

    def test_catalog_supplements_missing_meshes_from_older_year(self):
        def row(year,meshes):
            return {'cityCode':'27204','cityName':'池田市','year':year,'registrationYear':year,
                    'files':{'bldg':[{'code':mesh,'url':f'https://assets.cms.plateau.reearth.io/{year}/{mesh}.gml',
                                      'maxLod':2,'fileSize':100} for mesh in meshes]}}
        chosen=select_sources([row(2023,['52350401','52350402']),row(2025,['52350401'])])
        self.assertEqual([(s.mesh,s.year) for s in chosen],[('52350401',2025),('52350402',2023)])
        newest=select_sources([row(2025,['52350401']),row(2023,['52350401','52350402'])])
        self.assertEqual(chosen,newest)
        with self.assertRaisesRegex(ValueError,'広すぎる'):
            select_sources([row(2023,['52350401','52350402']),row(2025,['52350401'])],max_files=1)

    def test_dem_pixel_signed_and_missing(self):
        self.assertEqual(decode_dem(0,0,100),1.0)
        self.assertEqual(decode_dem(128,0,0),None)
        self.assertEqual(decode_dem(255,255,156),-1.0)
        x,y=fractional_tile(0,0,1)
        self.assertEqual((x,y),(1,1))

    def test_photo_and_dem_reprojection_with_fake_tiles(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            center=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
            bounds=(center[0]-30,center[1]-30,center[0]+30,center[1]+30)
            def fakeopen(request,timeout=30):
                url=request.full_url
                image=Image.new('RGB',(256,256), (100,120,140) if 'seamlessphoto' in url else (0,4,210))
                buf=io.BytesIO();image.save(buf,format='PNG')
                return Response(buf.getvalue(),url)
            tiles=GsiTiles(root/'tiles',opener=fakeopen,max_tiles=35)
            info=reproject_photo(tiles,bounds,'EPSG:6674',root/'photo.png',root/'photo.pgw',max_dimension=60)
            self.assertTrue((root/'photo.pgw').is_file())
            self.assertEqual(Image.open(root/'photo.png').getpixel((1,1)),(100,120,140))
            obj_info=make_terrain(tiles,bounds,'EPSG:6674',root/'terrain.obj',resolution=8)
            self.assertEqual(obj_info['valid_ratio'],1.0)
            self.assertIn('f ',(root/'terrain.obj').read_text())

    def test_axis_detection_rectangular_and_nonrectangular(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cx,cy=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
            obj=root/'a.obj';obj.write_text(f'v {cx-2} {cy-2} 10\nv {cx+2} {cy+2} 12\nf 1 2 1\n')
            axis=_locate_axis([obj],'r:135.428,34.818,135.432,34.822','EPSG:6674',lambda x:None)
            self.assertEqual(axis,'xyz')
            swapped=root/'s.obj';swapped.write_text(f'v {cy-2} {cx-2} 10\nv {cy+2} {cx+2} 12\nf 1 2 1\n')
            self.assertEqual(_locate_axis([swapped],'r:135.428,34.818,135.432,34.822','EPSG:6674',lambda x:None),'yxz')

    def test_managed_generated_inputs_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);work=d/'scene.work'/'auto';work.mkdir(parents=True)
            obj=work/'a.obj';obj.write_text('v 1 1 1\nv 2 2 2\nf 1 2 2\n')
            terrain=work/'t.obj';terrain.write_text('v 0 0 0\nv 3 0 0\nv 0 3 0\nf 1 2 3\n')
            png=work/'p.png';Image.new('RGB',(32,32),(130,110,70)).save(png)
            pgw=work/'p.pgw';pgw.write_text('1\n0\n0\n-1\n-0.5\n31.5\n')
            cfg=Config(d/'blender', (obj,), (terrain,),png,pgw,'EPSG:6674','EPSG:6674',d/'scene.blend',
                       imagery_attribution='GSI',imagery_terms_url='https://maps.gsi.go.jp/help/use.html')
            with self.assertRaisesRegex(ValueError,'衝突'):validate(cfg)
            self.assertEqual(validate(cfg,allow_managed_inputs=True)['coverage'],1.0)

    def test_discovery_scans_blender_root(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            a=root/'Blender Foundation'/'Blender 5.2'/'blender.exe';a.parent.mkdir(parents=True);a.write_text('')
            b=root/'Blender Foundation'/'Blender 4.4'/'blender.exe';b.parent.mkdir(parents=True);b.write_text('')
            found=blender_candidates(environ={'ProgramFiles':str(root)},platform='linux')
            self.assertGreaterEqual(len(found),2)
            self.assertEqual(found[0],a)

    def test_full_auto_flow_offline_with_fake_blender(self):
        from plateau_blend.catalog import Source
        from plateau_blend.gsi import GsiTiles
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            fake_blender=root/'blender.exe'; fake_blender.write_bytes(b'blender placeholder')
            fake_converter=root/'nusamai.exe'; fake_converter.write_bytes(b'converter placeholder')
            center=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
            source_record={'cityCode':'27204','cityName':'池田市','year':2025,
                'files':{'bldg':[{'url':'https://assets.cms.plateau.reearth.io/mock.gml',
                                   'code':'52350401','maxLod':1,'fileSize':100}]}}
            def fake_fetch(cond):
                self.assertTrue(cond.startswith('r:'))
                return [source_record]
            def fake_download(source,out):
                out.mkdir(parents=True,exist_ok=True)
                file=out/'test.gml';file.write_text('<CityModel/>')
                return file
            def fake_convert(binary,gmls,dest,epsg,lod,log):
                dest.mkdir(parents=True,exist_ok=True)
                obj=dest/'buildings.obj'
                x,y=center
                obj.write_text(f'v {x-10} {y-10} 5\nv {x+10} {y-10} 5\nv {x} {y+10} 30\nf 1 2 3\n')
                return [obj]
            def fakeopen(request,timeout=30):
                url=request.full_url
                image=Image.new('RGB',(256,256), (120,130,170) if 'seamlessphoto' in url else (0,4,210))
                buf=io.BytesIO();image.save(buf,format='PNG')
                return Response(buf.getvalue(),url)
            def tiles(root):return GsiTiles(root,opener=fakeopen)
            def fake_build(config,*,log,allow_managed_inputs,extra_provenance):
                self.assertTrue(allow_managed_inputs)
                self.assertEqual(extra_provenance['condition'],req.condition)
                p=validate(config,allow_managed_inputs=True)
                self.assertEqual(p['coverage'],1.0)
                config.output_blend.parent.mkdir(parents=True,exist_ok=True)
                config.output_blend.write_bytes(b'BLENDER mock')
                return config.output_blend
            req=AutoRequest('r:135.429,34.819,135.431,34.821','EPSG:6674',root/'scene.blend',
                            blender_executable=fake_blender,nusamai_executable=fake_converter,terrain_resolution=24,photo_zoom=14)
            logs=[]
            output=generate(req,log=logs.append,catalogue_fetch=fake_fetch,source_download=fake_download,
                            converter=fake_convert,blender_runner=fake_build,tile_factory=tiles)
            self.assertEqual(output,root/'scene.blend')
            self.assertTrue((root/'scene.work'/'auto'/'acquisition.json').is_file())
            self.assertTrue(any('[DONE]' in x for x in logs))

    def test_identical_gml_from_two_wards_is_converted_once(self):
        # Shinjuku/Shibuya both publish mesh 53394525 with byte-identical content.
        from plateau_blend.gsi import GsiTiles
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'blender.exe').write_bytes(b'x'); (root/'nusamai.exe').write_bytes(b'x')
            center=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
            records=[{'cityCode':code,'cityName':name,'year':2025,
                      'files':{'bldg':[{'url':f'https://assets.cms.plateau.reearth.io/{code}/mock.gml',
                                         'code':'52350401','maxLod':1,'fileSize':None}]}}
                     for code,name in (('13104','新宿区'),('13113','渋谷区'))]  # unknown sizes: only the hash can tell
            def fake_download(source,out):
                out.mkdir(parents=True,exist_ok=True)
                file=out/f'{source.city}.gml';file.write_text('<CityModel/>')
                return file
            converted=[]
            def fake_convert(binary,gmls,dest,epsg,lod,log):
                converted.extend(gmls)
                dest.mkdir(parents=True,exist_ok=True)
                obj=dest/'buildings.obj';x,y=center
                obj.write_text(f'v {x-10} {y-10} 5\nv {x+10} {y-10} 5\nv {x} {y+10} 30\nf 1 2 3\n')
                return [obj]
            def fakeopen(request,timeout=30):
                image=Image.new('RGB',(256,256),(120,130,170) if 'seamlessphoto' in request.full_url else (0,4,210))
                buf=io.BytesIO();image.save(buf,format='PNG')
                return Response(buf.getvalue(),request.full_url)
            def fake_build(config,**kw):
                config.output_blend.parent.mkdir(parents=True,exist_ok=True)
                config.output_blend.write_bytes(b'BLENDER mock')
                return config.output_blend
            req=AutoRequest('r:135.429,34.819,135.431,34.821','EPSG:6674',root/'scene.blend',
                            blender_executable=root/'blender.exe',nusamai_executable=root/'nusamai.exe',terrain_resolution=24,photo_zoom=14)
            logs=[]
            generate(req,log=logs.append,catalogue_fetch=lambda c:records,source_download=fake_download,
                     converter=fake_convert,blender_runner=fake_build,tile_factory=lambda r:GsiTiles(r,opener=fakeopen))
            self.assertEqual(len(converted),1)
            self.assertTrue(any('[DEDUP]' in x for x in logs))

if __name__=='__main__':unittest.main()
