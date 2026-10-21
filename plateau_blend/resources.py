"""Conservative workload forecast for the explicitly selected bbox.

An estimate is not a claim about final OBJ size or actual RAM use.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math
from pyproj import Transformer
from .gsi import photo_zoom, tile_bbox_from_xy


@dataclass(frozen=True)
class ResourcePlan:
    photo_zoom: int
    photo_tiles: int
    dem_tile_positions: int
    dem_upper_bound_requests: int
    photo_width: int
    photo_height: int
    terrain_vertices: int
    image_rgb_mib: float
    estimate_note: str = 'タイル位置数と生成画像の概算。GML変換・Blenderの実RAM使用量ではない'

    def as_dict(self):
        return asdict(self)


def forecast(bounds: tuple[float,float,float,float], epsg: str, *,
             wanted_zoom: int = 16, dem_resolution: int = 100,
             max_dimension: int = 2048, dem_zoom: int = 14) -> ResourcePlan:
    xmin,ymin,xmax,ymax=bounds
    if not all(math.isfinite(v) for v in bounds) or xmin>=xmax or ymin>=ymax:
        raise ValueError('取得範囲が不正')
    z=photo_zoom(bounds,epsg,wanted=wanted_zoom)
    a,b,c,d=tile_bbox_from_xy(bounds,epsg,z)
    photo_tiles=(c-a+1)*(d-b+1)
    a,b,c,d=tile_bbox_from_xy(bounds,epsg,dem_zoom)
    dem_tiles=(c-a+1)*(d-b+1)
    # DEM fallbacks may try up to 4 layers for each tile (very conservative).
    lon,lat=Transformer.from_crs(epsg,4326,always_xy=True).transform((xmin+xmax)/2,(ymin+ymax)/2)
    pixel_m=156543.03392804097*math.cos(math.radians(lat))/(2**z)
    w=max(16,min(max_dimension,math.ceil((xmax-xmin)/pixel_m)))
    h=max(16,min(max_dimension,math.ceil((ymax-ymin)/pixel_m)))
    longest=max(xmax-xmin,ymax-ymin)
    nx=max(2,math.ceil(dem_resolution*(xmax-xmin)/longest))
    ny=max(2,math.ceil(dem_resolution*(ymax-ymin)/longest))
    return ResourcePlan(z,photo_tiles,dem_tiles,dem_tiles*4,w,h,(nx+1)*(ny+1),round(w*h*3/1048576,2))


def enforce_budget(plan: ResourcePlan, *, max_total_requests: int = 160,
                   max_image_pixels: int = 2048*2048) -> None:
    if plan.photo_tiles+plan.dem_upper_bound_requests>max_total_requests:
        raise ValueError(f'取得対象が多すぎます。写真{plan.photo_tiles}枚、DEM最大{plan.dem_upper_bound_requests}試行。範囲を狭めてください')
    if plan.photo_width*plan.photo_height > max_image_pixels:
        raise ValueError('写真の画素数が設定上限を超えています')
