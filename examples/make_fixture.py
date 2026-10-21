"""Generate a harmless local test dataset with known XY/georeference.

Usage: py examples/make_fixture.py --blender "C:/.../blender.exe"
"""
from pathlib import Path
from argparse import ArgumentParser
import json
from PIL import Image, ImageDraw


def main():
    p=ArgumentParser()
    p.add_argument('--blender', required=True, help='Blender executable full path')
    p.add_argument('--out', default='example_out', help='Output folder for generated test data')
    args=p.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    origin_x,origin_y=550000,130000
    # Slightly sloped grid with building on same XY projected CRS.
    terrain=[]
    for iy in range(6):
        for ix in range(6):
            terrain.append((origin_x+ix*100,origin_y+iy*100,(ix+iy)*.3))
    lines=['# Synthetic terrain: XYZ metres (illustrative CRS only)']
    lines += [f'v {x} {y} {z:g}' for x,y,z in terrain]
    for iy in range(5):
        for ix in range(5):
            a=iy*6+ix+1;b=a+1;c=a+6;d=c+1
            lines += [f'f {a} {b} {c}', f'f {b} {d} {c}']
    (out/'dem.obj').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    def building(cx,cy,base,height,side):
        v=[(cx,cy,base),(cx+side,cy,base),(cx+side,cy+side,base),(cx,cy+side,base),
           (cx,cy,base+height),(cx+side,cy,base+height),(cx+side,cy+side,base+height),(cx,cy+side,base+height)]
        return v
    vertices=building(origin_x+150,origin_y+150,5,60,75)+building(origin_x+300,origin_y+270,6,90,100)
    faces=[]
    for offset in (0,8):
        for a,b,c,d in ((1,2,3,4),(5,8,7,6),(1,5,6,2),(2,6,7,3),(3,7,8,4),(4,8,5,1)):
            faces.append(f'f {a+offset} {b+offset} {c+offset} {d+offset}')
    (out/'buildings.obj').write_text('\n'.join(['# Synthetic two buildings']+[f'v {x} {y} {z:g}' for x,y,z in vertices]+faces)+'\n',encoding='utf-8')
    im=Image.new('RGB',(512,512),'#779a65')
    dr=ImageDraw.Draw(im)
    for x in range(0,512,50): dr.line((x,0,x,511),fill='#b1b5b0',width=8)
    for y in range(0,512,50): dr.line((0,y,511,y),fill='#b1b5b0',width=8)
    dr.rectangle((150,150,225,225),fill='#bbbbbb')
    dr.rectangle((300,270,400,370),fill='#b3a39b')
    im.save(out/'aerial.png')
    # Demonstration only: image rows advance northward (E positive).
    # Normal north-up QGIS exports typically have a *negative* E coefficient.
    (out/'aerial.pgw').write_text(f'1\n0\n0\n1\n{origin_x}\n{origin_y}\n',encoding='utf-8')
    cfg={
        'blender_executable':args.blender,
        'building_objs':['buildings.obj'], 'terrain_objs':['dem.obj'],
        'aerial_png':'aerial.png','world_file':'aerial.pgw',
        'epsg':'EPSG:6674','image_epsg':'EPSG:6674',
        'building_axis':'xyz','terrain_axis':'xyz',
        'output_blend':'demo.blend',
        'imagery_attribution':'合成テスト画像（OpenAIによるコード生成）',
        'imagery_terms_url':'https://example.com/synthetic-demo-not-gsi',
        'pack_image':True
    }
    (out/'config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Created:',out)
    print('Test: py -m plateau_blend.cli --config',out/'config.json','--preflight')

if __name__=='__main__':main()
