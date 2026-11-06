import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / 'packaging' / 'dist_extras.py'


def _load_dist_extras():
    spec = importlib.util.spec_from_file_location('dist_extras', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault('dist_extras', module)
    spec.loader.exec_module(module)
    return module


dist_extras = _load_dist_extras()


def _build_dependencies_available():
    try:
        import PIL  # noqa: F401
        import pyproj  # noqa: F401
        from importlib import metadata
        metadata.version('pyinstaller')
    except Exception:
        return False
    return True


HAVE_BUILD_DEPENDENCIES = _build_dependencies_available()


class WriteThirdPartyLicensesTests(unittest.TestCase):
    def test_writes_listing_bodies_and_extra_notices_with_crlf(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            license_file = base / 'LICENSE-alpha.txt'
            license_file.write_text('Alpha license line 1\nline 2\n', encoding='utf-8')
            extra = base / 'extra.txt'
            extra.write_text('Extra notice body\nsecond line\n', encoding='utf-8')
            sources = [dist_extras.LicenseSource(
                'Alpha', '1.2.3', 'MIT', 'https://example.com/alpha', [license_file])]

            output = dist_extras.write_third_party_licenses(base / 'dist', sources, extra)

            self.assertEqual(output, base / 'dist' / 'THIRD_PARTY_LICENSES.txt')
            raw = output.read_bytes()
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(raw.count(b'\r\n'), raw.count(b'\n'))
            text = raw.decode('utf-8')
            self.assertIn('このフォルダには次のソフトウェアが同梱されています。', text)
            self.assertIn('- Alpha 1.2.3 — MIT — https://example.com/alpha', text)
            self.assertIn('=' * 78, text)
            self.assertIn('Alpha 1.2.3', text)
            self.assertIn('LICENSE-alpha.txt', text)
            self.assertIn('Alpha license line 1', text)
            self.assertIn('追加の表示', text)
            self.assertIn('Extra notice body', text)

    def test_source_without_files_points_at_extra_notices(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            extra = base / 'extra.txt'
            extra.write_text('Only here', encoding='utf-8')
            sources = [dist_extras.LicenseSource('Beta', '2.0', 'BSD', 'https://example.com/beta')]

            text = dist_extras.write_third_party_licenses(
                base / 'dist', sources, extra).read_text(encoding='utf-8')

            self.assertIn('原文は「追加の表示」に収録しています。', text)
            self.assertIn('Only here', text)

    def test_missing_license_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            extra = base / 'extra.txt'
            extra.write_text('x', encoding='utf-8')
            sources = [dist_extras.LicenseSource(
                'Alpha', '1.0', 'MIT', 'https://example.com', [base / 'missing.txt'])]

            with self.assertRaises(dist_extras.DistExtrasError):
                dist_extras.write_third_party_licenses(base / 'dist', sources, extra)

    def test_missing_extra_notices_raises(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(dist_extras.DistExtrasError):
                dist_extras.write_third_party_licenses(
                    base / 'dist', [], base / 'missing-extra.txt')


class CopyUserDocsTests(unittest.TestCase):
    def test_copies_files_and_folders_and_overwrites(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / 'user_docs'
            src.mkdir()
            (src / 'はじめにお読みください.txt').write_text('準備中', encoding='utf-8')
            (src / 'screenshots').mkdir()
            (src / 'screenshots' / 'a.png').write_bytes(b'png')
            app = base / 'dist' / 'app'

            copied = dist_extras.copy_user_docs(src, app)

            self.assertTrue((app / 'はじめにお読みください.txt').is_file())
            self.assertTrue((app / 'screenshots' / 'a.png').is_file())
            self.assertEqual(len(copied), 2)

            (src / 'はじめにお読みください.txt').write_text('更新', encoding='utf-8')
            dist_extras.copy_user_docs(src, app)
            self.assertEqual(
                (app / 'はじめにお読みください.txt').read_text(encoding='utf-8'), '更新')

    def test_requires_at_least_one_txt_file(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / 'user_docs'
            src.mkdir()
            (src / 'readme.md').write_text('x', encoding='utf-8')

            with self.assertRaises(dist_extras.DistExtrasError):
                dist_extras.copy_user_docs(src, base / 'app')


@unittest.skipUnless(HAVE_BUILD_DEPENDENCIES,
                     'Pillow / pyproj / PyInstaller are not installed')
class DefaultSourcesTests(unittest.TestCase):
    def _fake_license(self, site_packages, dist_info, relative):
        target = site_packages / dist_info / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'{dist_info} {relative}', encoding='utf-8')
        return target

    def test_lists_real_files_and_skips_absent_nusamai(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            prefix = base / 'python'
            prefix.mkdir()
            (prefix / 'LICENSE.txt').write_text('PSF license', encoding='utf-8')
            site_packages = base / 'site-packages'
            site_packages.mkdir()
            self._fake_license(site_packages, 'pillow-99.0.dist-info', 'licenses/LICENSE')
            self._fake_license(site_packages, 'pyproj-99.0.dist-info', 'licenses/LICENSE')
            self._fake_license(site_packages, 'pyproj-99.0.dist-info', 'licenses/LICENSE_proj')
            self._fake_license(site_packages, 'certifi-99.0.dist-info', 'licenses/LICENSE')
            self._fake_license(site_packages, 'pyinstaller-99.0.dist-info', 'licenses/COPYING.txt')
            dist_app = base / 'app'
            tk_data = dist_app / '_internal' / '_tk_data'
            tk_data.mkdir(parents=True)
            (tk_data / 'license.terms').write_text('Tcl license', encoding='utf-8')
            (tk_data / 'tcl8.6').mkdir()

            sources = dist_extras.default_sources(prefix, site_packages, base / 'root', dist_app)
            by_name = {source.name: source for source in sources}

            for name in ('Python', 'Pillow', 'pyproj', 'PROJ', 'certifi', 'Tcl/Tk',
                         'PyInstaller（bootloader）'):
                self.assertIn(name, by_name)
            self.assertNotIn('nusamai', by_name)
            self.assertRegex(by_name['Tcl/Tk'].version, r'^8\.6(\.\d+)?$')
            self.assertEqual(len([source for source in sources if source.name == 'PROJ']), 1)
            for source in sources:
                for path in source.files:
                    self.assertTrue(path.is_file(), path)

    def test_includes_nusamai_when_its_exe_is_bundled(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            prefix = base / 'python'
            prefix.mkdir()
            (prefix / 'LICENSE.txt').write_text('PSF license', encoding='utf-8')
            site_packages = base / 'site-packages'
            site_packages.mkdir()
            self._fake_license(site_packages, 'pillow-99.0.dist-info', 'licenses/LICENSE')
            self._fake_license(site_packages, 'pyproj-99.0.dist-info', 'licenses/LICENSE')
            self._fake_license(site_packages, 'pyproj-99.0.dist-info', 'licenses/LICENSE_proj')
            self._fake_license(site_packages, 'certifi-99.0.dist-info', 'licenses/LICENSE')
            self._fake_license(site_packages, 'pyinstaller-99.0.dist-info', 'licenses/COPYING.txt')
            dist_app = base / 'app'
            tk_data = dist_app / '_internal' / '_tk_data'
            tk_data.mkdir(parents=True)
            (tk_data / 'license.terms').write_text('Tcl license', encoding='utf-8')
            root = base / 'root'
            nusamai_dir = root / 'tools' / 'nusamai'
            nusamai_dir.mkdir(parents=True)
            (nusamai_dir / 'nusamai.exe').write_bytes(b'')
            (nusamai_dir / 'LICENSE-nusamai.txt').write_text('MIT', encoding='utf-8')
            (root / 'build_nusamai.bat').write_text(
                'set NUSAMAI_TAG=v9.9.9\n', encoding='utf-8')

            sources = dist_extras.default_sources(prefix, site_packages, root, dist_app)
            nusamai = [source for source in sources if source.name == 'nusamai']

            self.assertEqual(len(nusamai), 1)
            self.assertEqual(nusamai[0].version, 'v9.9.9')
            self.assertEqual(nusamai[0].files, [nusamai_dir / 'LICENSE-nusamai.txt'])

    def test_bundled_nusamai_without_license_stops_the_build(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            prefix = base / 'python'
            prefix.mkdir()
            (prefix / 'LICENSE.txt').write_text('PSF license', encoding='utf-8')
            site_packages = base / 'site-packages'
            site_packages.mkdir()
            for dist_info, relative in (('pillow-99.0.dist-info', 'licenses/LICENSE'),
                                        ('pyproj-99.0.dist-info', 'licenses/LICENSE'),
                                        ('pyproj-99.0.dist-info', 'licenses/LICENSE_proj'),
                                        ('certifi-99.0.dist-info', 'licenses/LICENSE'),
                                        ('pyinstaller-99.0.dist-info', 'licenses/COPYING.txt')):
                self._fake_license(site_packages, dist_info, relative)
            dist_app = base / 'app'
            tk_data = dist_app / '_internal' / '_tk_data'
            tk_data.mkdir(parents=True)
            (tk_data / 'license.terms').write_text('Tcl license', encoding='utf-8')
            root = base / 'root'
            nusamai_dir = root / 'tools' / 'nusamai'
            nusamai_dir.mkdir(parents=True)
            (nusamai_dir / 'nusamai.exe').write_bytes(b'')
            extra = base / 'extra.txt'
            extra.write_text('x', encoding='utf-8')

            sources = dist_extras.default_sources(prefix, site_packages, root, dist_app)

            self.assertIn('nusamai', [source.name for source in sources])
            with self.assertRaises(dist_extras.DistExtrasError):
                dist_extras.write_third_party_licenses(base / 'out', sources, extra)


if __name__ == '__main__':
    unittest.main()
