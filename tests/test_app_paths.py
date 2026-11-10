import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plateau_blend import app_paths


class QuickProbeTests(unittest.TestCase):
    def test_writable_quick_reports_true_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'new'
            self.assertIs(app_paths.writable_quick(folder), True)
            self.assertEqual(list(folder.iterdir()), [])

    def test_writable_quick_is_none_when_the_probe_hangs(self):
        import threading
        release = threading.Event()
        self.addCleanup(release.set)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(Path, 'mkdir', lambda *a, **k: release.wait(5)):
            self.assertIsNone(app_paths.writable_quick(Path(tmp) / 'slow', timeout=0.1))

    def test_writable_quick_is_false_when_a_file_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / 'blocked'
            blocked.write_bytes(b'')
            self.assertIs(app_paths.writable_quick(blocked / 'child'), False)

    def test_documents_dir_falls_back_to_home(self):
        if app_paths.os.name != 'nt':
            self.assertEqual(app_paths.documents_dir(), Path.home() / 'Documents')
            return
        import ctypes
        with patch.object(ctypes, 'windll', None):  # API が使えない時
            self.assertEqual(app_paths.documents_dir(), Path.home() / 'Documents')

    def test_documents_dir_is_a_real_folder_on_windows(self):
        if app_paths.os.name != 'nt':
            self.skipTest('Windows only')
        self.assertTrue(app_paths.documents_dir().is_absolute())


class DataDirTests(unittest.TestCase):
    def test_uses_data_folder_inside_the_app_folder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(app_paths.data_dir(root), root / 'data')
            self.assertEqual(app_paths.logs_dir(root), root / 'data' / 'logs')
            self.assertEqual(app_paths.edge_profile_dir(root), root / 'data' / 'edge-profile')
            self.assertEqual(list((root / 'data').iterdir()), [])  # 書込み確認の跡を残さない

    def test_write_check_leaves_existing_files_alone(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'data').mkdir()
            kept = root / 'data' / '.write-test'
            kept.write_text('keep me', encoding='utf-8')
            self.assertEqual(app_paths.data_dir(root), root / 'data')
            self.assertEqual(kept.read_text(encoding='utf-8'), 'keep me')
            self.assertEqual(sorted(p.name for p in (root / 'data').iterdir()), ['.write-test'])

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

    def test_edge_profile_uses_a_short_localappdata_path(self):
        # Edge の描画プロセスは、プロファイルのパスが深いと起動できない。
        # 凍結アプリの既定はアプリのフォルダではなく、短い LOCALAPPDATA の下。
        with tempfile.TemporaryDirectory() as local, tempfile.TemporaryDirectory() as app:
            with patch.dict('os.environ', {'LOCALAPPDATA': local}):
                profile = app_paths.edge_profile_dir()
            self.assertEqual(profile, Path(local) / 'PLATEAU-Blender-Bridge' / 'edge-profile')
            self.assertLess(len(str(profile)), 100)
            self.assertFalse(str(profile).startswith(app))

    def test_edge_profile_falls_back_to_temp_without_localappdata(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict('os.environ', {'LOCALAPPDATA': ''}), \
                    patch.object(app_paths.tempfile, 'gettempdir', return_value=td):
                self.assertEqual(app_paths.edge_profile_dir(),
                                 Path(td) / 'PLATEAU-Blender-Bridge' / 'edge-profile')


if __name__ == '__main__':
    unittest.main()
