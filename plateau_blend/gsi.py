"""Fetch GSI orthophoto + RGB DEM tiles, reproject directly into a local metric CRS.

No GDAL/QGIS required. All raster sampling uses pyproj always_xy=True.
No-data DEM samples abort generation, rather than fabricate zero-elevation ground.
"""
from __future__ import annotations
from io import BytesIO
from math import atan, asinh, cos, degrees, floor, log, pi, radians, sinh, tan
from pathlib import Path
import hashlib
import json
import math
import urllib.error
import urllib.request
import urllib.parse
from PIL import Image
from pyproj import CRS, Transformer

BASE='https://cyberjapandata.gsi.go.jp/xyz'
PHOTO='seamlessphoto'
DEM_SOURCES=('dem5a_png','dem5b_png','dem5c_png','dem_png')
TERMS='https://maps.gsi.go.jp/help/use.html'
ATTRIBUTION='国土地理院（地理院タイル：全国最新写真・標高タイル）'


def _safe_gsi_url(url: str) -> bool:
    p=urllib.parse.urlsplit(url)
    return (p.scheme=='https' and p.hostname=='cyberjapandata.gsi.go.jp'
            and not p.username and not p.password and p.port in (None,443))


class _GsiSafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _safe_gsi_url(newurl):
            raise ValueError('地理院タイルの外部ホストへのリダイレクトを拒否しました')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


_GSI_OPEN=urllib.request.build_opener(_GsiSafeRedirect()).open


def fractional_tile(lon: float, lat: float, zoom: int) -> tuple[float,float]:
    if not (-180<=lon<=180 and -85.05112878<lat<85.05112878): raise ValueError('WebMercator範囲外')
    n=2**zoom
    return ((lon+180)/360*n, (1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)


def tile_bbox_from_xy(bounds: tuple[float,float,float,float], epsg: str, zoom: int) -> tuple[int,int,int,int]:
    xmin,ymin,xmax,ymax=bounds
    inv=Transformer.from_crs(CRS.from_user_input(epsg),CRS.from_epsg(4326),always_xy=True)
    pts=[inv.transform(x,y) for x in (xmin,(xmin+xmax)/2,xmax) for y in (ymin,(ymin+ymax)/2,ymax)]
    v=[fractional_tile(lon,lat,zoom) for lon,lat in pts]
    return (floor(min(x for x,y in v)),floor(min(y for x,y in v)),
            floor(max(x for x,y in v)),floor(max(y for x,y in v)))


def photo_zoom(bounds,epsg,wanted=16,max_tiles=64):
    for z in range(wanted,13,-1):
        a,b,c,d=tile_bbox_from_xy(bounds,epsg,z)
        if (c-a+1)*(d-b+1)<=max_tiles: return z
    raise ValueError('地理院写真の取得範囲が大きすぎます（z14で64タイル超）')


def decode_dem(r:int,g:int,b:int) -> float|None:
    v=(r<<16)+(g<<8)+b
    return None if v==8388608 else (v-16777216 if v>8388608 else v)*0.01


class GsiTiles:
    def __init__(self, root:Path, *, opener=None, max_tiles=160):
        self.root=root;self.root.mkdir(parents=True,exist_ok=True)
        self.opener=opener or _GSI_OPEN
        self.images: dict[tuple[str,int,int,int],Image.Image|None]={}
        self.max_tiles=max_tiles
        self.tile_attempts=0
        self.cancel=None
        self.missing:set[tuple[str,int,int,int]]=set()
    def get(self,layer:str,z:int,x:int,y:int,required:bool=False) -> Image.Image|None:
        if self.cancel: self.cancel.check()
        if layer not in (*DEM_SOURCES,PHOTO): raise ValueError('未許可のGSIレイヤー')
        if z<0 or not (0<=x<2**z and 0<=y<2**z): raise ValueError('タイル座標が範囲外')
        key=(layer,z,x,y)
        if key in self.images:
            if required and self.images[key] is None: raise ValueError(f'航空写真のタイル欠損: {key}')
            return self.images[key]
        self.tile_attempts+=1
        if self.tile_attempts>self.max_tiles: raise ValueError('GSIタイル取得数上限に達しました')
        extension='jpg' if layer==PHOTO else 'png'
        path=self.root/layer/str(z)/str(x)/f'{y}.{extension}'
        def cached_image():
            if not path.is_file(): return None
            try:
                with Image.open(path) as im:
                    # A tile of the wrong dimensions is unsafe even when decodable.
                    if im.size != (256,256): raise ValueError('GSIタイルが256x256ではありません')
                    converted=im.convert('RGB').copy()
                return converted
            except (OSError, ValueError):
                # A truncated cache must be re-fetched, not treated as valid.
                path.unlink(missing_ok=True)
                return None

        image=cached_image()
        if image is None:
            path.parent.mkdir(parents=True,exist_ok=True)
            url=f'{BASE}/{layer}/{z}/{x}/{y}.{extension}'
            tmp=path.with_suffix('.part')
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'PLATEAU-Blender-Bridge/0.9'})
                with self.opener(req,timeout=30) as r:
                    if not _safe_gsi_url(r.geturl()):
                        raise ValueError('GSIタイルが許可外ホストにリダイレクトしました')
                    data=r.read(2_000_001)
                if len(data)>2_000_000: raise ValueError('タイルが2MB超')
                with Image.open(BytesIO(data)) as im:
                    im.load()
                    if im.size != (256,256): raise ValueError('GSIタイルが256x256ではありません')
                    image=im.convert('RGB').copy()
                if self.cancel: self.cancel.check()
                tmp.write_bytes(data)
                tmp.replace(path)
            except urllib.error.HTTPError as exc:
                if exc.code != 404: raise
                self.images[key]=None;self.missing.add(key)
                if required: raise ValueError(f'航空写真のタイル欠損: {url}') from exc
                return None
            finally:
                tmp.unlink(missing_ok=True)
        self.images[key]=image
        return image

    def pixel(self,layer:str,lon:float,lat:float,z:int,required=False):
        a,b=fractional_tile(lon,lat,z)
        tx,ty=floor(a),floor(b)
        image=self.get(layer,z,tx,ty,required=required)
        if image is None:return None
        return image.getpixel((min(255,int((a-tx)*256)),min(255,int((b-ty)*256))))

    def height(self,lon:float,lat:float,z=14) -> float|None:
        for layer in DEM_SOURCES:
            if layer=='dem_png' and z>14: continue
            pix=self.pixel(layer,lon,lat,z)
            if pix is None: continue
            h=decode_dem(*pix)
            if h is not None: return h
        return None


