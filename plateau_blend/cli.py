"""CLI for preflight and Blender generation."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .config import Config
from .pipeline import run, validate, report


def main(argv=None):
    parser = argparse.ArgumentParser(description='PLATEAU OBJ + DEM + PNG/PGW => .blend')
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--preflight', action='store_true', help='Blenderを起動せず座標などを検査')
    parser.add_argument('--overwrite-output', action='store_true', help='既存 .blend を明示的に置換')
    opts = parser.parse_args(argv)
    try:
        cfg = Config.load(opts.config)
        if opts.preflight:
            print(json.dumps(report(validate(cfg)), ensure_ascii=False, indent=2))
        else:
            run(cfg, overwrite_output=opts.overwrite_output)
    except Exception as exc:
        print(f'[ERROR] {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
