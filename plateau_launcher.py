"""Frozen application entrypoint; never installs Python or executes tests at startup."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import traceback
from datetime import datetime


def self_check(destination: Path) -> int:
    """Headless installed-bundle test, safe on Windows CI without a display server."""
    try:
        from PIL import Image
        from pyproj import Transformer, datadir
        import tkinter as tk
        from plateau_blend import pipeline
        import plateau_blend
        module_dir=Path(plateau_blend.__file__).parent
        files=['blender_scene.py','blender_verify.py']
        missing=[f for f in files if not (module_dir/f).is_file()]
        coords=Transformer.from_crs(4326,6674,always_xy=True).transform(135.43,34.82)
        tcl_version=tk.Tcl().eval('info patchlevel')
        from plateau_blend.discovery import bundled_nusamai
        nusamai=bundled_nusamai()
        status={'passed':bool(nusamai) and not missing and all(map(lambda x:abs(x)<1e7,coords)),
                'bundled_nusamai':str(nusamai) if nusamai else None,
                'bundled_blender_scripts':files,'missing_scripts':missing,
                'proj_data_found':(Path(datadir.get_data_dir())/'proj.db').is_file(),
                'image_library':'Pillow','tcl_version':tcl_version,'coordinate_sample':coords}
        status['passed'] = bool(status['passed'] and status['proj_data_found'])
    except BaseException as exc:
        status={'passed':False,'error':repr(exc),'trace':traceback.format_exc()}
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if status['passed'] else 1


def report_crash(error: BaseException) -> None:
    home=Path(os.getenv('LOCALAPPDATA') or Path.home())/'PLATEAU-Blender-Bridge'/'logs'
    try:
        home.mkdir(parents=True,exist_ok=True)
        destination=home/f'crash-{datetime.now():%Y%m%d-%H%M%S}.log'
        destination.write_text(traceback.format_exc(),encoding='utf-8')
        from tkinter import messagebox
        messagebox.showerror('PLATEAU → Blender 起動エラー',
                             f'{type(error).__name__}: {error}\n\n詳細ログ: {destination}')
    except Exception:
        pass


def main(args: list[str] | None = None) -> int:
    if args is None:args=sys.argv[1:]
    try:
        if args[:1]==['--self-check']:
            if len(args)!=2:raise ValueError('--self-check requires output JSON path')
            return self_check(Path(args[1]))
        if args[:1]==['--manual']:
            from plateau_blend.gui import main as gui_main
            gui_main()
        elif args[:1]==['--cli']:
            from plateau_blend.auto_cli import main as cli_main
            return int(cli_main(args[1:]) or 0)
        elif args:
            raise ValueError('Unknown argument: '+args[0])
        else:
            from plateau_blend.auto_gui import main as auto_main
            auto_main()
    except Exception as exc:
        report_crash(exc)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