def xy_bounds_of_obj(bbox_xyz:tuple[float,...],padding:float=20.0)->tuple[float,float,float,float]:
    return bbox_xyz[0]-padding,bbox_xyz[1]-padding,bbox_xyz[3]+padding,bbox_xyz[4]+padding


def reproject_photo(tiles:GsiTiles,bounds:tuple[float,float,float,float],epsg:str,
                    output_png:Path,output_pgw:Path, *, zoom=16,max_dimension=2048,log=print)->dict:
    xmin,ymin,xmax,ymax=bounds
    if xmax<=xmin or ymax<=ymin: raise ValueError('写真範囲が空')
    used_zoom=photo_zoom(bounds,epsg,wanted=zoom)
    # Longitude/latitude is only needed to calculate the ground pixel size.
    # For the raster itself, perform one *vectorized* CRS -> WebMercator
    # transformation per scanline rather than millions of individual calls.
    geographic=Transformer.from_crs(CRS.from_user_input(epsg),CRS.from_epsg(4326),always_xy=True)
    cx,cy=geographic.transform((xmin+xmax)/2,(ymin+ymax)/2)
    pixel_m=156543.03392804097*cos(radians(cy))/(2**used_zoom)
    w=max(16, min(max_dimension,math.ceil((xmax-xmin)/pixel_m)))
    h=max(16, min(max_dimension,math.ceil((ymax-ymin)/pixel_m)))
    dx=(xmax-xmin)/w;dy=(ymax-ymin)/h
    output_png.parent.mkdir(parents=True,exist_ok=True)
    output_pgw.parent.mkdir(parents=True,exist_ok=True)
    image=Image.new('RGB',(w,h))
    log(f'[GSI PHOTO] z={used_zoom}, {w}x{h}, {len(tiles.images)} tiles cached')
    metric=Transformer.from_crs(CRS.from_user_input(epsg),CRS.from_epsg(3857),always_xy=True)
    xmeters=[xmin+(col+0.5)*dx for col in range(w)]
    world=40075016.685578488
    half=world/2
    scale=(2**used_zoom)/world
    active_pixels={}
    for row in range(h):
        if tiles.cancel: tiles.cancel.check()
        y=ymax-(row+0.5)*dy
        # pyproj processes an entire row in native code.
        mxs,mys=metric.transform(xmeters,[y]*w)
        scan=bytearray(3*w)
        for col,(mx,my) in enumerate(zip(mxs,mys)):
            if not math.isfinite(mx) or not math.isfinite(my):
                raise ValueError('航空写真の投影座標が非有限値です')
            fx=(mx+half)*scale
            fy=(half-my)*scale
            tx,ty=math.floor(fx),math.floor(fy)
            key=(used_zoom,tx,ty)
            pixels=active_pixels.get(key)
            if pixels is None:
                tile=tiles.get(PHOTO,used_zoom,tx,ty,required=True)
                pixels=tile.load()
                active_pixels[key]=pixels
            r,g,b=pixels[min(255,max(0,int((fx-tx)*256))),
                         min(255,max(0,int((fy-ty)*256)))]
            i=col*3
            scan[i]=r;scan[i+1]=g;scan[i+2]=b
        image.paste(Image.frombytes('RGB',(w,1),bytes(scan)),(0,row))
    # Pixel-center affine; WorldFile(A,D,B,E,C,F)
    pgw=f'{dx:.15g}\n0\n0\n{-dy:.15g}\n{xmin+dx/2:.15g}\n{ymax-dy/2:.15g}\n'
    tmp=output_png.with_suffix('.tmp.png')
    image.save(tmp);tmp.replace(output_png)
    output_pgw.write_text(pgw,encoding='utf-8')
    result={'image_size':[w,h],'zoom':used_zoom,'bounds_xy':list(bounds),'tiles':tiles.tile_attempts,
            'source':f'{BASE}/{PHOTO}/{{z}}/{{x}}/{{y}}.jpg','attribution':ATTRIBUTION,'terms':TERMS}
    return result


