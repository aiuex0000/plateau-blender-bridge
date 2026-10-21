"""Select one JGD2011 Japanese plane rectangular zone for bbox-only queries.

Selection is based on the EPSG published area-of-use extent and zone origin;
CRS metadata is from PROJ/pyproj, not a hard-coded municipal zone table.
The result is still a recommendation; logged in scene provenance.
"""
from __future__ import annotations
from functools import lru_cache
import math
from pyproj import CRS


@lru_cache(maxsize=1)
def _zones():
    zones=[]
    for code in range(6669,6688):
        crs=CRS.from_epsg(code)
        extent=crs.area_of_use.bounds
        params={p.name.lower():p.value for p in crs.coordinate_operation.params}
        lat0=next((v for k,v in params.items() if 'latitude of natural origin' in k),None)
        lon0=next((v for k,v in params.items() if 'longitude of natural origin' in k),None)
        if lat0 is None or lon0 is None:raise RuntimeError('PROJ zone origin parameter not found')
        zones.append((code,extent,lat0,lon0))
    return zones


def japanese_zone_from_bbox(west:float,south:float,east:float,north:float) -> str:
    """Area-of-use filter + nearest CRS origin. Only supported for Japan."""
    if not all(math.isfinite(x) for x in (west,south,east,north)) or not (-180<west<east<180 and -90<south<north<90):
        raise ValueError('緯度経度範囲が不正')
    lon=(west+east)/2;lat=(south+north)/2
    candidates=[]
    for code,(a,b,c,d),lat0,lon0 in _zones():
        if a<=lon<=c and b<=lat<=d:
            score=((lon-lon0)*math.cos(math.radians(lat)))**2+(lat-lat0)**2
            candidates.append((score,code))
    if not candidates:
        raise ValueError('対象地点はJGD2011平面直角座標系19系の定義範囲外です。EPSGを手動指定してください')
    return 'EPSG:'+str(min(candidates)[1])


def resolve_epsg(value:str,condition:str,sources=None) -> str:
    if value.upper().strip() != 'AUTO':return value
    if not condition.startswith('r:'):
        if sources is not None:
            return zone_from_sources(sources)
        if condition.startswith(('m:', 'mm:')) and ',' not in condition:
            lon,lat=mesh_center(condition.split(':',1)[1])
            return japanese_zone_from_bbox(lon-0.000001,lat-0.000001,lon+0.000001,lat+0.000001)
        raise ValueError('地名・複数メッシュ指定のEPSG AUTOはカタログ検索後に判定します')
    try:coords=tuple(map(float,condition[2:].split(',')))
    except ValueError as e:raise ValueError('矩形の数値が不正') from e
    if len(coords)!=4:raise ValueError('EPSG自動判定には矩形の4値が必要です')
    return japanese_zone_from_bbox(*coords)


def mesh_center(mesh: str) -> tuple[float, float]:
    """Japanese standard mesh center (lon, lat), supports mesh levels 1–3 + splits.

    Mesh code is geographical rather than a CRS; used to choose a projection zone,
    never to pretend it gives the exact building footprint.
    """
    if not mesh.isascii() or not mesh.isdigit() or len(mesh) not in (4, 6, 8, 9, 10):
        raise ValueError(f'日本の地域メッシュコードが不正: {mesh}')
    lat=int(mesh[:2])*2/3
    lon=100+int(mesh[2:4])
    dlat=2/3
    dlon=1.0
    if len(mesh)>=6:
        if mesh[4] not in '01234567' or mesh[5] not in '01234567':
            raise ValueError(f'2次メッシュ番号が不正: {mesh}')
        dlat/=8; dlon/=8
        lat+=int(mesh[4])*dlat; lon+=int(mesh[5])*dlon
    if len(mesh)>=8:
        dlat/=10; dlon/=10
        lat+=int(mesh[6])*dlat;lon+=int(mesh[7])*dlon
    for n in mesh[8:]:
        if n not in '1234':raise ValueError(f'分割メッシュ番号が不正: {mesh}')
        dlat/=2;dlon/=2
        lat+=int(n in '34')*dlat
        lon+=int(n in '24')*dlon
    return lon+dlon/2,lat+dlat/2


def zone_from_sources(sources) -> str:
    """Resolve a common zone for a catalog result; reject multi-zone selections."""
    codes=[]
    for source in sources:
        lon,lat=mesh_center(source.mesh)
        codes.append(japanese_zone_from_bbox(lon-0.000001,lat-0.000001,lon+0.000001,lat+0.000001))
    unique=sorted(set(codes))
    if not unique:raise ValueError('EPSGのAUTO判定には地域メッシュ情報が必要です')
    if len(unique)!=1:raise ValueError(f'選択範囲が複数の平面直角座標系にまたがります: {unique}。範囲を分割するかEPSGを手動指定してください')
    return unique[0]
