"""web_server の HTTP 契約テスト。127.0.0.1 に実際に立てて urllib / http.client で叩く。"""
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from plateau_blend.web_server import MAX_BODY_BYTES, BridgeServer


class FakeApi:
    """BridgeApi の代わり。公開・非公開・property・例外だけを確かめる。"""

    def __init__(self):
        self.calls = 0

    def echo(self, value=None):
        self.calls += 1
        return {'value': value}

    def boom(self):
        self.calls += 1
        raise ValueError('壊れた')

    def _hidden(self):
        return 'no'

    @property
    def running(self):
        return False


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.web = Path(self._tmp.name)
        (self.web / 'index.html').write_text('<html>PLATEAU</html>', encoding='utf-8')
        (self.web / 'app.js').write_text('console.log(1)', encoding='utf-8')
        (self.web / 'app.css').write_text('body{}', encoding='utf-8')
        self.api = FakeApi()
        self.server = BridgeServer(self.api, web_dir=self.web, token='test-token')
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f'http://127.0.0.1:{self.server.port}'
        self.host = f'127.0.0.1:{self.server.port}'

    # ── 補助 ────────────────────────────────────────────────────────

    def post(self, name, args=(), token='test-token', host=None, body=None):
        payload = json.dumps({'args': list(args)}).encode('utf-8') if body is None else body
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-Bridge-Token'] = token
        if host is not None:
            headers['Host'] = host
        request = Request(self.base + '/api/' + name, data=payload, headers=headers, method='POST')
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode('utf-8'))

    def get(self, path, host=None):
        headers = {'Host': host} if host else {}
        request = Request(self.base + path, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read(), response.headers.get('Content-Type')
        except HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get('Content-Type')

    def raw_get(self, path):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        try:
            conn.putrequest('GET', path, skip_host=True)
            conn.putheader('Host', self.host)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    # ── 静的配信 ────────────────────────────────────────────────────

    def test_every_response_says_connection_close(self):
        # AdGuard などの中継が閉じた接続を使い回さないよう、毎回明示する。
        for method, path in (('GET', '/app.js'), ('POST', '/api/missing')):
            conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
            try:
                conn.putrequest(method, path, skip_host=True)
                conn.putheader('Host', self.host)
                conn.putheader('X-Bridge-Token', 'test-token')
                conn.putheader('Content-Length', '0')
                conn.endheaders()
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.getheader('Connection'), 'close', path)
            finally:
                conn.close()

    def test_page_served_is_marked_only_by_index(self):
        self.get('/app.js')
        self.assertIsNone(self.server.page_served_at)
        status, _, _ = self.get('/')
        self.assertEqual(status, 200)
        self.assertIsNotNone(self.server.page_served_at)

    def test_bye_is_recorded_without_counting_as_contact(self):
        self.assertIsNone(self.server.bye_at)
        status, payload = self.post('_bye', token='wrong')
        self.assertEqual(status, 403)
        self.assertIsNone(self.server.bye_at)
        status, payload = self.post('_bye')
        self.assertEqual((status, payload), (200, {'ok': True, 'result': None}))
        self.assertIsNotNone(self.server.bye_at)
        self.assertIsNone(self.server.last_contact)

    def test_index_is_served_without_a_token(self):
        status, body, content_type = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn(b'PLATEAU', body)
        self.assertTrue(content_type.startswith('text/html'))
        self.assertIn('utf-8', content_type)

    def test_direct_files_are_served_with_their_types(self):
        status, body, content_type = self.get('/app.js')
        self.assertEqual(status, 200)
        self.assertIn(b'console.log', body)
        self.assertTrue(content_type.startswith('text/javascript'))

        status, _, content_type = self.get('/app.css')
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith('text/css'))

    def test_static_host_must_be_loopback_with_the_server_port(self):
        status, _, _ = self.get('/', host='evil.example')
        self.assertEqual(status, 403)

    def test_parent_reference_is_not_served(self):
        status, _ = self.raw_get('/../app.js')
        self.assertEqual(status, 404)
        status, _ = self.raw_get('/%2e%2e/app.js')
        self.assertEqual(status, 404)

    def test_subdirectory_and_missing_files_are_not_served(self):
        self.assertEqual(self.get('/nope.js')[0], 404)
        self.assertEqual(self.get('/sub/app.js')[0], 404)

    # ── API ────────────────────────────────────────────────────────

    def test_missing_or_wrong_token_is_forbidden(self):
        self.assertEqual(self.post('echo', token=None)[0], 403)
        self.assertEqual(self.post('echo', token='wrong')[0], 403)
        self.assertEqual(self.api.calls, 0)

    def test_api_host_must_match(self):
        self.assertEqual(self.post('echo', host='evil.example')[0], 403)
        self.assertEqual(self.api.calls, 0)

    def test_public_method_returns_ok_and_result(self):
        status, payload = self.post('echo', ('hello',))
        self.assertEqual(status, 200)
        self.assertEqual(payload, {'ok': True, 'result': {'value': 'hello'}})

    def test_no_args_is_allowed(self):
        status, payload = self.post('echo')
        self.assertEqual(status, 200)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['result'], {'value': None})

    def test_hidden_property_and_unknown_names_are_404(self):
        for name in ('_hidden', 'running', 'nope'):
            with self.subTest(name=name):
                self.assertEqual(self.post(name)[0], 404)

    def test_exception_is_reported_without_dropping_the_server(self):
        status, payload = self.post('boom')
        self.assertEqual(status, 200)
        self.assertFalse(payload['ok'])
        self.assertTrue(payload['error'].startswith('ValueError: '))
        self.assertIn('壊れた', payload['error'])
        # 落ちていないこと
        self.assertEqual(self.post('echo', ('still here',))[0], 200)

    def test_each_api_call_updates_the_heartbeat(self):
        self.assertIsNone(self.server.last_contact)
        self.post('echo', ('beat',))
        self.assertIsNotNone(self.server.last_contact)

    def test_body_larger_than_one_megabyte_is_413(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        try:
            conn.putrequest('POST', '/api/echo', skip_host=True)
            conn.putheader('Host', self.host)
            conn.putheader('X-Bridge-Token', 'test-token')
            conn.putheader('Content-Type', 'application/json')
            conn.putheader('Content-Length', str(MAX_BODY_BYTES + 1))
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