def make_terrain(tiles:GsiTiles,bounds:tuple[float,float,float,float],epsg:str,
                 output:Path, *, resolution=100,zoom=14,log=print)->dict:
    xmin,ymin,xmax,ymax=bounds
    longest=max(xmax-xmin,ymax-ymin)
    nx=max(2,math.ceil(resolution*(xmax-xmin)/longest));ny=max(2,math.ceil(resolution*(ymax-ymin)/longest))
    inv=Transformer.from_crs(CRS.from_user_input(epsg),CRS.from_epsg(4326),always_xy=True)
    values=[];valid=0
    for j in range(ny+1):
        if tiles.cancel: tiles.cancel.check()
        y=ymin+(ymax-ymin)*j/ny
        for i in range(nx+1):
            x=xmin+(xmax-xmin)*i/nx
            lon,lat=inv.transform(x,y)
            height=tiles.height(lon,lat,zoom)
            if height is not None:valid+=1
            values.append((x,y,height))
    coverage=valid/len(values)
    if valid != len(values):
        raise ValueError(f'GSI DEMの有効標高が{coverage:.1%}。欠落頂点を0mに置換せず停止します')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8') as f:
        f.write('# GSI PNG DEM generated in ' + epsg+'\n')
        for x,y,z in values:
            assert z is not None  # Checked before output file is opened.
            f.write(f'v {x:.6f} {y:.6f} {z:.3f}\n')
        for j in range(ny):
            for i in range(nx):
                a=j*(nx+1)+i
                f.write(f'f {a+1} {a+2} {a+nx+2}\nf {a+1} {a+nx+2} {a+nx+1}\n')
    log(f'[GSI DEM] {nx}x{ny} cells; valid samples {coverage:.1%}')
    return {'samples':[nx+1,ny+1],'valid_ratio':coverage,'zoom':zoom,
            'attribution':ATTRIBUTION,'terms':TERMS}
