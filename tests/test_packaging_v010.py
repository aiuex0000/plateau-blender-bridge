import json
from pathlib import Path
import tempfile
import unittest
from plateau_launcher import main

class PackageContractTests(unittest.TestCase):
    def test_headless_self_check_reports_projection_and_blender_scripts(self):
        with tempfile.TemporaryDirectory() as td:
            report=Path(td)/'check.json'
            self.assertEqual(main(['--self-check',str(report)]),0)
            payload=json.loads(report.read_text(encoding='utf-8'))
            self.assertTrue(payload['passed'])
            self.assertTrue(payload['proj_data_found'])
            self.assertFalse(payload['missing_scripts'])

    def test_packaging_spec_names_blender_subprocess_scripts(self):
        contents=(Path(__file__).resolve().parent.parent/'packaging'/'plateau_bridge.spec').read_text(encoding='utf-8')
        for target in ('blender_scene.py','blender_verify.py','collect_data_files(\'pyproj\')'):
            self.assertIn(target,contents)

if __name__=='__main__':unittest.main()
