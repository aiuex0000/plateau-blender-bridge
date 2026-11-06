"""Files that go beside the built exe: user docs and third-party license notices.

``plateau_bridge.spec`` imports this module while PyInstaller assembles the
onedir bundle. Every failure raises so a bundle never ships without its
documentation or license texts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
import re
import shutil
import sys

LICENSE_FILENAME = 'THIRD_PARTY_LICENSES.txt'
SEPARATOR = '=' * 78
SUB_SEPARATOR = '-' * 78

PYTHON_URL = 'https://docs.python.org/3/license.html'
PILLOW_URL = 'https://github.com/python-pillow/Pillow/blob/main/LICENSE'
PYPROJ_URL = 'https://github.com/pyproj4/pyproj/blob/main/LICENSE'
PROJ_URL = 'https://github.com/OSGeo/PROJ/blob/master/COPYING'
CERTIFI_URL = 'https://github.com/certifi/python-certifi/blob/master/LICENSE'
TCLTK_URL = 'https://www.tcl.tk/software/tcltk/license.html'
NUSAMAI_URL = 'https://github.com/MIERUNE/nusamai'
PYINSTALLER_URL = 'https://pyinstaller.org/en/stable/license.html'


class DistExtrasError(RuntimeError):
    """A required document or license text is missing from the build."""


@dataclass
class LicenseSource:
    """One bundled component and the license file(s) that must ship with it."""

    name: str
    version: str
    license: str
    url: str
    files: list[Path] = field(default_factory=list)


def copy_user_docs(src_dir: Path, dist_app_dir: Path) -> list[Path]:
    """Copy the user documentation tree beside the exe, overwriting old copies.

    Returns the top-level destinations (files and folders) that were written.
    """
    src_dir = Path(src_dir)
    dist_app_dir = Path(dist_app_dir)
    if not src_dir.is_dir():
        raise DistExtrasError(f'説明書フォルダがありません: {src_dir}')
    if not any(path.is_file() and path.suffix.lower() == '.txt'
               for path in src_dir.rglob('*')):
        raise DistExtrasError(f'説明書 (.txt) が1つもありません: {src_dir}')

    dist_app_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for entry in sorted(src_dir.iterdir(), key=lambda path: path.name):
        destination = dist_app_dir / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, destination)
        copied.append(destination)
    return copied


def write_third_party_licenses(dist_app_dir: Path, sources: list[LicenseSource],
                               extra_notices: Path) -> Path:
    """Write ``THIRD_PARTY_LICENSES.txt`` (UTF-8 without BOM, CRLF line endings)."""
    dist_app_dir = Path(dist_app_dir)
    extra_notices = Path(extra_notices)
    if not extra_notices.is_file():
        raise DistExtrasError(f'追加の表示ファイルがありません: {extra_notices}')

    missing = [str(path) for source in sources for path in source.files
               if not Path(path).is_file()]
    if missing:
        raise DistExtrasError('同梱するライセンス原文が見つかりません: ' + ', '.join(missing))

    lines = [
        'このフォルダには次のソフトウェアが同梱されています。',
        '各ライセンスの原文を下に収録します。',
        '',
        '同梱ソフトウェア',
    ]
    for source in sources:
        lines.append(f'- {source.name} {source.version} — {source.license} — {source.url}')

    for source in sources:
        if not source.files:
            lines += ['', SEPARATOR, f'{source.name} {source.version}',
                      '原文は「追加の表示」に収録しています。']
            continue
        for path in source.files:
            path = Path(path)
            lines += ['', SEPARATOR, f'{source.name} {source.version}', path.name, SUB_SEPARATOR]
            lines += _read_text(path).split('\n')

    lines += ['', SEPARATOR, '追加の表示', SUB_SEPARATOR]
    lines += _read_text(extra_notices).split('\n')

    dist_app_dir.mkdir(parents=True, exist_ok=True)
    output = dist_app_dir / LICENSE_FILENAME
    with output.open('w', encoding='utf-8', newline='') as handle:
        handle.write('\r\n'.join(lines) + '\r\n')
    return output


def default_sources(build_python_prefix: Path, site_packages: Path, root: Path,
                    dist_app_dir: Path) -> list[LicenseSource]:
    """Describe the components that ship inside the onedir bundle."""
    build_python_prefix = Path(build_python_prefix)
    site_packages = Path(site_packages)
    root = Path(root)
    dist_app_dir = Path(dist_app_dir)

    sources = [
        LicenseSource(
            'Python', _python_version(),
            'PSF License（Windows binary の追加条件を含む）', PYTHON_URL,
            [build_python_prefix / 'LICENSE.txt']),
        LicenseSource(
            'Pillow', _package_version('Pillow'),
            'MIT-CMU（同梱の FreeType, libjpeg-turbo, libpng, XZ 等を含む）', PILLOW_URL,
            [_dist_info_file(site_packages, 'pillow-*.dist-info', 'licenses/LICENSE')]),
        LicenseSource(
            'pyproj', _package_version('pyproj'), 'MIT', PYPROJ_URL,
            [_dist_info_file(site_packages, 'pyproj-*.dist-info', 'licenses/LICENSE')]),
        LicenseSource(
            'PROJ', _proj_version(), 'MIT', PROJ_URL,
            [_dist_info_file(site_packages, 'pyproj-*.dist-info', 'licenses/LICENSE_proj')]),
        LicenseSource(
            'certifi', _package_version('certifi'), 'MPL-2.0', CERTIFI_URL,
            [_dist_info_file(site_packages, 'certifi-*.dist-info', 'licenses/LICENSE')]),
        LicenseSource(
            'Tcl/Tk', _tcl_tk_version(dist_app_dir), 'Tcl/Tk License', TCLTK_URL,
            [dist_app_dir / '_internal' / '_tk_data' / 'license.terms']),
    ]

    # spec と同じく exe の有無で同梱を決める。exe があって原文が無ければ、
    # write_third_party_licenses の欠落検査でビルドを止める。
    nusamai_dir = root / 'tools' / 'nusamai'
    nusamai_license = nusamai_dir / 'LICENSE-nusamai.txt'
    if (nusamai_dir / 'nusamai.exe').is_file():
        sources.append(LicenseSource(
            'nusamai', _nusamai_version(root), 'MIT', NUSAMAI_URL, [nusamai_license]))

    sources.append(LicenseSource(
        'PyInstaller（bootloader）', _package_version('pyinstaller'),
        'GPL-2.0-or-later WITH Bootloader-exception', PYINSTALLER_URL,
        [_pyinstaller_license(site_packages)]))
    return sources


def _read_text(path: Path) -> str:
    text = Path(path).read_text(encoding='utf-8', errors='replace')
    return text.replace('\r\n', '\n').replace('\r', '\n').strip('\n')


def _dist_info(site_packages: Path, pattern: str) -> Path:
    matches = sorted(Path(site_packages).glob(pattern))
    if len(matches) != 1:
        raise DistExtrasError(
            f'{pattern} が {len(matches)} 個あります（1個である必要があります）: {site_packages}')
    return matches[0]


def _dist_info_file(site_packages: Path, pattern: str, relative: str) -> Path:
    return _dist_info(site_packages, pattern) / relative


def _pyinstaller_license(site_packages: Path) -> Path:
    dist_info = _dist_info(site_packages, 'pyinstaller-*.dist-info')
    for relative in ('licenses/COPYING.txt', 'COPYING.txt'):
        candidate = dist_info / relative
        if candidate.is_file():
            return candidate
    return dist_info / 'licenses/COPYING.txt'


def _package_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError as exc:
        raise DistExtrasError(
            f'ビルド環境に {distribution} がありません（版を読み取れません）') from exc


def _python_version() -> str:
    return '.'.join(str(part) for part in sys.version_info[:3])


def _proj_version() -> str:
    try:
        import pyproj
    except ImportError as exc:
        raise DistExtrasError('pyproj がビルド環境にありません（PROJの版を読み取れません）') from exc
    return pyproj.proj_version_str


def _tcl_tk_version(dist_app_dir: Path) -> str:
    """凍結版に入る Tcl/Tk はビルド環境の tkinter と同じ物なので、そこから版を読む。"""
    try:
        import tkinter
        return str(tkinter.Tcl().eval('info patchlevel'))
    except Exception:
        return '同梱版'


def _nusamai_version(root: Path) -> str:
    build_script = Path(root) / 'build_nusamai.bat'
    if build_script.is_file():
        text = build_script.read_text(encoding='utf-8', errors='replace')
        match = re.search(r'^\s*set\s+NUSAMAI_TAG\s*=\s*(\S+)', text, re.MULTILINE)
        if match:
            return match.group(1)
    return '同梱版'
