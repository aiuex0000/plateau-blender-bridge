import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from plateau_blend import __version__, auto_cli


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = auto_cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class AutoCliResultTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def test_diagnose_writes_ok_result(self):
        target = self.tmp / 'r.json'
        report = {'blender': 'ok', 'free_gb': 1.5}
        with patch.object(auto_cli, 'inspect_environment', return_value=report):
            code, out, err = run(['--diagnose', '--result-json', str(target)])
        self.assertEqual(code, 0)
        data = self.load(target)
        self.assertTrue(data['ok'])
        self.assertEqual(data['exit_code'], 0)
        self.assertEqual(data['mode'], 'diagnose')
        self.assertEqual(data['version'], __version__)
        self.assertEqual(data['data'], report)
        self.assertIsNone(data['error'])
        self.assertIsNone(data['output'])
        self.assertIsNone(data['verify_json'])
        self.assertIn('ok', out)
        self.assertEqual(err, '')

    def test_generate_exception_writes_error_result(self):
        target = self.tmp / 'r.json'
        output = self.tmp / 'scene.blend'
        with patch.object(auto_cli, 'generate', side_effect=RuntimeError('boom')):
            code, out, err = run(['--condition', 'g:池田市', '--output', str(output), '--result-json', str(target)])
        self.assertEqual(code, 1)
        data = self.load(target)
        self.assertFalse(data['ok'])
        self.assertEqual(data['exit_code'], 1)
        self.assertEqual(data['mode'], 'generate')
        self.assertEqual(data['error'], {'type': 'RuntimeError', 'message': 'boom'})
        self.assertEqual(Path(data['output']), output.resolve())
        self.assertIn('[ERROR]', err)

    def test_default_path_sits_next_to_output(self):
        output = self.tmp / 'scene.blend'
        with patch.object(auto_cli, 'inspect_environment', return_value={'ok': True}):
            code, _, _ = run(['--diagnose', '--output', str(output)])
        self.assertEqual(code, 0)
        target = (self.tmp / 'scene.result.json').resolve()
        self.assertTrue(target.is_file())
        self.assertEqual(self.load(target)['mode'], 'diagnose')

    def test_smoke_result_is_recorded(self):
        target = self.tmp / 'r.json'
        with patch.object(auto_cli, 'run_smoke', return_value=self.tmp / 'smoke.blend'):
            code, out, err = run(['--smoke-test', '--result-json', str(target)])
        self.assertEqual(code, 0)
        data = self.load(target)
        self.assertTrue(data['ok'])
        self.assertEqual(data['mode'], 'smoke')
        self.assertEqual(data['data'], str(self.tmp / 'smoke.blend'))
        self.assertIsNone(data['error'])
        self.assertIn('[SMOKE OK]', out)

    def test_generate_records_verify_json(self):
        output = (self.tmp / 'scene.blend').resolve()
        work = output.with_suffix('.work')
        work.mkdir()
        verify = work / 'verify_abc123.json'
        verify.write_text('{}', encoding='utf-8')
        target = self.tmp / 'r.json'
        with patch.object(auto_cli, 'generate', return_value=output):
            code, _, _ = run(['--condition', 'g:池田市', '--output', str(output), '--result-json', str(target)])
        self.assertEqual(code, 0)
        data = self.load(target)
        self.assertTrue(data['ok'])
        self.assertEqual(Path(data['verify_json']), verify)
        self.assertEqual(Path(data['output']), output)

    def test_unwritable_result_path_keeps_exit_code(self):
        # An existing directory cannot be overwritten as a file.
        with patch.object(auto_cli, 'inspect_environment', return_value={}):
            code, _, err = run(['--diagnose', '--result-json', str(self.tmp)])
        self.assertEqual(code, 0)
        self.assertIn('[WARN]', err)


if __name__ == '__main__':
    unittest.main()
