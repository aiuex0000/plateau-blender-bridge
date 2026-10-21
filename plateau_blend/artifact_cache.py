"""Validated generated-artifact cache markers with atomic writes.

The cache is best-effort; corrupt or outdated entries are regenerated rather
than trusted. This is separate from source acquisition's immutable assets.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def valid(marker: Path, key: list, paths: list[Path]) -> bool:
    try:
        payload=json.loads(marker.read_text(encoding='utf-8'))
        if payload.get('schema')!=1 or payload.get('key')!=key:return False
        saved=payload.get('files')
        if not isinstance(saved,list) or len(saved)!=len(paths):return False
        for path, entry in zip(paths,saved):
            if not path.is_file() or path.is_symlink() or path.stat().st_size<=0:
                return False
            if entry != {'name':path.name,'size':path.stat().st_size,'sha256':digest(path)}:
                return False
        return True
    except (OSError,ValueError,TypeError,KeyError):
        return False


def mark(marker: Path, key: list, paths: list[Path], result: dict) -> None:
    entries=[]
    for path in paths:
        if not path.is_file() or path.stat().st_size==0:raise ValueError('キャッシュの入力成果物が空です')
        entries.append({'name':path.name,'size':path.stat().st_size,'sha256':digest(path)})
    payload={'schema':1,'key':key,'files':entries,'result':result}
    marker.parent.mkdir(parents=True,exist_ok=True)
    tmp=marker.with_name(marker.name+'.part')
    try:
        tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(marker)
    finally:
        tmp.unlink(missing_ok=True)
