"""Experimental Phase 2 adapter: invokes the documented nusamai CLI safely.

CLI binary availability on Windows and generated OBJ CRS/axis are NOT verified here.
The output is not automatically fed into Phase 1.
"""
from __future__ import annotations
from pathlib import Path
import argparse
import subprocess
import sys


def command(binary: Path, inputs: list[Path], out_dir: Path, lod: str = 'textured_max_lod', *, split: bool = True) -> list[str]:
    if lod not in ('textured_max_lod', 'max_lod', 'min_lod'):
        raise ValueError('LODモードが不正')
    if not inputs or any(p.suffix.lower() != '.gml' for p in inputs):
        raise ValueError('CityGML入力は1件以上の.gmlファイル')
    # nusamai >= 0.1.19 recentres OBJ on its centroid by default; keep surveyed coordinates.
    return [str(binary), *(str(p) for p in inputs), '--sink', 'obj', '--output', str(out_dir),
            '-t', f'use_lod={lod}', '-o', 'limit_texture_resolution=true', '-o', f'split={str(split).lower()}',
            '-o', 'center_at_origin=false']


def yup_to_zup(path: Path) -> int:
    """Rewrite one nusamai OBJ in place from its Y-up [x, z, -y] layout to Z-up [x, y, z].

    This is a proper rotation (det +1): face winding and UVs stay valid, normals rotate alike.
    Only call on files inside our own converter output folder, never on user inputs.
    """
    tmp = path.with_name(path.name + '.zup.tmp')
    count = 0
    with path.open('r', encoding='utf-8-sig', errors='replace') as inp, tmp.open('w', encoding='utf-8', newline='\n') as out:
        for line in inp:
            if line.startswith(('v ', 'vn ')):
                p = line.split()
                a, b, c = map(float, p[1:4])
                p[1:4] = (format(a, '.12g'), format(-c, '.12g'), format(b, '.12g'))
                out.write(' '.join(p) + '\n')
                count += line.startswith('v ')
            else:
                out.write(line)
    tmp.replace(path)
    return count


def main(argv=None):
    p = argparse.ArgumentParser(description='GIS Converter nusamai -> OBJ (experimental)')
    p.add_argument('--nusamai', required=True, type=Path)
    p.add_argument('--gml', nargs='+', required=True, type=Path)
    p.add_argument('--output-dir', required=True, type=Path)
    p.add_argument('--lod', default='textured_max_lod', choices=['textured_max_lod','max_lod','min_lod'])
    opt = p.parse_args(argv)
    try:
        binary = opt.nusamai.resolve()
        inputs = [s.resolve() for s in opt.gml]
        out = opt.output_dir.resolve()
        if not binary.is_file() or not all(p.is_file() for p in inputs):
            raise FileNotFoundError('nusamai実行ファイル または GMLファイルがありません')
        if any(p == out or out in p.parents for p in inputs):
            raise ValueError('出力フォルダ内に元GMLがあります')
        if out.exists() and any(out.iterdir()):
            raise FileExistsError('既存の非空ディレクトリを上書きしません: ' + str(out))
        out.mkdir(parents=True, exist_ok=True)
        cmd = command(binary, inputs, out, opt.lod)
        print('Running: ' + subprocess.list2cmdline(cmd), flush=True)
        subprocess.run(cmd, check=True)
        print('完成したOBJのCRS/座標順は実測確認してからPhase1へ入力してください')
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('[ERROR] ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
