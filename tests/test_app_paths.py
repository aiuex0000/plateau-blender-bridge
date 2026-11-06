import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plateau_blend import app_paths


class DataDirTests(unittest.TestCase):
    def test_uses_data_folder_inside_the_app_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(app_paths.data_dir(root), root / 'data')
            self.assertEqual(app_paths.logs_dir(root), root / 'data' / 'logs')
            self.assertEqual(app_paths.edge_profile_dir(root), root / 'data' / 'edge-profile')
            self.assertEqual(list((root / 'data').iterdir()), [])  # 書込み確認の跡を残さない

    def test_falls_back_to_temp_when_the_app_folder_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(app_paths, '_writable', return_value=False), \
                    patch.object(app_paths.tempfile, 'gettempdir', return_value=td):
                self.assertEqual(app_paths.data_dir(Path(r'C:\Program Files\X')),
                                 Path(td) / 'PLATEAU-Blender-Bridge')

    def test_never_points_at_localappdata(self):
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as app:
            with patch.dict('os.environ', {'LOCALAPPDATA': local}):
                self.assertEqual(app_paths.data_dir(Path(app)), Path(app) / 'data')
            self.assertEqual(list(Path(local).iterdir()), [])

    def test_frozen_root_is_the_exe_folder(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / 'PLATEAU-Blender-Bridge.exe'
            with patch.object(app_paths.sys, 'frozen', True, create=True), \
                    patch.object(app_paths.sys, 'executable', str(exe)):
                self.assertEqual(app_paths.app_root(), exe.resolve().parent)


if __name__ == '__main__':
    unittest.main()
