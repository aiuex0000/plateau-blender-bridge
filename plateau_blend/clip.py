"""Offline OBJ XY clipping for geographic rectangle selection.

Faces are clipped by Sutherland-Hodgman in projected XY. Per-corner VT and
VN values are interpolated; winding and usemtl are preserved. This is a *surface
clip*: walls along the cut are not capped (not a watertight solid operation).
Only paths under the generated work folder are ever written.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import math
import shutil
import struct
import tempfile
import mmap
from contextlib import ExitStack
from .obj import _copy_mtl_assets, obj_dependencies
from .artifact_cache import valid as cache_valid, mark as cache_mark


class _DiskIndexedTriples:
    """Random-access float64 tuples using temporary disk storage, not Python lists.

    Stores coordinates in a compact binary table so multi-million-vertex OBJ
    clipping does not allocate a Python object for every coordinate.
    Binary buffers live on disk and are removed after the clip stage.
    """
    def __init__(self, stack: ExitStack, arity: int, directory: Path):
        self.arity = arity
        self._struct = struct.Struct('<' + 'd' * arity)
        self._file = stack.enter_context(tempfile.TemporaryFile(dir=directory))
        self._size = 0
        self._map = None
        stack.callback(self.close)

    def append(self, values: tuple[float, ...]):
        if len(values) != self.arity or not all(map(math.isfinite, values)):
            raise ValueError(f'OBJの{self.arity}要素座標が不正')
        self._file.write(self._struct.pack(*values))
        self._size += 1

    def freeze(self):
        self._file.flush()
        if self._size:
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)

    def __len__(self):
        return self._size

    def __getitem__(self, idx: int):
        if idx < 0 or idx >= self._size or self._map is None:
            raise IndexError(idx)
        return self._struct.unpack_from(self._map, idx * self._struct.size)

    def close(self):
        if self._map is not None:
            self._map.close()
            self._map = None


@dataclass(frozen=True)
class Corner:
    xyz: tuple[float, float, float]
    uv: tuple[float, float] | None = None
    normal: tuple[float, float, float] | None = None


def interpolate(a: Corner, b: Corner, t: float) -> Corner:
    t = min(1.0, max(0.0, t))
    def mix(l, r):
        return tuple(x + (y-x)*t for x, y in zip(l,r))
    return Corner(mix(a.xyz,b.xyz), mix(a.uv,b.uv) if a.uv is not None and b.uv is not None else None,
                  mix(a.normal,b.normal) if a.normal is not None and b.normal is not None else None)


def clip_polygon(points: list[Corner], bounds: tuple[float, float, float, float]) -> list[Corner]:
    xmin, ymin, xmax, ymax = bounds
    if not (all(math.isfinite(v) for v in bounds) and xmin < xmax and ymin < ymax):
        raise ValueError('XY切り出し範囲が不正')
    result = points
    for axis, boundary, keep_greater in ((0,xmin,True),(0,xmax,False),(1,ymin,True),(1,ymax,False)):
        if not result:break
        output=[]
        previous=result[-1]
        prev_v=previous.xyz[axis]
        prev_inside = (prev_v >= boundary - 1e-9) if keep_greater else (prev_v <= boundary + 1e-9)
        for point in result:
            current=point.xyz[axis]
            inside = (current >= boundary - 1e-9) if keep_greater else (current <= boundary + 1e-9)
            if inside != prev_inside:
                div = current - prev_v
                if abs(div)>1e-14:
                    output.append(interpolate(previous, point, (boundary-prev_v)/div))
            if inside:output.append(point)
            previous,prev_v,prev_inside=point,current,inside
        result=output
    return result


def _face_area_xy(p: list[Corner]) -> float:
    return abs(sum(a.xyz[0]*b.xyz[1]-b.xyz[0]*a.xyz[1] for a,b in zip(p,p[1:]+p[:1])))/2


def _index(s: str, length: int, what: str) -> int:
    if not s:raise ValueError(f'OBJ {what} index missing')
    idx=int(s)
    idx=(idx-1 if idx>0 else length+idx)
    if idx<0 or idx>=length:raise ValueError(f'OBJ {what} index out of range: {s}')
    return idx


def _corner(token, positions, uvs, normals, axis):
    parts=token.split('/')
    if len(parts)>3:raise ValueError('OBJ face triplet invalid')
    xyz=positions[_index(parts[0],len(positions),'vertex')]
    if axis=='yxz':xyz=(xyz[1],xyz[0],xyz[2])
    uv=uvs[_index(parts[1],len(uvs),'UV')] if len(parts)>1 and parts[1] else None
    norm=normals[_index(parts[2],len(normals),'normal')] if len(parts)>2 and parts[2] else None
    if axis=='yxz' and norm is not None:norm=(norm[1],norm[0],norm[2])
    return Corner(xyz,uv,norm)


def crop_obj(source:Path, output:Path, bounds:tuple[float,float,float,float], axis:str, *, cancel=None) -> dict:
    """Clips an OBJ file into a new OBJ, output is always xyz. Copies MTL assets.

    Each clipped triangle has its own corner attributes (not vertex welding),
    to keep intersection interpolation deterministic and avoid invalid OBJ indices.
    """
    if axis not in ('xyz','yxz'):raise ValueError('unsupported axis')
    output.parent.mkdir(parents=True,exist_ok=True)
    # Read OBJ geometry once into disk-backed float64 arrays, then read faces.
    # This avoids ~100+ bytes of Python-object overhead per vertex on giant GML.
    stack = ExitStack()
    try:
        vertices=_DiskIndexedTriples(stack,3,output.parent)
        uvs=_DiskIndexedTriples(stack,2,output.parent)
        normals=_DiskIndexedTriples(stack,3,output.parent)
        with source.open(encoding='utf-8-sig',errors='replace') as f:
            for number, line in enumerate(f, 1):
                if cancel and number % 4096 == 0: cancel.check()
                if line.startswith('v '):
                    vertices.append(tuple(map(float,line.split()[1:4])))
                elif line.startswith('vt '):
                    uvs.append(tuple(map(float,line.split()[1:3])))
                elif line.startswith('vn '):
                    normals.append(tuple(map(float,line.split()[1:4])))
        for table in (vertices,uvs,normals):
            table.freeze()
        return _crop_faces(source, output, bounds, axis, vertices, uvs, normals, cancel=cancel)
    finally:
        stack.close()


def _crop_faces(source: Path, output: Path, bounds, axis, vertices, uvs, normals,
                cancel=None) -> dict:
    warnings=[]
    renamed=_copy_mtl_assets(source,output.parent,warnings)
    tmp_v=output.with_suffix('.clip_v.part')
    tmp_t=output.with_suffix('.clip_t.part')
    tmp_n=output.with_suffix('.clip_n.part')
    tmp_f=output.with_suffix('.clip_f.part')
    temporary=(tmp_v,tmp_t,tmp_n,tmp_f)
    stats={'faces_input':0,'faces_output':0,'vertices_output':0,'warnings':warnings}
    count_v=count_t=count_n=0
    try:
        with (source.open(encoding='utf-8-sig',errors='replace') as src,
              tmp_v.open('w',encoding='utf-8') as vert,
              tmp_t.open('w',encoding='utf-8') as tex,
              tmp_n.open('w',encoding='utf-8') as norm,
              tmp_f.open('w',encoding='utf-8') as face):
            face.write('# Projected XY rectangle clipped from '+source.name+'\n')
            for number, line in enumerate(src, 1):
                if cancel and number % 1024 == 0: cancel.check()
                if line.startswith('mtllib '):
                    ref=line[len('mtllib '):].strip().strip('"')
                    resolved=str((source.parent/ref.replace('\\','/')).resolve())
                    face.write('mtllib '+renamed[resolved]+'\n')
                elif line.startswith(('usemtl ','o ','g ','s ')):
                    face.write(line)
                elif line.startswith('f '):
                    stats['faces_input']+=1
                    p=[_corner(token,vertices,uvs,normals,axis) for token in line.split()[1:]]
                    if len(p)<3:continue
                    clipped=clip_polygon(p,bounds)
                    if len(clipped)<3:continue
                    # Sutherland-Hodgman may return coincident boundary vertices.
                    cleaned=[]
                    for c in clipped:
                        if not cleaned or math.dist(c.xyz,cleaned[-1].xyz)>1e-8:cleaned.append(c)
                    if len(cleaned)>2 and math.dist(cleaned[0].xyz,cleaned[-1].xyz)<=1e-8:cleaned.pop()
                    if len(cleaned)<3:continue
                    # Vertical wall faces have zero XY area. Keep them if 3D face area exists.
                    v1,v2,v3=cleaned[0].xyz,cleaned[1].xyz,cleaned[2].xyz
                    cross=((v2[1]-v1[1])*(v3[2]-v1[2])-(v2[2]-v1[2])*(v3[1]-v1[1]),
                           (v2[2]-v1[2])*(v3[0]-v1[0])-(v2[0]-v1[0])*(v3[2]-v1[2]),
                           (v2[0]-v1[0])*(v3[1]-v1[1])-(v2[1]-v1[1])*(v3[0]-v1[0]))
                    if math.sqrt(sum(v*v for v in cross))<1e-10:continue
                    if axis=='yxz':cleaned.reverse()  # XY swap reflects orientation.
                    parts=[]
                    for c in cleaned:
                        count_v+=1
                        vert.write('v '+' '.join(format(v,'.12g') for v in c.xyz)+'\n')
                        vi=str(count_v)
                        ti=ni=''
                        if c.uv is not None:
                            count_t+=1;ti=str(count_t)
                            tex.write('vt '+' '.join(format(v,'.12g') for v in c.uv)+'\n')
                        if c.normal is not None:
                            count_n+=1;ni=str(count_n)
                            norm.write('vn '+' '.join(format(v,'.12g') for v in c.normal)+'\n')
                        parts.append(vi + (('/'+ti+('/'+ni if ni else '')) if (ti or ni) else ''))
                    face.write('f '+' '.join(parts)+'\n')
                    stats['faces_output']+=1
        if not count_v:return stats
        staging=output.with_suffix('.tmpobj')
        with staging.open('w',encoding='utf-8') as out:
            for path in (tmp_v,tmp_t,tmp_n,tmp_f):
                with path.open(encoding='utf-8') as src:shutil.copyfileobj(src,out)
        staging.replace(output)
        stats['vertices_output']=count_v
        return stats
    finally:
        for p in temporary:p.unlink(missing_ok=True)


def cached_clip(source:Path, root:Path, bounds:tuple[float,float,float,float], axis:str,
                log=print, *, cancel=None) -> tuple[Path|None,dict]:
    """Clip with a verifiable content manifest and safely regenerable results.

    Keep the provenance marker separate from the SHA-256 manifest so corrupt
    ready markers and modified geometry cannot masquerade as valid results.
    An empty clip has an explicit verified record with no geometry files.
    """
    if cancel: cancel.check()
    deps=obj_dependencies(source)
    inputs=[source,*deps]
    signature={'version':3,'paths':[[str(p.resolve()),p.stat().st_size,p.stat().st_mtime_ns] for p in inputs],
               'bounds':list(bounds),'axis':axis}
    key=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()[:20]
    directory=root/key
    directory.mkdir(parents=True,exist_ok=True)
    dest=directory/source.name
    meta=directory/'clip_ready.json'
    content=directory/'clip_contents.json'
    cache_key=['clip-v3',signature]
    if meta.is_file():
        try:
            data=json.loads(meta.read_text(encoding='utf-8'))
            if data.get('signature')==signature:
                stats=data['stats']
                outputs=[directory / item for item in data['generated']]
                if (isinstance(stats,dict) and isinstance(outputs,list) and
                    all(p.parent==directory and p.name not in ('.','..') for p in outputs) and
                    ((stats['vertices_output']==0 and not outputs and not dest.exists()) or
                      (stats['vertices_output']>0 and outputs and outputs[0]==dest)) and
                    cache_valid(content,cache_key,outputs)):
                    log('[CACHE] SHA-256検証済みclip: '+source.name)
                    return (dest if outputs else None),stats
        except (OSError,ValueError,TypeError,KeyError):
            pass
    # Drop only the dedicated derived cache folder, not the user's OBJ source.
    if directory.exists():shutil.rmtree(directory)
    directory.mkdir(parents=True)
    try:
        stat=crop_obj(source,dest,bounds,axis,cancel=cancel)
        if cancel: cancel.check()
        outputs=([dest]+sorted(p for p in directory.iterdir()
                               if p.is_file() and p!=dest and not p.name.endswith('.part')
                               and p.name not in ('clip_ready.json','clip_contents.json'))
                 if stat['vertices_output'] else [])
        if stat['vertices_output']==0:
            dest.unlink(missing_ok=True)
            # Materials alone are not geometry; a zero-face cache is empty.
            for p in directory.iterdir():
                if p.is_file() and p.name not in ('clip_ready.json','clip_contents.json'):
                    p.unlink(missing_ok=True)
        cache_mark(content,cache_key,outputs,stat)
        if cancel: cancel.check()
        payload={'signature':signature,'stats':stat,'generated':[p.name for p in outputs]}
        tmp=meta.with_name(meta.name+'.part')
        tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(meta)
    except BaseException:
        meta.unlink(missing_ok=True)
        raise
    if not stat['vertices_output']:
        log('[CLIP] no geometry inside: '+source.name)
        return None,stat
    log(f'[CLIP] {source.name}: {stat["faces_input"]} input faces -> {stat["faces_output"]} clipped faces')
    return dest,stat
