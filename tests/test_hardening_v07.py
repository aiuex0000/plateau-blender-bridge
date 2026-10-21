"""v0.7: corrupted cache recovery, streamed validation and cancellation."""
from __future__ import annotations
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plateau_blend.catalog import Source, download, validate_citygml
from plateau_blend.pipeline import _stage_obj
from plateau_blend.runtime import CancelToken, CancelledError


class _Response:
    def __init__(self, raw: bytes, url: str):
        self.source = io.BytesIO(raw)
        self.url = url
    def __enter__(self): return self
    def __exit__(self, *args): self.source.close()
    def geturl(self): return self.url
    def read(self, n=-1): return self.source.read(n)


class CityGMLDownloadTests(unittest.TestCase):
    @staticmethod
    def source():
        return Source('bldg','Sample','52353525',2024,
                      'https://assets.cms.plateau.reearth.io/dataset/udx/bldg/file.gml',None,1)

    def test_reject_html_even_with_xml_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            s=self.source()
            def opener(req, timeout):
                return _Response(b'<?xml version="1.0"?><html><body>Access denied</body></html>',s.url)
            with self.assertRaisesRegex(ValueError,'CityGMLのルート'):
                download(s,Path(td),opener=opener)
            self.assertFalse(list(Path(td).rglob('*.gml')))
            self.assertFalse(list(Path(td).rglob('*.part')))

    def test_reject_malformed_trailing_xml_even_when_citymodel_starts_correctly(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'truncated.gml'
            path.write_bytes(b'<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"><item>')
            with self.assertRaisesRegex(ValueError,'XML解析失敗'):
                validate_citygml(path)

    def test_reject_doctype_with_internal_entity(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'entity.gml'
            path.write_text('<!DOCTYPE CityModel [<!ENTITY e "hi">]><CityModel>&e;</CityModel>')
            with self.assertRaisesRegex(ValueError,'DTD/ENTITY'):
                validate_citygml(path)

    def test_reject_rootless_xml(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'x.gml';f.write_bytes(b'<?xml version="1.0"?>\n<!--no city model-->')
            with self.assertRaisesRegex(ValueError,'CityModel'):
                validate_citygml(f)

    def test_xml_namespace_and_comments_are_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'x.gml'
            f.write_text('<?xml version="1.0"?>\n<!-- Sample -->\n'
                         '<core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"></core:CityModel>')
            validate_citygml(f)

    def test_corrupt_metadata_recovers_from_network(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);s=self.source()
            raw=b'<?xml version="1.0"?><core:CityModel xmlns:core="http://www.opengis.net/citygml/2.0"/>'
            calls=[]
            def opener(req,timeout):
                calls.append(req.full_url)
                return _Response(raw,s.url)
            result=download(s,root,opener=opener)
            self.assertEqual(result.read_bytes(),raw)
            result.with_suffix('.json').write_text('{invalid',encoding='utf-8')
            result2=download(s,root,opener=opener)
            self.assertEqual(result2,result)
            self.assertEqual(len(calls),2)
            download(s,root,opener=opener)
            self.assertEqual(len(calls),2)

    def test_corrupt_cached_gml_retries_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);s=self.source()
            valid=b'<CityModel xmlns="http://www.opengis.net/citygml/2.0"/>'
            calls=[]
            def opener(req,timeout):
                calls.append(1)
                return _Response(valid,s.url)
            target=download(s,root,opener=opener)
            invalid=b'<html>HTML is not GML</html>'
            target.write_bytes(invalid)
            meta=target.with_suffix('.json')
            payload=json.loads(meta.read_text())
            payload['size']=len(invalid)
            payload['sha256']=hashlib.sha256(invalid).hexdigest()
            meta.write_text(json.dumps(payload))
            download(s,root,opener=opener)
            self.assertEqual(target.read_bytes(),valid)
            self.assertEqual(len(calls),2)


class NormalizedCacheTests(unittest.TestCase):
    def stage(self, root, src, logs, cancel=None):
        return _stage_obj(src,'building','xyz',(1000.,2000.,0.),root/'cache',[],logs.append,0,cancel=cancel)

    def test_corrupt_obj_is_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'sample.obj';src.write_text('v 1001 2002 3\nv 1005 2006 7\nf 1 2 1\n')
            logs=[]
            staged=self.stage(root,src,logs)
            saved=staged.read_bytes()
            staged.write_bytes(b'broken')
            self.stage(root,src,logs)
            self.assertEqual(staged.read_bytes(),saved)
            self.assertEqual(sum('[OBJ]' in line for line in logs),2)
            self.stage(root,src,logs)
            self.assertTrue(any('[CACHE]' in line for line in logs))
            self.assertEqual(src.read_text(),'v 1001 2002 3\nv 1005 2006 7\nf 1 2 1\n')

    def test_corrupt_staged_texture_is_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'model.obj'
            src.write_text('mtllib roof.mtl\nv 1001 2002 3\nf 1 1 1\n')
            (root/'roof.mtl').write_text('newmtl sample\nmap_Kd roof.png\n')
            (root/'roof.png').write_bytes(b'FAKE-TEXTURE')
            logs=[]
            obj=self.stage(root,src,logs)
            staged_tex=next(obj.parent.glob('texture_*'))
            staged_tex.write_bytes(b'corrupt')
            self.stage(root,src,logs)
            self.assertEqual(staged_tex.read_bytes(),b'FAKE-TEXTURE')
            self.assertEqual(sum('[OBJ]' in line for line in logs),2)

    def test_cancel_during_normalization_does_not_cache_success(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'huge.obj'
            with src.open('w') as f:
                for _ in range(10000):f.write('v 1001 2002 3\n')
            class CancelOnSecondCheck:
                def __init__(self): self.n=0
                def check(self):
                    self.n+=1
                    if self.n>=3: raise CancelledError('stopped')
            with self.assertRaises(CancelledError):
                self.stage(root,src,[],cancel=CancelOnSecondCheck())
            self.assertFalse(list((root/'cache').rglob('ready.json')))
            self.assertFalse(list((root/'cache').rglob('*.part')))


if __name__ == '__main__': unittest.main()

class ClippedCacheTests(unittest.TestCase):
    def test_broken_obj_and_manifest_are_rebuilt(self):
        from plateau_blend.clip import cached_clip
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            source=root/'city.obj'
            source.write_text('v 0 0 0\nv 5 0 0\nv 0 5 0\nf 1 2 3\n')
            logs=[]
            output,stats=cached_clip(source,root/'cache',(0,0,10,10),'xyz',logs.append)
            self.assertTrue(stats['vertices_output']>0)
            original=output.read_bytes()
            output.write_text('v 0 0 0\n')
            rebuilt,_=cached_clip(source,root/'cache',(0,0,10,10),'xyz',logs.append)
            self.assertEqual(rebuilt.read_bytes(),original)
            self.assertEqual(len([v for v in logs if '[CLIP]' in v]),2)
            cached_clip(source,root/'cache',(0,0,10,10),'xyz',logs.append)
            self.assertTrue(any('[CACHE]' in v for v in logs))
            (output.parent/'clip_ready.json').write_text('{unreadable',encoding='utf-8')
            rebuilt,_=cached_clip(source,root/'cache',(0,0,10,10),'xyz',logs.append)
            self.assertEqual(rebuilt.read_bytes(),original)

    def test_empty_clip_cache_is_held_as_explicit_no_geometry(self):
        from plateau_blend.clip import cached_clip
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'city.obj'
            src.write_text('v 10 10 0\nv 11 10 0\nv 10 11 0\nf 1 2 3\n')
            messages=[]
            a,st=cached_clip(src,root/'cache',(0,0,1,1),'xyz',messages.append)
            b,st2=cached_clip(src,root/'cache',(0,0,1,1),'xyz',messages.append)
            self.assertIsNone(a)
            self.assertIsNone(b)
            self.assertEqual(st2['vertices_output'],0)
            self.assertEqual(sum('[CACHE]' in v for v in messages),1)


if __name__ == "__main__": unittest.main()
