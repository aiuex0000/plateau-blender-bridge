import io
import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from plateau_blend.appearance import resolve_asset, referenced_images, stage_gml
from plateau_blend.catalog import Source, download

BASE='https://assets.cms.plateau.reearth.io/assets/72/1234/test_city_2025/udx/bldg/53390001_bldg.gml'
XML=b'''<?xml version="1.0"?><core:CityModel xmlns:core="urn:core" xmlns:app="urn:app"><app:imageURI>../../appearance/roof.png</app:imageURI><app:imageURI>../../appearance/roof.png</app:imageURI></core:CityModel>'''


class MockResponse(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data);self.url=url
    def geturl(self):return self.url


class AppearanceTests(unittest.TestCase):
    def source(self):return Source('bldg','sample','53390001',2025,BASE,100,2)

    def test_resolve_within_dataset(self):
        asset=resolve_asset(BASE,'../../appearance/roof.png')
        self.assertEqual(asset.relative,'appearance/roof.png')
        self.assertTrue(asset.url.endswith('/test_city_2025/appearance/roof.png'))
        asset=resolve_asset(BASE,'../texture/special.jpg')
        self.assertEqual(asset.relative,'udx/texture/special.jpg')

    def test_reject_outside_and_remote(self):
        for value in ('../../../secret.png','https://evil.test/a.png','//evil.test/a.png',
                      'C:/Windows/a.png','../../appearance/a.png?token=x',
                      '../../appearance/a%2Ffoo.png','../../appearance/hack.zip'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                resolve_asset(BASE,value)

    def test_stream_parse_deduplicates_references(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'sample.gml';p.write_bytes(XML)
            self.assertEqual(len(referenced_images(p,BASE)),1)
            with self.assertRaises(ValueError):referenced_images(p,BASE,max_images=0)

    def test_staged_paths_cache_resume_and_image_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);gml=root/'source.gml';gml.write_bytes(XML)
            png=io.BytesIO();Image.new('RGB',(4,4),(4,5,6)).save(png,format='PNG')
            opens=[]
            def fake_open(request,timeout):
                opens.append(request.full_url)
                return MockResponse(png.getvalue(),request.full_url)
            staged,report=stage_gml(self.source(),gml,root/'cache',opener=fake_open,log=lambda s:None)
            self.assertEqual(report['images'],1)
            self.assertTrue((staged.parents[2]/'appearance'/'roof.png').is_file())
            self.assertEqual(len(opens),1)
            stage_gml(self.source(),gml,root/'cache',opener=fake_open,log=lambda s:None)
            self.assertEqual(len(opens),1)
            image=staged.parents[2]/'appearance'/'roof.png'
            image.write_bytes(b'corrupted bytes' + b' ' * 40)
            stage_gml(self.source(),gml,root/'cache',opener=fake_open,log=lambda s:None)
            self.assertEqual(len(opens),2)
            self.assertTrue(json.loads((staged.parents[2]/'appearance_ready.json').read_text())['complete'])

    def test_reject_invalid_response_and_never_publish_it(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);gml=root/'source.gml';gml.write_bytes(XML)
            def fake_open(request,timeout):return MockResponse(b'<!DOCTYPE html><html>error</html>',request.full_url)
            with self.assertRaises(Exception):stage_gml(self.source(),gml,root/'cache',opener=fake_open,log=lambda s:None)
            self.assertFalse(list((root/'cache').rglob('*.part')))
            self.assertFalse(list((root/'cache').rglob('*.png')))

    def test_gml_download_corrupt_cache_is_refetched(self):
        with tempfile.TemporaryDirectory() as td:
            src=self.source()
            root=Path(td)
            calls=[]
            def opener(request,timeout):
                calls.append(request.full_url)
                return MockResponse(XML,request.full_url)
            p=download(src,root,opener=opener)
            self.assertEqual(len(calls),1)
            download(src,root,opener=opener)
            self.assertEqual(len(calls),1)
            p.write_bytes(b'X'*len(XML))
            download(src,root,opener=opener)
            self.assertEqual(len(calls),2)
            self.assertEqual(p.read_bytes(),XML)

    def test_dtd_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.gml'
            p.write_text('<!DOCTYPE test [<!ENTITY e SYSTEM "file:///etc/passwd">]><test>&e;</test>')
            with self.assertRaisesRegex(ValueError,'DTD'):
                referenced_images(p,BASE)

if __name__=='__main__':unittest.main()


class TexturedAutoIntegration(unittest.TestCase):
    def test_textured_gml_stage_is_wired_into_converter_and_blender_pipeline(self):
        from unittest.mock import patch
        from pyproj import Transformer
        from plateau_blend.auto import AutoRequest, generate
        from plateau_blend.appearance import stage_gml as actual_stage
        from plateau_blend.gsi import GsiTiles
        from plateau_blend.pipeline import validate

        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'blender.exe').write_bytes(b'stub')
            (root/'nusamai.exe').write_bytes(b'stub')
            center=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
            fake_image=io.BytesIO()
            Image.new('RGB',(256,256),(80,110,140)).save(fake_image,format='PNG')
            requested=[]
            def fetch(_condition):
                return [{'cityCode':'27204','cityName':'池田市','year':2025,
                         'files':{'bldg':[{'url':BASE,'code':'52350401','maxLod':2,'fileSize':100}]}}]
            def dl(_source,dirpath):
                dirpath.mkdir(parents=True,exist_ok=True)
                p=dirpath/'source.gml';p.write_bytes(XML)
                return p
            def fake_appearance_open(request,timeout):
                requested.append(request.full_url)
                return MockResponse(fake_image.getvalue(),request.full_url)
            def staged(source,path,folder,**kw):
                return actual_stage(source,path,folder,opener=fake_appearance_open,**kw)
            def converter(binary,gmls,dest,epsg,lod,log):
                self.assertEqual(lod,'textured_max_lod')
                self.assertEqual(epsg,'EPSG:6674')
                gml=gmls[0]
                self.assertTrue((gml.parents[2]/'appearance'/'roof.png').is_file())
                dest.mkdir(parents=True,exist_ok=True)
                from shutil import copyfile
                copyfile(gml.parents[2]/'appearance'/'roof.png',dest/'roof.png')
                (dest/'material.mtl').write_text('newmtl sample\nmap_Kd roof.png\n')
                x,y=center
                obj=dest/'sample.obj'
                obj.write_text('mtllib material.mtl\nusemtl sample\n'+
                               f'v {x-5} {y-5} 5\nv {x+5} {y-5} 5\nv {x} {y+5} 8\nf 1 2 3\n')
                return [obj]
            def fake_tile(request,timeout):
                url=request.full_url
                im=Image.new('RGB',(256,256),(80,110,140) if 'seamlessphoto' in url else (0,4,210))
                buf=io.BytesIO();im.save(buf,format='PNG')
                return MockResponse(buf.getvalue(),url)
            def tiles(root):return GsiTiles(root,opener=fake_tile)
            def blender(cfg,**kw):
                self.assertEqual(validate(cfg,allow_managed_inputs=True)['coverage'],1.0)
                cfg.output_blend.write_bytes(b'BLENDER mocked')
                return cfg.output_blend
            req=AutoRequest('r:135.429,34.819,135.431,34.821','AUTO',root/'scene.blend',
                            blender_executable=root/'blender.exe',nusamai_executable=root/'nusamai.exe',
                            lod='textured_max_lod',photo_zoom=14,terrain_resolution=24)
            with patch('plateau_blend.auto.stage_gml',side_effect=staged):
                output=generate(req,log=lambda _:None,catalogue_fetch=fetch,source_download=dl,
                                converter=converter,blender_runner=blender,tile_factory=tiles)
            self.assertEqual(output,root/'scene.blend')
            self.assertEqual(len(requested),1)
            appearance=__import__('json').loads((root/'scene.work'/'auto'/'acquisition.json').read_text(encoding='utf-8'))
            self.assertEqual(appearance['texture_policy'],'strict')
            self.assertEqual(appearance['texture_stage'][0]['images'],1)
