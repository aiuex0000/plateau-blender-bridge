"""Non-GUI automation entrypoint; useful for Luna's Windows logging."""
from pathlib import Path
from datetime import datetime
import argparse
import sys
import json
from . import __version__
from .auto import AutoRequest,generate,preview,projected_selection
from .diagnostics import inspect_environment
from .resources import forecast, enforce_budget
from .smoke import run_smoke

def _now():return datetime.now().astimezone().isoformat(timespec='seconds')

def _mode(args):
    if args.diagnose:return 'diagnose'
    if args.smoke_test:return 'smoke'
    return 'preview' if args.preview else 'generate'

def _mode_from_flags(argv):
    if '--diagnose' in argv:return 'diagnose'
    if '--smoke-test' in argv:return 'smoke'
    return 'preview' if '--preview' in argv else 'generate'

def _flag_path(argv,flag):
    for i,a in enumerate(argv):
        if a==flag:return Path(argv[i+1]) if i+1<len(argv) else None
        if a.startswith(flag+'='):return Path(a.split('=',1)[1])
    return None

def _default_result(out):
    if out:return out.with_name(out.stem+'.result.json')
    from .app_paths import data_dir
    return data_dir()/'cli_result.json'

def _result_path(args,out):
    return args.result_json or _default_result(out)

def _verify_json(output):
    try:found=sorted(output.with_suffix('.work').glob('verify_*.json'),key=lambda p:p.stat().st_mtime)
    except OSError:return None
    return str(found[-1]) if found else None

def _payload(code,started,mode,data=None,output=None,verify=None,error=None):
    return {'ok':code==0,'exit_code':code,'mode':mode,'version':__version__,'started_at':started,
            'finished_at':_now(),'output':output,'verify_json':verify,'data':data,'error':error}

def _write_result(path,payload):
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as exc:
        print(f'[WARN] 結果JSONを書き出せません: {path}: {exc}',file=sys.stderr)

def main(argv=None):
    p=argparse.ArgumentParser(description='Automated PLATEAU API + GSI -> Blender')
    p.add_argument('--condition',required=False,help='r:west,south,east,north / m:mesh / g:place')
    p.add_argument('--epsg',default='AUTO',help='JGD2011 EPSG code or AUTO (bbox/mesh/place)')
    p.add_argument('--output',required=False,type=Path)
    p.add_argument('--blender',type=Path)
    p.add_argument('--nusamai',type=Path)
    p.add_argument('--lod',default='textured_max_lod',choices=['min_lod','max_lod','textured_max_lod'])
    p.add_argument('--zoom',type=int,default=18)
    p.add_argument('--dem-resolution',type=int,default=100)
    p.add_argument('--preview',action='store_true')
    p.add_argument('--smoke-test',action='store_true',help='locally build and reopen a tiny .blend using real Blender; no network')
    p.add_argument('--smoke-dir',type=Path,help='directory to retain smoke-test inputs and logs')
    p.add_argument('--diagnose',action='store_true',help='report installed Blender/CLI versions and free disk space')
    p.add_argument('--no-clip',action='store_true',help='include full source mesh, skip bbox crop')
    p.add_argument('--axis',choices=['auto','xyz','yxz'],default='auto')
    p.add_argument('--result-json',type=Path,help='machine-readable result JSON (default: next to --output, else data/cli_result.json next to the app)')
    started=_now()
    try:args=p.parse_args(argv)
    except SystemExit as exc:
        code=exc.code if isinstance(exc.code,int) and exc.code else 0
        if not code:return 0
        raw=list(sys.argv[1:] if argv is None else argv)
        out=_flag_path(raw,'--output')
        out=None if out is None else out.resolve()
        _write_result(_flag_path(raw,'--result-json') or _default_result(out),
                      _payload(code,started,_mode_from_flags(raw),output=None if out is None else str(out),
                               error={'type':'ArgumentError','message':f'argument error (exit {code})'}))
        return code
    out=None if args.output is None else args.output.resolve()
    state={'data':None,'output':None if out is None else str(out),'verify_json':None,'error':None}
    try:code=_run(args,p,state)
    except SystemExit as exc:
        if exc.code in (None,0):return 0
        code=exc.code if isinstance(exc.code,int) else 1
        state['error']={'type':'ArgumentError','message':f'argument error (exit {code})'}
    except Exception as exc:
        code=1
        state['error']={'type':type(exc).__name__,'message':str(exc)}
        print(f'[ERROR] {type(exc).__name__}: {exc}',file=sys.stderr)
    _write_result(_result_path(args,out),_payload(code,started,_mode(args),state['data'],state['output'],
                                                  state['verify_json'],state['error']))
    return code

def _run(args,p,state):
    if args.diagnose:
        state['data']=inspect_environment(blender=args.blender,nusamai=args.nusamai,output=args.output)
        print(json.dumps(state['data'],ensure_ascii=False,indent=2))
        return 0
    if args.smoke_test:
        try:state['data']=str(run_smoke(blender=args.blender,output_parent=args.smoke_dir))
        except Exception as exc:
            state['error']={'type':type(exc).__name__,'message':str(exc)}
            print(f'[SMOKE ERROR] {type(exc).__name__}: {exc}',file=sys.stderr)
            return 1
        print('[SMOKE OK] '+state['data'])
        return 0
    if not args.condition or args.output is None:
        p.error('--condition and --output required unless --diagnose is used')
    try:
        req=AutoRequest(args.condition,args.epsg,args.output.resolve(),args.blender,args.nusamai,
                        args.lod,args.zoom,args.dem_resolution,axis_override=args.axis,clip_requested_area=not args.no_clip)
        if args.preview:
            sources=preview(req)
            state['data']=[s.__dict__ for s in sources]
            for s in sources:print(json.dumps(s.__dict__,ensure_ascii=False))
            if req.condition.startswith('r:'):
                xy=projected_selection(req.condition,req.effective_epsg(sources))
                if xy:
                    plan=forecast(xy,req.effective_epsg(sources),wanted_zoom=req.photo_zoom,dem_resolution=req.terrain_resolution)
                    enforce_budget(plan)
                    print(json.dumps({'resource_plan':plan.as_dict()},ensure_ascii=False))
        else:
            result=generate(req)
            if result is not None:
                state['output']=str(result);state['verify_json']=_verify_json(Path(result))
    except Exception as exc:
        state['error']={'type':type(exc).__name__,'message':str(exc)}
        print(f'[ERROR] {type(exc).__name__}: {exc}',file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())
