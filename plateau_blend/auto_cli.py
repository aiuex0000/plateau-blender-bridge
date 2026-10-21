"""Non-GUI automation entrypoint; useful for Luna's Windows logging."""
from pathlib import Path
import argparse
import sys
import json
from .auto import AutoRequest,generate,preview,projected_selection
from .diagnostics import inspect_environment
from .resources import forecast, enforce_budget
from .smoke import run_smoke

def main(argv=None):
    p=argparse.ArgumentParser(description='Automated PLATEAU API + GSI -> Blender')
    p.add_argument('--condition',required=False,help='r:west,south,east,north / m:mesh / g:place')
    p.add_argument('--epsg',default='AUTO',help='JGD2011 EPSG code or AUTO (bbox/mesh/place)')
    p.add_argument('--output',required=False,type=Path)
    p.add_argument('--blender',type=Path)
    p.add_argument('--nusamai',type=Path)
    p.add_argument('--lod',default='max_lod',choices=['min_lod','max_lod','textured_max_lod'])
    p.add_argument('--zoom',type=int,default=16)
    p.add_argument('--dem-resolution',type=int,default=100)
    p.add_argument('--preview',action='store_true')
    p.add_argument('--smoke-test',action='store_true',help='locally build and reopen a tiny .blend using real Blender; no network')
    p.add_argument('--smoke-dir',type=Path,help='directory to retain smoke-test inputs and logs')
    p.add_argument('--diagnose',action='store_true',help='report installed Blender/CLI versions and free disk space')
    p.add_argument('--no-clip',action='store_true',help='include full source mesh, skip bbox crop')
    p.add_argument('--axis',choices=['auto','xyz','yxz'],default='auto')
    args=p.parse_args(argv)
    if args.diagnose:
        print(json.dumps(inspect_environment(blender=args.blender,nusamai=args.nusamai,output=args.output),ensure_ascii=False,indent=2))
        return 0
    if args.smoke_test:
        try:
            result=run_smoke(blender=args.blender,output_parent=args.smoke_dir)
            print('[SMOKE OK] '+str(result))
        except Exception as exc:
            print(f'[SMOKE ERROR] {type(exc).__name__}: {exc}',file=sys.stderr)
            return 1
        return 0
    if not args.condition or args.output is None:
        p.error('--condition and --output required unless --diagnose is used')
    req=AutoRequest(args.condition,args.epsg,args.output.resolve(),args.blender,args.nusamai,
                    args.lod,args.zoom,args.dem_resolution,axis_override=args.axis,clip_requested_area=not args.no_clip)
    try:
        if args.preview:
            sources=preview(req)
            for s in sources:print(json.dumps(s.__dict__,ensure_ascii=False))
            if req.condition.startswith('r:'):
                xy=projected_selection(req.condition,req.effective_epsg(sources))
                if xy:
                    plan=forecast(xy,req.effective_epsg(sources),wanted_zoom=req.photo_zoom,dem_resolution=req.terrain_resolution)
                    enforce_budget(plan)
                    print(json.dumps({'resource_plan':plan.as_dict()},ensure_ascii=False))
        else:generate(req)
    except Exception as exc:
        print(f'[ERROR] {type(exc).__name__}: {exc}',file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
