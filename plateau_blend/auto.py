"""Phase 2+3 auto-flow: PLATEAU API -> CityGML -> nusamai OBJ -> GSI DEM/photo -> Blender.

Defaults prioritize inspectability and safe failure. No input is overwritten.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import hashlib
import json
import math
import re
import subprocess
import shutil
from pyproj import CRS, Transformer
from .config import Config
from .catalog import Source, fetch_catalog, select_sources, download
from .appearance import stage_gml
from .converter import command as converter_command, yup_to_zup
from .discovery import find_blender, find_nusamai
from .gsi import GsiTiles, make_terrain, reproject_photo, xy_bounds_of_obj
from .obj import inspect_obj, union_bbox, missing_material_images, obj_dependencies
from .clip import cached_clip
from .crs import resolve_epsg
from .pipeline import run
from .artifact_cache import valid as cache_valid, mark as cache_mark
from .runtime import CancelToken, run_logged
from .resources import forecast, enforce_budget

Log=Callable[[str],None]


@dataclass(frozen=True)
class AutoRequest:
    condition: str
    epsg: str
    output_blend: Path
    blender_executable: Path|None=None
    nusamai_executable: Path|None=None
    lod: str='max_lod'  # min_lod is LOD0: flat footprints at height 0
    photo_zoom: int=16
    terrain_resolution: int=100
    max_gml_files: int=24
    max_download_mb: int=250
    include_dem_from_plateau: bool=False  # Experimental; GSI terrain is the default.
    axis_override: str = 'auto'
    clip_requested_area: bool = True
    max_texture_mb: int = 120

    def effective_epsg(self,sources=None)->str:
        return resolve_epsg(self.epsg,self.condition,sources)

    def check(self):
        # Geocoded AUTO needs the catalog mesh list; resolve after fetch, not before.
        pending=(self.epsg.upper().strip()=='AUTO' and not self.condition.startswith('r:')
                 and (self.condition.startswith('g:') or ',' in self.condition))
        epsg=(None if pending else self.effective_epsg())
        if epsg is not None and not re.fullmatch(r'EPSG:[1-9][0-9]{3,5}',epsg): raise ValueError('EPSG:6674 または AUTO の形式で指定')
        if epsg is not None:
            crs=CRS.from_user_input(epsg)
            if not crs.is_projected or not all(abs(axis.unit_conversion_factor-1)<1e-8 for axis in crs.axis_info[:2]):
                raise ValueError('メートルの投影座標系EPSGが必要（緯度経度4326や3857の誤指定に注意）')
        if self.output_blend.suffix.lower()!='.blend':raise ValueError('.blend拡張子が必要')
        if self.lod not in ('min_lod','max_lod','textured_max_lod'):raise ValueError('未対応LOD')
        if self.axis_override not in ('auto','xyz','yxz'):raise ValueError('未対応の軸順指定')
        if not 14<=self.photo_zoom<=18:raise ValueError('航空写真ズームは14〜18')
        if not 24<=self.terrain_resolution<=256:raise ValueError('地形解像度は24〜256')
        if self.max_texture_mb<1 or self.max_texture_mb>2048: raise ValueError('テクスチャ容量制限が不正')
        if self.max_gml_files<1 or self.max_gml_files>100 or self.max_download_mb<1:raise ValueError('ダウンロード制限値が不正')
        if self.include_dem_from_plateau:raise ValueError('PLATEAU DEM直接変換は実験中。現状はGSI標高タイルを使ってください')


def projected_selection(condition: str, epsg: str) -> tuple[float,float,float,float] | None:
    """Projected envelope of a geographic rectangle (not a geometric geodesic clip).

    Midpoints along edges bound projection curvature better than 4 corners.
    This is a conservative XY approximation; keep that explicit in metadata.
    """
    if not condition.startswith('r:'):
        return None
    raw=tuple(map(float,condition[2:].split(',')))
    if len(raw)!=4:raise ValueError('r:矩形指定が必要')
    west,south,east,north=raw
    tr=Transformer.from_crs('EPSG:4326',epsg,always_xy=True)
    pts=[]
    for i in range(9):
        p=i/8
        pts.extend((tr.transform(west+(east-west)*p,south),
                    tr.transform(west+(east-west)*p,north),
                    tr.transform(west,south+(north-south)*p),
                    tr.transform(east,south+(north-south)*p)))
    vals=[(x,y) for x,y in pts if math.isfinite(x) and math.isfinite(y)]
    if len(vals)!=len(pts):raise ValueError('選択矩形のEPSG投影に失敗')
    return (min(x for x,_ in vals), min(y for _,y in vals),
            max(x for x,_ in vals), max(y for _,y in vals))


def preview(req:AutoRequest, *, fetcher=fetch_catalog, cancel:CancelToken|None=None)->list[Source]:
    req.check()
    if cancel: cancel.check()
    records=fetcher(req.condition)
    if cancel: cancel.check()
    return select_sources(records,max_files=req.max_gml_files,max_known_mb=req.max_download_mb)


def _locate_axis(obj_paths:list[Path],condition:str,epsg:str,log:Log)->str:
    """Reject ambiguous geo axis rather than silently making false maps."""
    xyz=union_bbox([inspect_obj(p,'xyz') for p in obj_paths])
    yxz=union_bbox([inspect_obj(p,'yxz') for p in obj_paths])
    def plausible(b):
        # Region supplied explicitly: confirm intersection with projected query envelope.
        if condition.startswith('r:'):
            west,south,east,north=map(float,condition[2:].split(','))
            fwd=Transformer.from_crs(4326,epsg,always_xy=True)
            pts=[fwd.transform(lon,lat) for lon in (west,east,(west+east)/2) for lat in (south,north,(south+north)/2)]
            x0=min(p[0] for p in pts); x1=max(p[0] for p in pts)
            y0=min(p[1] for p in pts); y1=max(p[1] for p in pts)
            # GML mesh bbox may be bigger, but MUST intersect selected area.
            return b[0]<=x1 and b[3]>=x0 and b[1]<=y1 and b[4]>=y0
        # With geo names / meshes, use Japan extent rough sanity check; exact alignment not provable.
        inv=Transformer.from_crs(epsg,4326,always_xy=True)
        lon,lat=inv.transform((b[0]+b[3])/2,(b[1]+b[4])/2)
        return 122<=lon<=154 and 20<=lat<=46 and max(b[3]-b[0],b[4]-b[1])<150000
    goodx,goody=plausible(xyz),plausible(yxz)
    if not (goodx or goody):raise ValueError('OBJ座標が指定EPSG/検索範囲と一致しません。Converterの--epsg・重心移動設定を検証してください')
    if goodx and goody:
        # Projection coordinate pairs can look plausible when swapped in small regions;
        # require an explicit region rectangle to disambiguate rather than silently choose.
        raise ValueError('XYZとYXZの両方が地理範囲と一致。軸順を一意に判定できません。対象範囲を狭めるか実機確認してください')
    detected='xyz' if goodx else 'yxz'
    log(f'[AXIS] OBJの座標順を {detected} と判定しました')
    return detected


def converter_objects(binary:Path,gml_paths:list[Path],dest:Path,epsg:str,lod:str,log:Log, *, cancel:CancelToken|None=None)->list[Path]:
    if cancel: cancel.check()
    if not binary.is_file(): raise FileNotFoundError(f'nusamai CLI実行ファイルがありません: {binary}')
    dest.mkdir(parents=True,exist_ok=True)
    signature=hashlib.sha256(json.dumps({'version':4,'sources':[(str(x),x.stat().st_size,x.stat().st_mtime_ns) for x in gml_paths],
                                        'binary':(str(binary),binary.stat().st_size,binary.stat().st_mtime_ns),'epsg':epsg,'lod':lod},sort_keys=True).encode()).hexdigest()[:20]
    folder=dest/f'converted_{signature}'
    ready=folder/'conversion_ready.json'
    def all_generated_files():
        objs=sorted(folder.rglob('*.obj'))
        deps=sorted({path for obj in objs for path in obj_dependencies(obj)})
        return objs, objs + deps

    if ready.exists():
        try:
            objs,outputs=all_generated_files()
            if objs and cache_valid(ready,['converter-v4',signature],outputs):
                log('[CACHE] SHA-256検証済みOBJ変換結果を再利用')
                return objs
        except (OSError,FileNotFoundError,ValueError):
            pass
    if folder.exists():
        # Only our own known incomplete conversion folder, not source inputs.
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    argv=converter_command(binary,gml_paths,folder,lod,split=False)
    # Current MIERUNE nusamai supports --epsg for OBJ; old binary errors explicitly.
    argv += ['--epsg',epsg.split(':')[1]]
    log('[CONVERTER] '+subprocess.list2cmdline(argv))
    logpath=dest/'last_converter.log'
    run_logged(argv,logpath,log,cancel=cancel)
    objs=sorted(folder.rglob('*.obj'))
    if not objs: raise RuntimeError('変換結果にOBJが見つかりません: '+str(folder))
    for obj in objs:
        if cancel: cancel.check()
        yup_to_zup(obj)
    log(f'[CONVERTER] nusamaiのY-up OBJをZ-upへ回転: {len(objs)}件')
    objs,outputs=all_generated_files()
    cache_mark(ready,['converter-v4',signature],outputs,{'args':argv,'objs':[str(p) for p in objs]})
    return objs


def generate(req:AutoRequest, *, log:Log=print, cancel:CancelToken|None=None, catalogue_fetch=fetch_catalog,
             source_download=download, converter=converter_objects,
             blender_runner=run, tile_factory=GsiTiles)->Path:
    req.check()
    if cancel: cancel.check()
    if req.output_blend.exists():raise FileExistsError(f'出力 .blend を上書きしません: {req.output_blend}')
    blender=req.blender_executable or find_blender()
    if blender is None or not blender.is_file(): raise FileNotFoundError('Blenderが検出できません。Blender.exeを指定してください')
    binary=req.nusamai_executable or find_nusamai()
    if binary is None or not binary.is_file(): raise FileNotFoundError('nusamai CLIが見つかりません。nusamai.exeを指定してください（GIS Converter GUIのexeとは別）')
    log('[CATALOG] PLATEAUのCityGMLファイルを検索')
    response=catalogue_fetch(req.condition)
    if cancel: cancel.check()
    sources=select_sources(response,max_files=req.max_gml_files,max_known_mb=req.max_download_mb)
    log(f'[CATALOG] {len(sources)} GML, known bytes: {sum(s.size or 0 for s in sources)/1048576:.1f} MiB')
    years_by_city={}
    for source in sources:years_by_city.setdefault(source.city,set()).add(source.year)
    for city,years in years_by_city.items():
        if len(years)>1:log(f'[WARN] {city}: 異なる収録年度を混在させてメッシュの欠落を補完します: {sorted(years)}')
    epsg=req.effective_epsg(sources)
    log(f'[CRS] 利用する投影座標系: {epsg}'+(' (自動推定)' if req.epsg.upper()=='AUTO' else ' (手動指定)'))
    work=req.output_blend.with_suffix('.work')/'auto'
    if any(work==p or work in p.parents for p in (blender,binary)):
        raise ValueError('プログラム本体が作業フォルダの中にあります')
    gmls=[]
    texture_reports=[]
    textures_bytes=0
    downloaded_bytes=0
    for index,source in enumerate(sources,1):
        if cancel: cancel.check()
        log(f'[DOWNLOAD {index}/{len(sources)}] {source.city} / mesh {source.mesh}')
        if cancel and source_download is download:
            path=source_download(source,work/'downloads',cancel=cancel)
        else:
            path=source_download(source,work/'downloads')
        downloaded_bytes+=path.stat().st_size
        if downloaded_bytes>req.max_download_mb*1024*1024:
            raise ValueError(f'合計ダウンロードが上限 {req.max_download_mb}MiB を超えました。入力範囲を狭めてください')
        if req.lod=='textured_max_lod':
            staged,texture_report=stage_gml(source,path,work/'citygml',max_total_mb=req.max_texture_mb,log=log,cancel=cancel)
            gmls.append(staged)
            texture_reports.append(texture_report)
            textures_bytes+=texture_report['bytes']
            if textures_bytes>req.max_texture_mb*1024*1024:
                raise ValueError(f'複数GML合計のテクスチャが{req.max_texture_mb}MiBを超えました')
        else:
            gmls.append(path)
    if cancel: cancel.check()
    if cancel and converter is converter_objects:
        objs=converter(binary,gmls,work/'converter',epsg,req.lod,log,cancel=cancel)
    else:
        objs=converter(binary,gmls,work/'converter',epsg,req.lod,log)
    if len(objs)>200:raise ValueError('変換後のOBJが200個を超えました。入力範囲を狭めてください')
    if req.lod=='textured_max_lod':
        missing=[ref for obj in objs for ref in missing_material_images(obj)]
        if missing:
            raise ValueError('Converter出力のMTLで画像が見つかりません（テクスチャ参照切れ）:\n'+
                             '\n'.join(missing[:15]))
    axis=(req.axis_override if req.axis_override!='auto' else _locate_axis(objs,req.condition,epsg,log))
    if req.axis_override!='auto': log(f'[AXIS] 手動指定: {axis} （投影位置の最終確認が必要）')
    original_objs=list(objs)
    selection=projected_selection(req.condition,epsg) if req.clip_requested_area else None
    if selection is not None:
        if max(selection[2]-selection[0],selection[3]-selection[1])>15000:
            raise ValueError('選択範囲が15kmを超えています。小さな矩形に分割してください')
        # Crop *after* axis validation. Cropped OBJ is normalized to xyz for Blender.
        clipped=[]
        for obj in original_objs:
            if cancel: cancel.check()
            output,stats=cached_clip(obj,work/'clipped',selection,axis,log,cancel=cancel)
            if output is not None:clipped.append(output)
        if not clipped:raise ValueError('指定範囲にOBJ建物面がありません。座標系・対象地域・LODを見直してください')
        objs=clipped
        axis='xyz'
        # Terrain and orthophoto cover the selected XY envelope, NOT the entire source mesh.
        xy=selection
        log(f'[BBOX] 指定した緯度経度矩形を投影XY近似して切り出し: {xy} / {epsg}')
    else:
        bounds=union_bbox([inspect_obj(p,axis) for p in objs])
        xy=xy_bounds_of_obj(bounds,20.0)
        if max(xy[2]-xy[0],xy[3]-xy[1])>15000:
            raise ValueError('建物の収録範囲が15km超。選択した範囲に対しGMLが広すぎる可能性があります')
        log(f'[BBOX] 取得メッシュの全建物を含む範囲: {xy} / {epsg}')
    plan=forecast(xy,epsg,wanted_zoom=req.photo_zoom,dem_resolution=req.terrain_resolution)
    enforce_budget(plan)
    log(f'[PLAN] 写真z{plan.photo_zoom}: {plan.photo_tiles}タイル / {plan.photo_width}x{plan.photo_height}px; DEM {plan.dem_tile_positions}タイル位置、最大{plan.dem_upper_bound_requests}回試行')
    if cancel: cancel.check()
    tiles=tile_factory(work/'gsi_cache')
    if hasattr(tiles,'cancel'): tiles.cancel=cancel
    terrain=work/'generated'/'terrain_gsi.obj'
    # Cache elevation mesh keyed on bbox/quality (not only filename).
    terrain_meta=terrain.with_suffix('.json')
    terrain_key=[list(xy),epsg,req.terrain_resolution,14]
    if cancel: cancel.check()
    if not cache_valid(terrain_meta,terrain_key,[terrain]):
        dem=make_terrain(tiles,xy,epsg,terrain,resolution=req.terrain_resolution,log=log)
        cache_mark(terrain_meta,terrain_key,[terrain],dem)
    else: log('[CACHE] 地形メッシュを再利用')
    png=work/'generated'/'photo.png'
    pgw=work/'generated'/'photo.pgw'
    photo_meta=work/'generated'/'photo.json'
    photo_key=[list(xy),epsg,req.photo_zoom]
    if cancel: cancel.check()
    if not cache_valid(photo_meta,photo_key,[png,pgw]):
        result=reproject_photo(tiles,xy,epsg,png,pgw,zoom=req.photo_zoom,log=log)
        cache_mark(photo_meta,photo_key,[png,pgw],result)
    else:log('[CACHE] 航空写真を再利用')
    cfg=Config(blender_executable=blender,building_objs=tuple(objs),terrain_objs=(terrain,),
               aerial_png=png,world_file=pgw,epsg=epsg,image_epsg=epsg,
               output_blend=req.output_blend,building_axis=axis,terrain_axis='xyz',
               imagery_attribution='国土地理院（全国最新写真・標高タイル） / Project PLATEAU（建築物モデル）',
               imagery_terms_url='https://maps.gsi.go.jp/help/use.html',pack_image=True)
    provenance={'condition':req.condition,'sources':[s.__dict__ for s in sources],
                'epsg':epsg,'requested_lod':req.lod,'axis':axis,'converter':str(binary),
                'original_obj_paths':[str(p) for p in original_objs],
                'clip_mode':('projected_xy_envelope_surface_clip' if selection else 'none'),
                'selection_bounds_xy':list(selection) if selection else None,
                'tile_provider':'GSI','terrain_provider':'GSI DEM','resource_plan':plan.as_dict(),
                'texture_stage':texture_reports,'texture_policy':('strict' if req.lod=='textured_max_lod' else 'not_requested')}
    provpath=work/'acquisition.json'
    provpath.write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
    log('[BUILD] 最終座標チェックとBlenderシーン構築')
    if cancel: cancel.check()
    if cancel and blender_runner is run:
        result=blender_runner(cfg,log=log,allow_managed_inputs=True,extra_provenance=provenance,cancel=cancel)
    else:
        result=blender_runner(cfg,log=log,allow_managed_inputs=True,extra_provenance=provenance)
    log(f'[DONE] 自動取得と生成が終了: {result}')
    return result
