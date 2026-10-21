import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from pyproj import Transformer
from plateau_blend.runtime import CancelToken, CancelledError, run_logged
from plateau_blend.catalog import fetch_catalog, select_sources
from plateau_blend.resources import forecast, enforce_budget
from plateau_blend.diagnostics import inspect_environment
from plateau_blend.auto import AutoRequest, generate


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)


class V05Tests(unittest.TestCase):
    def test_quiet_process_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            token=CancelToken()
            timer=threading.Timer(.2,token.cancel)
            timer.start()
            started=time.monotonic()
            try:
                with self.assertRaises(CancelledError):
                    run_logged([sys.executable,'-c','import time;time.sleep(30)'],
                               Path(td)/'child.log', lambda s:None,cancel=token,timeout=40)
            finally:
                timer.join()
            self.assertLess(time.monotonic()-started,4.0)

    def test_proc_log_and_nonzero_status(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'log.txt';lines=[]
            run_logged([sys.executable,'-u','-c','print("begin");print("end")'],path,lines.append)
            self.assertEqual(lines,['begin','end'])
            self.assertIn('end',path.read_text())
            with self.assertRaisesRegex(RuntimeError,'exit=4'):
                run_logged([sys.executable,'-c','import sys;sys.exit(4)'],path,lambda s:None)

    def test_cancel_before_spawning(self):
        with tempfile.TemporaryDirectory() as td:
            token=CancelToken(); token.cancel()
            with self.assertRaises(CancelledError):
                run_logged([sys.executable,'--version'],Path(td)/'not_created.log',cancel=token)
            self.assertFalse((Path(td)/'not_created.log').exists())

    def test_response_shapes_single_multi_and_error(self):
        city={'cityCode':'12345','cityName':'Test','year':2025,'files':{'bldg':[]}}
        for body in (city,[city],{'cities':[city]}):
            def opening(*a,**kw):return Response(json.dumps(body).encode())
            self.assertEqual(fetch_catalog('m:533935',opener=opening),[city])
        with self.assertRaisesRegex(ValueError,'rate limit'):
            fetch_catalog('m:533935',opener=lambda *a,**kw:Response(b'{"error":"rate limit"}'))
        with self.assertRaises(ValueError):
            fetch_catalog('m:533935',opener=lambda *a,**kw:Response(b'"unexpected"'))

    def test_resource_forecast_deterministic(self):
        x,y=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
        bounds=(x-50,y-50,x+50,y+50)
        p=forecast(bounds,'EPSG:6674',wanted_zoom=16,dem_resolution=40)
        self.assertLess(p.photo_tiles,10)
        self.assertGreaterEqual(p.terrain_vertices,1600)
        self.assertGreater(p.photo_width,10)
        self.assertLess(p.photo_width,2049)
        enforce_budget(p)
        with self.assertRaisesRegex(ValueError,'多すぎ'):
            enforce_budget(p,max_total_requests=1)
        with self.assertRaises(ValueError):
            forecast((0,1,0,2),'EPSG:6674')

    def test_environment_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            data=inspect_environment(blender=Path(td)/'b.exe',nusamai=Path(td)/'n.exe',
                                     output=Path(td)/'test.blend',check_versions=False)
            self.assertFalse(data['blender']['found'])
            self.assertFalse(data['nusamai']['found'])
            self.assertGreaterEqual(data['free_space_gib'],0)
            self.assertIsNotNone(data['Pillow'])

    def test_cancel_prevents_catalog_and_output(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            b=folder/'b.exe'; n=folder/'n.exe'
            b.write_bytes(b'exe');n.write_bytes(b'exe')
            req=AutoRequest('r:135.42,34.81,135.43,34.82','EPSG:6674',folder/'out.blend',b,n)
            token=CancelToken();token.cancel()
            with self.assertRaises(CancelledError):
                generate(req,cancel=token,catalogue_fetch=lambda *a: self.fail('should not fetch'))
            self.assertFalse(req.output_blend.exists())

    def test_cancel_after_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            b=folder/'b.exe'; n=folder/'n.exe'
            b.write_bytes(b'exe');n.write_bytes(b'exe')
            req=AutoRequest('r:135.42,34.81,135.43,34.82','EPSG:6674',folder/'out.blend',b,n)
            token=CancelToken()
            def fetch(*a):
                token.cancel()
                return []
            with self.assertRaises(CancelledError):
                generate(req,cancel=token,catalogue_fetch=fetch)
            self.assertFalse((folder/'out.blend').exists())

if __name__=='__main__':unittest.main()

# Outside unittest discovery the following standalone class is also included.
class RedirectSafetyTests(unittest.TestCase):
    def test_gsi_redirect_allowlist(self):
        from plateau_blend.gsi import _safe_gsi_url, _GsiSafeRedirect
        self.assertTrue(_safe_gsi_url('https://cyberjapandata.gsi.go.jp/xyz/dem_png/14/1/1.png'))
        self.assertFalse(_safe_gsi_url('http://cyberjapandata.gsi.go.jp/xyz'))
        self.assertFalse(_safe_gsi_url('https://cyberjapandata.gsi.go.jp.evil.test/x'))
        handler=_GsiSafeRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(None,None,302,'moved',{},'https://evil.test/track')
