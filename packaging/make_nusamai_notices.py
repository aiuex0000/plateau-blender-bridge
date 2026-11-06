"""Regenerate tools/nusamai/THIRD_PARTY_LICENSES-nusamai.txt and MPL-SOURCE-CODE.zip.

Run after build_nusamai.bat, against the same patched checkout:

    python packaging/make_nusamai_notices.py .build-nusamai/src

The inventory follows the Windows (x86_64-pc-windows-msvc) normal-dependency graph of the
nusamai package in the checkout's Cargo.lock (``cargo metadata --locked``; no build scripts
run). Build-only and dev-only dependencies are not shipped and are left out. It stops when a
third-party package declares no license and ships no license file, or when kv-extsort is
still in the graph.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = 'x86_64-pc-windows-msvc'
LICENSE_NAME = re.compile(r'^(LICENSE|LICENCE|COPYING|NOTICE|COPYRIGHT|AUTHORS)(\.|$|[-_])', re.I)
BLOCKED = {'kv-extsort'}

# Published packages that declare MIT but ship no license file. The notice line and where it
# was taken from are recorded here; anything else without a file must offer Apache-2.0.
MIT_NOTICES = {
    'fax': ('Copyright © 2021 The pdf-rs contributors.',
            'upstream LICENSE, https://github.com/pdf-rs/fax'),
    'fax_derive': ('Copyright © 2021 The pdf-rs contributors.',
                   'upstream LICENSE, https://github.com/pdf-rs/fax'),
    'simd_helpers': ('Copyright (c) 2019 Luca Barbato',
                     'upstream LICENSE, https://github.com/lu-zero/simd_helpers'),
    'seahash': ('Copyright (c) ticki, Tom Almeida',
                'Cargo.toml authors; the upstream LICENSE could not be retrieved'),
    'jprect': ('Copyright (c) Taku Fukada',
               'crates.io owner; neither the package nor https://github.com/ciscorn/jprect-rs '
               'states a copyright line'),
}
MIT_TEXT = '''Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.'''


def cargo_metadata(src: Path) -> dict:
    out = subprocess.run(
        ['cargo', 'metadata', '--locked', '--offline', '--format-version', '1',
         '--filter-platform', TARGET, '--manifest-path', str(src / 'Cargo.toml')],
        check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(out.stdout)


def shipped_packages(meta: dict) -> list[dict]:
    pkgs = {p['id']: p for p in meta['packages']}
    nodes = {n['id']: n for n in meta['resolve']['nodes']}
    root = next(i for i in meta['workspace_members'] if pkgs[i]['name'] == 'nusamai')
    seen, todo = set(), [root]
    while todo:
        ident = todo.pop()
        if ident in seen:
            continue
        seen.add(ident)
        for dep in nodes[ident]['deps']:
            if any(k['kind'] is None for k in dep['dep_kinds']):
                todo.append(dep['pkg'])
    return sorted((pkgs[i] for i in seen), key=lambda p: (p['name'], p['version'], p['id']))


def license_files(pkg: dict) -> list[Path]:
    package_dir = Path(pkg['manifest_path']).parent
    found = set()
    if pkg.get('license_file'):
        found.add((package_dir / pkg['license_file']).resolve())
    for dirpath, dirnames, filenames in os.walk(package_dir):
        dirnames[:] = [d for d in dirnames if d not in ('target', '.git')]
        for name in filenames:
            if LICENSE_NAME.match(name):
                found.add((Path(dirpath) / name).resolve())
    return sorted(found, key=str)


def lock_checksum(lock_text: str, name: str, version: str) -> str | None:
    block = re.search(
        rf'(?ms)^\[\[package\]\]\s*\nname = "{re.escape(name)}"\s*\nversion = "{re.escape(version)}"'
        rf'\s*\n(?:(?!\[\[package\]\]).)*?^checksum = "([0-9a-f]+)"', lock_text)
    return block.group(1) if block else None


def _entry(name: str) -> zipfile.ZipInfo:
    """Fixed timestamp so an unchanged archive regenerates byte-for-byte."""
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description='Regenerate the bundled nusamai license notices.')
    ap.add_argument('src', type=Path, help='patched plateau-gis-converter checkout')
    ap.add_argument('--out', type=Path, default=ROOT / 'tools' / 'nusamai')
    opt = ap.parse_args()
    src = opt.src.resolve()
    commit = subprocess.run(['git', '-C', str(src), 'rev-parse', 'HEAD'], check=True,
                            capture_output=True, text=True).stdout.strip()
    lock_text = (src / 'Cargo.lock').read_text(encoding='utf-8')
    lock_sha = hashlib.sha256((src / 'Cargo.lock').read_bytes()).hexdigest()
    meta = cargo_metadata(src)
    members = set(meta['workspace_members'])
    pkgs = shipped_packages(meta)

    blocked = [p for p in pkgs if p['name'] in BLOCKED]
    if blocked or re.search(r'(?m)^name = "kv-extsort"$', lock_text):
        print('kv-extsort is still in the resolved graph / Cargo.lock', file=sys.stderr)
        return 1

    files = {p['id']: license_files(p) for p in pkgs}
    # A third-party package without its own license file is covered either by electing its
    # Apache-2.0 option (full text below) or by a recorded MIT notice; otherwise stop.
    apache_elected, mit_filled, unresolved = [], [], []
    for p in pkgs:
        if p['id'] in members or files[p['id']]:
            continue
        expr = p.get('license') or ''
        if 'Apache-2.0' in expr:
            apache_elected.append(p)
        elif expr == 'MIT' and p['name'] in MIT_NOTICES:
            mit_filled.append(p)
        else:
            unresolved.append(p)
    if unresolved:
        for p in unresolved:
            print(f"no license file and no recorded notice: {p['name']} {p['version']} "
                  f"({p.get('license')}; {p['source']})", file=sys.stderr)
        return 1
    apache_text = None
    if apache_elected:
        for p in pkgs:
            for f in files[p['id']]:
                text = f.read_text(encoding='utf-8', errors='replace')
                if 'Apache License' in text and 'TERMS AND CONDITIONS FOR USE, REPRODUCTION' in text:
                    apache_text = (p, f, text)
                    break
            if apache_text:
                break
        if apache_text is None:
            print('Apache-2.0 full text not found in the resolved sources', file=sys.stderr)
            return 1

    lines = [
        'nusamai v0.1.19 (PLATEAU Blender Bridge build) — Third-party license and notice inventory',
        '=' * 72,
        'Upstream source: https://github.com/MIERUNE/plateau-gis-converter/tree/v0.1.19',
        f'Source commit: {commit}',
        'Patches (PLATEAU Blender Bridge repository, patches/):',
        '  nusamai-v0.1.19-textured-lod-fallback.patch',
        '  nusamai-v0.1.19-drop-unused-sinks.patch  (removes the 3D Tiles, MVT, MLT and PMTiles',
        '  outputs and their dependencies, including kv-extsort, and the Tauri GUI workspace member)',
        f'Cargo.lock SHA-256 (patched): {lock_sha}',
        f'Resolution: cargo metadata --locked --filter-platform {TARGET}; normal dependencies',
        'reachable from the nusamai package with default features. Build-only and dev-only',
        'dependencies are not shipped and are not listed. Procedural-macro crates are listed',
        'conservatively although they run only at compile time.',
        'Package license expressions are Cargo metadata. Where a license/notice file was present in',
        'the resolved source, its text is reproduced below. The upstream workspace LICENSE (MIT;',
        'shipped separately as LICENSE-nusamai.txt) applies to the MIERUNE workspace packages.',
        '', 'PACKAGE INVENTORY', '-' * 72,
    ]
    for p in pkgs:
        if p.get('license'):
            lic = p['license']
        elif p['id'] in members:
            lic = 'MIT (upstream workspace LICENSE)'
        else:
            lic = 'not declared in Cargo.toml; license file reproduced below'
        lines.append(f"{p['name']} {p['version']} | license: {lic} | "
                     f"source: {p.get('source') or 'upstream workspace source'}")

    mpl = [p for p in pkgs if 'MPL' in (p.get('license') or '')]
    if mpl:
        lines += ['', 'RECIPROCAL LICENSES', '-' * 72,
                  'These packages are MPL-2.0. Their original, unmodified Cargo crate archives are in',
                  'MPL-SOURCE-CODE.zip with checksums verified against Cargo.lock. MPL-2.0 is file-level',
                  'reciprocal; the rest of the application is not thereby relicensed.']
        lines += [f"  {p['name']} {p['version']}" for p in mpl]

    lines += ['', 'LICENSE / NOTICE FILES', '=' * 72]
    for p in pkgs:
        for f in files[p['id']]:
            data = f.read_bytes()
            if b'\0' in data[:4096]:
                continue
            body = data.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
            body = '\n'.join(line.rstrip() for line in body.strip().split('\n'))
            lines += ['', '-' * 72, f"{p['name']} {p['version']} — {f.name}",
                      f'SHA-256 (original file): {hashlib.sha256(data).hexdigest()}', '-' * 72, body]

    if mit_filled or apache_elected:
        lines += ['', 'PACKAGES PUBLISHED WITHOUT A LICENSE FILE', '=' * 72,
                  'These packages declare their license in Cargo.toml but their published source has',
                  'no license file. The license text is supplied here.']
    for p in mit_filled:
        notice_line, origin = MIT_NOTICES[p['name']]
        lines += ['', '-' * 72, f"{p['name']} {p['version']} — MIT License",
                  f'Copyright notice source: {origin}', '-' * 72,
                  'MIT License', '', notice_line, '', MIT_TEXT]
    if apache_elected:
        assert apache_text is not None
        src_pkg, src_file, text = apache_text
        body = '\n'.join(line.rstrip() for line in text.replace('\r\n', '\n').strip().split('\n'))
        lines += ['', '-' * 72,
                  'Used under Apache License 2.0 (one of the offered licenses), full text below:']
        lines += [f"  {p['name']} {p['version']} ({p['license']})" for p in apache_elected]
        lines += [f"Text taken from {src_pkg['name']} {src_pkg['version']} — {src_file.name}",
                  '-' * 72, body]

    out = opt.out
    out.mkdir(parents=True, exist_ok=True)
    notice = out / 'THIRD_PARTY_LICENSES-nusamai.txt'
    notice.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    print(f'{notice}: {len(pkgs)} packages')

    zip_path = out / 'MPL-SOURCE-CODE.zip'
    if not mpl:
        if zip_path.exists():
            zip_path.unlink()
            print(f'{zip_path}: removed (no MPL package in the graph)')
        return 0
    cache_roots = sorted((Path.home() / '.cargo' / 'registry' / 'cache').glob('index.crates.io-*'))
    readme = ['MPL-2.0 dependency sources for the PLATEAU Blender Bridge build of nusamai v0.1.19.',
              'Original Cargo .crate archives, unmodified; SHA-256 values match Cargo.lock.', '']
    mpl_text = None
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in mpl:
            crate = next((c / f"{p['name']}-{p['version']}.crate" for c in cache_roots
                          if (c / f"{p['name']}-{p['version']}.crate").is_file()), None)
            if crate is None:
                print(f"missing Cargo archive for {p['name']} {p['version']}", file=sys.stderr)
                return 1
            digest = hashlib.sha256(crate.read_bytes()).hexdigest()
            if lock_checksum(lock_text, p['name'], p['version']) != digest:
                print(f"Cargo.lock checksum mismatch: {p['name']} {p['version']}", file=sys.stderr)
                return 1
            zf.writestr(_entry(crate.name), crate.read_bytes())
            readme.append(f"{crate.name}  SHA-256 {digest}  https://crates.io/crates/{p['name']}/{p['version']}")
            for f in files[p['id']]:
                text = f.read_text(encoding='utf-8', errors='replace')
                if 'Mozilla Public License' in text and '2.0' in text:
                    mpl_text = text
        if mpl_text is None:
            print('MPL-2.0 license text not found in the MPL packages', file=sys.stderr)
            return 1
        zf.writestr(_entry('README.txt'), '\n'.join(readme) + '\n')
        zf.writestr(_entry('MPL-2.0.txt'), mpl_text)
    print(f'{zip_path}: {len(mpl)} MPL archives')
    return 0


if __name__ == '__main__':
    sys.exit(main())
