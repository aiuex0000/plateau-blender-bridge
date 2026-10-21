"""Cut a PLATEAU CityGML down to the buildings near a lon/lat rectangle.

Text-level on purpose: whole cityObjectMember blocks are kept or dropped and the
global appearance keeps only texture targets of kept surfaces, so namespace
prefixes and every retained byte stay exactly as published. The downloaded GML
is read only; the subset is a new file. Geometry is still clipped later in XY.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import re

MEMBER = re.compile(r'<(\w+):cityObjectMember\b[^>]*>.*?</\1:cityObjectMember>', re.S)
POS = re.compile(r'<(?:\w+:)?pos(?:List)?\b[^>]*>([^<]*)<')
GML_ID = re.compile(r'\bgml:id="([^"]+)"')
APPEARANCE = re.compile(r'<(\w+):appearanceMember\b[^>]*>.*?</\1:appearanceMember>', re.S)
SURFACE_DATA = re.compile(r'<(\w+):surfaceDataMember\b[^>]*>.*?</\1:surfaceDataMember>', re.S)
TARGET = re.compile(r'<(\w+):target\b[^>]*\buri="#([^"]+)"[^>]*?(?:/>|>.*?</\1:target>)', re.S)
TARGET_TEXT = re.compile(r'<(\w+):target\b[^>]*>\s*#([^<\s]+)\s*</\1:target>')
MARGIN_DEG = 0.0005  # ~50 m: keep buildings straddling the edge; XY clip trims them later


def _member_box(block: str) -> tuple[float, float, float, float] | None:
    """(west, south, east, north) from lat lon h triplets (PLATEAU EPSG:6697 order)."""
    lat_lo = lon_lo = float('inf'); lat_hi = lon_hi = float('-inf')
    for m in POS.finditer(block):
        vals = m.group(1).split()
        if len(vals) % 3:
            raise ValueError('posListが3次元(緯度 経度 高さ)ではありません')
        lats = [float(v) for v in vals[0::3]]; lons = [float(v) for v in vals[1::3]]
        lat_lo = min(lat_lo, *lats); lat_hi = max(lat_hi, *lats)
        lon_lo = min(lon_lo, *lons); lon_hi = max(lon_hi, *lons)
    return None if lat_lo == float('inf') else (lon_lo, lat_lo, lon_hi, lat_hi)


def _filter_appearance(block: str, ids: set[str]) -> tuple[str, int]:
    kept = 0
    def surface(m: re.Match) -> str:
        nonlocal kept
        body = TARGET.sub(lambda t: t.group(0) if t.group(2) in ids else '', m.group(0))
        body = TARGET_TEXT.sub(lambda t: t.group(0) if t.group(2) in ids else '', body)
        if not (TARGET.search(body) or TARGET_TEXT.search(body)):
            return ''
        kept += 1
        return body
    out = SURFACE_DATA.sub(surface, block)
    return (out if SURFACE_DATA.search(out) else ''), kept


def subset_gml(src: Path, dest_dir: Path, bbox: tuple[float, float, float, float], *,
               margin: float = MARGIN_DEG) -> tuple[Path, dict]:
    """Return (subset path, report). bbox is (west, south, east, north) in degrees."""
    west, south, east, north = bbox
    st = src.stat()
    key = hashlib.sha256(repr((str(src.resolve()), st.st_size, st.st_mtime_ns, bbox, margin, 1)).encode()).hexdigest()[:16]
    dest = dest_dir / f'{src.stem}.subset_{key}.gml'
    text = src.read_text(encoding='utf-8-sig')
    members = list(MEMBER.finditer(text))
    if not members:
        # Nothing to cut by building; later size limits and XY clipping still apply.
        return src, {'members_total': 0, 'members_kept': 0, 'textures_kept': 0, 'path': str(src)}
    kept_blocks: list[str] = []
    ids: set[str] = set()
    for m in members:
        box = _member_box(m.group(0))
        if box and box[0] <= east + margin and box[2] >= west - margin and box[1] <= north + margin and box[3] >= south - margin:
            kept_blocks.append(m.group(0))
            ids.update(GML_ID.findall(m.group(0)))
    if not kept_blocks:
        raise ValueError('指定範囲の周辺に建物がありません。範囲を確認してください')
    head, tail = text[:members[0].start()], text[members[-1].end():]
    textures = 0
    def appearance(m: re.Match) -> str:
        nonlocal textures
        out, n = _filter_appearance(m.group(0), ids)
        textures += n
        return out
    head = APPEARANCE.sub(appearance, head)
    report = {'members_total': len(members), 'members_kept': len(kept_blocks),
              'textures_kept': textures, 'path': str(dest)}
    if not dest.is_file():
        dest_dir.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + '.part')
        part.write_text(head + '\n'.join(kept_blocks) + tail, encoding='utf-8', newline='')
        part.replace(dest)
    return dest, report
