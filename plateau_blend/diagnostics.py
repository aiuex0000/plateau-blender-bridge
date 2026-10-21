"""Non-destructive diagnostics for a user's installed local dependencies.

Explicit checks are opt-in: simply starting the GUI does not execute binaries.
"""
from __future__ import annotations
import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path
from .discovery import find_blender, find_nusamai


def _version(binary: Path | None) -> dict:
    if binary is None or not binary.is_file():
        return {'found': False, 'path': str(binary) if binary else None}
    try:
        result=subprocess.run([str(binary),'--version'],capture_output=True,
                              text=True,encoding='utf-8',errors='replace',timeout=15,check=False)
        return {'found':True,'path':str(binary),'returncode':result.returncode,
                'banner':(result.stdout or result.stderr).splitlines()[:4]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'found':True,'path':str(binary),'error':str(exc)}


def inspect_environment(*, blender:Path|None=None, nusamai:Path|None=None,
                        output:Path|None=None, check_versions:bool=True) -> dict:
    blender=blender or find_blender()
    nusamai=nusamai or find_nusamai()
    report={'python':sys.version.split()[0],
            'blender':_version(blender) if check_versions else {'found':bool(blender and blender.is_file()),'path':str(blender) if blender else None},
            'nusamai':_version(nusamai) if check_versions else {'found':bool(nusamai and nusamai.is_file()),'path':str(nusamai) if nusamai else None}}
    for package in ('Pillow','pyproj'):
        try: report[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: report[package]=None
    if output:
        root=output.expanduser().resolve().parent
        while not root.exists() and root != root.parent:root=root.parent
        report['output_parent']=str(root)
        report['free_space_gib']=round(shutil.disk_usage(root).free/(1024**3),2)
    return report
