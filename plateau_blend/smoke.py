"""Local-only Blender smoke test: no PLATEAU API, no tiles, no nusamai needed.

This intentionally uses the *production* scene writer and reopen verifier rather
than a mock; a small result remains for inspection and troubleshooting.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw
from .config import Config
from .pipeline import run
from .discovery import find_blender


def make_smoke_inputs(root:Path, blender:Path) -> Config:
    root.mkdir(parents=True,exist_ok=False)  # never overwrite previous diagnostics
    # Intentionally large projected coordinates to test float32 rebasing.
    ox,oy=500000.0,130000.0
    building=root/'building.obj'
    terrain=root/'terrain.obj'
    building.write_text('mtllib building.mtl\n'
                        f'v {ox+16} {oy+16} 5\n'
                        f'v {ox+36} {oy+16} 5\n'
                        f'v {ox+36} {oy+36} 15\n'
                        f'v {ox+16} {oy+36} 15\n'
                        'usemtl facade\nf 1 2 3 4\n',encoding='utf-8')
    (root/'building.mtl').write_text('newmtl facade\nKd 0.7 0.3 0.2\nmap_Kd facade.png\n',encoding='utf-8')
    Image.new('RGB',(8,8),(180,70,60)).save(root/'facade.png')
    terrain.write_text(f'v {ox} {oy} 0\nv {ox+50} {oy} 0\n'
                       f'v {ox+50} {oy+50} 0\nv {ox} {oy+50} 0\n'
                       'f 1 2 3 4\n',encoding='utf-8')
    png=root/'aerial.png'
    im=Image.new('RGB',(64,64),(110,150,110))
    dr=ImageDraw.Draw(im);dr.rectangle((14,14,47,47),fill=(150,155,155))
    im.save(png)
    # Pixel centres; north-up E<0; covers terrain envelope exactly.
    pgw=root/'aerial.pgw'
    step=50.0/64
    pgw.write_text(f'{step}\n0\n0\n{-step}\n{ox+step/2}\n{oy+50-step/2}\n',encoding='utf-8')
    return Config(blender,(building,),(terrain,),png,pgw,'EPSG:6674','EPSG:6674',root/'smoke.blend',
                  imagery_attribution='PLATEAU Blender Bridge local synthetic smoke image',
                  imagery_terms_url='https://example.org/synthetic-data',pack_image=True)


def run_smoke(*, blender:Path|None=None, output_parent:Path|None=None, log=print, cancel=None) -> Path:
    binary=blender or find_blender()
    if binary is None or not binary.is_file():
        raise FileNotFoundError('Blender本体が検出できません。--blender を指定してね')
    parent=(output_parent or (Path.cwd()/'diagnostics')).resolve()
    parent.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    cfg=make_smoke_inputs(parent/f'blender_smoke_{stamp}',binary)
    log('[SMOKE] 外部データを取得せず、合成OBJ・PNG・PGWでBlender保存と再読込を検証')
    return run(cfg,log=log,cancel=cancel)
