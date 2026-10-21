"""Streaming OBJ inspection and loss-minimising XY rebase before float32 import."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import math
import shlex
import shutil

@dataclass(frozen=True)
class ObjStats:
    path: str
    vertices: int
    bbox: tuple[float, float, float, float, float, float]


def _transform(x: float, y: float, z: float, axis: str):
    return (y, x, z) if axis == 'yxz' else (x, y, z)


def inspect_obj(path: Path, axis: str = 'xyz') -> ObjStats:
    if axis not in ('xyz', 'yxz'):
        raise ValueError('OBJ軸順序は xyz または yxz のみ')
    low = [math.inf] * 3
    high = [-math.inf] * 3
    count = 0
    with path.open('r', encoding='utf-8-sig', errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            if not line.startswith('v '):
                continue
            p = line.split()
            if len(p) < 4:
                raise ValueError(f'{path}:{lineno}: 不正な頂点')
            xyz = _transform(*map(float, p[1:4]), axis)
            if not all(map(math.isfinite, xyz)):
                raise ValueError(f'{path}:{lineno}: 非有限頂点')
            for i in range(3):
                low[i] = min(low[i], xyz[i]); high[i] = max(high[i], xyz[i])
            count += 1
    if not count:
        raise ValueError(f'{path}: v頂点がありません (OBJ形式を確認)')
    return ObjStats(str(path), count, (low[0], low[1], low[2], high[0], high[1], high[2]))


def _asset_name(path: Path, prefix: str) -> str:
    slug = hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()[:12]
    return f'{prefix}_{slug}_{path.name}'


def _material_refs(src: Path) -> list[Path]:
    refs = []
    with src.open('r', encoding='utf-8-sig', errors='replace') as f:
        for line in f:
            if line.startswith('mtllib '):
                # One path per mtllib line (most converter exports). An ambiguous
                # multi-path directive is not silently split incorrectly.
                name = line[len('mtllib '):].strip().strip('"')
                p = (src.parent / name.replace('\\', '/')).resolve()
                if not p.is_file():
                    raise FileNotFoundError(f'MTLファイルが見つかりません: {p}')
                refs.append(p)
    return refs


def _image_ref(line: str, mtl: Path) -> tuple[Path | None, str]:
    """Return (source texture, option prefix). Handles simple MTL and common flags."""
    key, _, rest = line.strip().partition(' ')
    value = rest.strip()
    if not value:
        return None, ''
    # Bare filenames may contain spaces. Try the whole path first.
    full = (mtl.parent / value.replace('\\', '/')).resolve()
    if full.is_file():
        return full, ''
    try:
        tokens = shlex.split(value, posix=False)
    except ValueError:
        return None, value
    if not tokens:
        return None, value
    # The last token is the texture filename in option-bearing MTL references.
    candidate = tokens[-1].strip('"\'')
    source = (mtl.parent / candidate.replace('\\', '/')).resolve()
    prefix = value[:value.rfind(tokens[-1])].strip()
    return (source if source.is_file() else None), prefix


def obj_dependencies(src: Path) -> list[Path]:
    """Dependencies used in cache fingerprints so modified MTL/texture invalidates cache."""
    output = []
    for mtl in _material_refs(src):
        output.append(mtl)
        with mtl.open('r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                key = line.lstrip().split(' ', 1)[0].lower()
                if key.startswith('map_') or key in ('bump', 'disp', 'decal', 'refl', 'norm'):
                    image, _ = _image_ref(line, mtl)
                    if image:
                        output.append(image)
    return sorted(set(output))


def _copy_mtl_assets(src_obj: Path, dest_dir: Path, warnings: list[str]) -> dict[str, str]:
    """Rebase MTL and texture links to stable, safe names within the stage directory."""
    renamed = {}
    for mtl in _material_refs(src_obj):
        staged_mtl = _asset_name(mtl, 'material')
        renamed[str(mtl)] = staged_mtl
        lines = []
        with mtl.open('r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                key = line.lstrip().split(' ', 1)[0].lower()
                if not (key.startswith('map_') or key in ('bump', 'disp', 'decal', 'refl', 'norm')):
                    lines.append(line)
                    continue
                image, flags = _image_ref(line, mtl)
                if not image:
                    warnings.append(f'{mtl.name}: 画像参照を解決できません: {line.strip()[:120]}')
                    lines.append(line)
                    continue
                dest_name = _asset_name(image, 'texture')
                shutil.copy2(image, dest_dir / dest_name)
                lines.append(line.split(None,1)[0] + ' ' + ((flags + ' ') if flags else '') + dest_name + '\n')
        (dest_dir / staged_mtl).write_text(''.join(lines), encoding='utf-8')
    return renamed


def normalize_obj(src: Path, dst: Path, origin: tuple[float, float, float],
                  axis: str, warnings: list[str], *, cancel=None) -> int:
    """Rebase using double precision. Retain vertex/face/UV associations.

    When swapping X/Y, reflect normals AND invert polygon winding.
    Materials and image links are staged without mutating source files.
    """
    if axis not in ('xyz','yxz'):
        raise ValueError('OBJ axis must be xyz or yxz')
    dst.parent.mkdir(parents=True, exist_ok=True)
    names = _copy_mtl_assets(src, dst.parent, warnings)
    count = 0
    with src.open('r', encoding='utf-8-sig', errors='replace') as inp, dst.open('w', encoding='utf-8', newline='\n') as out:
        for line_no, line in enumerate(inp, 1):
            if cancel and line_no % 4096 == 0: cancel.check()
            if line.startswith('v '):
                p = line.strip().split()
                x, y, z = _transform(*map(float, p[1:4]), axis)
                p[1:4] = (format(x - origin[0], '.12g'), format(y - origin[1], '.12g'),
                          format(z - origin[2], '.12g'))
                out.write(' '.join(p) + '\n')
                count += 1
            elif line.startswith('vn ') and axis == 'yxz':
                p = line.strip().split()
                x, y, z = _transform(*map(float, p[1:4]), axis)
                p[1:4] = (format(x, '.12g'), format(y, '.12g'), format(z, '.12g'))
                out.write(' '.join(p) + '\n')
            elif line.startswith('f ') and axis == 'yxz':
                p = line.strip().split()
                out.write('f ' + ' '.join(reversed(p[1:])) + '\n')
            elif line.startswith('mtllib '):
                ref = line[len('mtllib '):].strip().strip('"')
                resolved = str((src.parent / ref.replace('\\','/')).resolve())
                out.write('mtllib ' + names[resolved] + '\n')
            else:
                out.write(line)
    return count


def union_bbox(stats: list[ObjStats]) -> tuple[float, float, float, float, float, float]:
    return tuple((min(s.bbox[i] for s in stats) if i < 3 else max(s.bbox[i] for s in stats)) for i in range(6))  # type: ignore


def overlaps_xy(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return min(a[3], b[3]) >= max(a[0], b[0]) and min(a[4], b[4]) >= max(a[1], b[1])


def missing_material_images(src: Path) -> list[str]:
    """Return material map references that cannot be resolved locally.

    This detects texture loss after CLI conversion, before Blender is invoked.
    It does not require a texture to exist for materials without map statements.
    """
    missing=[]
    for mtl in _material_refs(src):
        with mtl.open(encoding='utf-8-sig',errors='replace') as f:
            for line in f:
                key=line.lstrip().split(' ',1)[0].lower()
                if key.startswith('map_') or key in ('bump','disp','decal','refl','norm'):
                    image,_ = _image_ref(line,mtl)
                    if image is None:
                        missing.append(f'{mtl.name}: {line.strip()}')
    return missing


def has_used_diffuse_texture(source: Path) -> bool:
    """Check whether any usemtl in OBJ refers to a material with map_Kd.

    Detect expected appearance before Blender import. Unused library materials
    must not cause a false-positive texture requirement.
    """
    used=set()
    with source.open('r',encoding='utf-8-sig',errors='replace') as stream:
        for line in stream:
            if line.startswith('usemtl '):
                used.add(line[7:].strip())
    if not used:
        return False
    for mtl in _material_refs(source):
        active=None
        with mtl.open('r',encoding='utf-8-sig',errors='replace') as stream:
            for line in stream:
                if line.startswith('newmtl '):
                    active=line[7:].strip()
                elif active in used and line.lstrip().lower().startswith('map_kd '):
                    texture, _ = _image_ref(line, mtl)
                    if texture is None:
                        raise FileNotFoundError(f'MTLの拡散反射テクスチャがありません: {mtl}: {line.strip()}')
                    return True
    return False
