"""Stage CityGML and referenced appearance images without mutating source GML.

Remote texture URI resolution is deliberately restricted to the same PLATEAU CMS
asset-tree; it rejects traversal out of the dataset, alternate hosts, schemes,
query strings and archives. A completed manifest is revalidated on each run.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote, unquote
import hashlib
import json
import posixpath
import re
import shutil
import urllib.request
import xml.etree.ElementTree as ET

from .catalog import Source, safe_source_url, _safe_open

SUPPORTED = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}
USER_AGENT = 'PLATEAU-Blender-Bridge/0.7'


@dataclass(frozen=True)
class Asset:
    reference: str
    url: str
    relative: str  # relative to CityGML dataset root, not the enclosing bldg directory


def _dataset_path(gml_url: str) -> tuple[str, str]:
    """Return path to dataset root (ending before /udx/) and path of GML within root."""
    url = urlsplit(safe_source_url(gml_url))
    raw = unquote(url.path)
    if raw.count('/udx/') != 1 or not raw.lower().endswith('.gml'):
        raise ValueError('CityGML URLに /udx/ 階層がありません。画像の相対位置を確定できません')
    prefix, suffix = raw.split('/udx/', 1)
    if not prefix or not suffix or '\\' in raw or '%' in raw:
        raise ValueError('CityGML URLパスの構造が不正')
    return prefix, 'udx/' + suffix


def resolve_asset(gml_url: str, reference: str) -> Asset:
    prefix, gml_inside = _dataset_path(gml_url)
    raw = reference.strip().replace('\\', '/')
    if not raw or len(raw) > 512 or '?' in raw or '#' in raw or '%' in raw or '\x00' in raw:
        raise ValueError('画像参照に禁止された文字があります')
    if urlsplit(raw).scheme or raw.startswith('/') or raw.startswith('//'):
        raise ValueError('絶対URLや外部ホストへの画像参照は使用できません')
    if re.search(r'(^|/)\.(?:/|$)', raw):
        raw = raw.replace('/./', '/')
    result = posixpath.normpath(posixpath.join(posixpath.dirname(gml_inside), raw))
    if result in ('', '.') or result == '..' or result.startswith('../') or result.startswith('/'):
        raise ValueError('画像参照がデータセット外へ出ています')
    if Path(result).suffix.lower() not in SUPPORTED:
        raise ValueError('画像参照に未対応の拡張子があります')
    source_url=urlsplit(gml_url)
    # Preserve asset origin, encode path segments. Do not silently change query/fragment.
    if source_url.query or source_url.fragment:
        raise ValueError('GML URLのquery/fragmentは対応していません')
    path=prefix+'/'+result
    url=urlunsplit((source_url.scheme,source_url.netloc,quote(path,safe='/:-_.~'),'', ''))
    safe_source_url(url)
    return Asset(reference,url,result)


def referenced_images(gml_file: Path, gml_url: str, *, max_images: int=96) -> list[Asset]:
    """Streaming XML parse, recognizes namespaced app:imageURI as local-name imageURI."""
    with gml_file.open('rb') as stream:
        first=stream.read(65536)
        if b'<!DOCTYPE' in first.upper() or b'<!ENTITY' in first.upper():
            raise ValueError('DTD/ENTITY を含むGMLは読み込みません')
    assets: dict[str,Asset] = {}
    try:
        for _,element in ET.iterparse(gml_file,events=('end',)):
            if element.tag.rsplit('}',1)[-1] == 'imageURI' and element.text and element.text.strip():
                asset = resolve_asset(gml_url,element.text)
                if asset.relative not in assets: assets[asset.relative]=asset
                if len(assets)>max_images:
                    raise ValueError(f'画像が{max_images}件を超えました。LODや地域を狭めてください')
            element.clear()
    except ET.ParseError as exc:
        raise ValueError(f'GML XML解析失敗: {gml_file.name}: {exc}') from exc
    return list(assets.values())


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def _download_asset(asset: Asset, target: Path, *, opener, max_file_bytes: int, cancel=None) -> int:
    if target.is_symlink():raise ValueError('保存先がシンボリックリンクです')
    req=urllib.request.Request(asset.url,headers={'User-Agent':USER_AGENT,'Accept':'image/*'})
    temp=target.with_name(target.name+'.part')
    temp.parent.mkdir(parents=True,exist_ok=True)
    size=0
    try:
        with opener(req,timeout=30) as response, temp.open('wb') as out:
            final=safe_source_url(response.geturl())
            # No redirection to a different data path: fail rather than accepting arbitrary assets.
            if final != asset.url:
                raise ValueError(f'画像取得で想定外のリダイレクト: {final}')
            while True:
                if cancel: cancel.check()
                data=response.read(1024*1024)
                if not data:break
                size+=len(data)
                if size>max_file_bytes:raise ValueError('単一画像の容量上限超過')
                out.write(data)
        if size<12:raise ValueError('画像ファイルが短すぎます')
        # Verify the file as a real image; do not cache HTML error pages.
        from PIL import Image
        with Image.open(temp) as img:
            img.verify()
        temp.replace(target)
        return size
    finally:
        temp.unlink(missing_ok=True)


def stage_gml(source: Source, downloaded_gml: Path, root: Path, *, opener=None,
              max_images: int=96, max_total_mb: int=120, log=print, cancel=None) -> tuple[Path, dict]:
    """Stage a single GML + dependent images, resumable per-file on a future call.

    Return staged GML path and report. Same-source cache entries validate digest;
    interrupted downloads use .part and never advertise a ready manifest.
    """
    opener=opener or _safe_open
    prefix, gml_inside=_dataset_path(source.url)
    assets=referenced_images(downloaded_gml,source.url,max_images=max_images)
    ident=hashlib.sha256(source.url.encode('utf-8')).hexdigest()[:20]
    folder=root/ident
    folder.mkdir(parents=True,exist_ok=True)
    gml_target=folder/gml_inside
    gml_target.parent.mkdir(parents=True,exist_ok=True)
    if gml_target.is_symlink():raise ValueError('GML出力がシンボリックリンクです')
    digest=_sha256(downloaded_gml)
    if not gml_target.is_file() or _sha256(gml_target)!=digest:
        tmp=gml_target.with_name(gml_target.name+'.part')
        shutil.copyfile(downloaded_gml,tmp)
        tmp.replace(gml_target)
    manifest=folder/'appearance_ready.json'
    previous={}
    if manifest.is_file():
        try:previous=json.loads(manifest.read_text(encoding='utf-8'))
        except (ValueError,OSError):pass
    if previous.get('gml_sha256')!=digest or previous.get('source_url')!=source.url:
        previous={}
    known=previous.get('images',{})
    total=0
    report_images={}
    for asset in assets:
        if cancel: cancel.check()
        target=folder/asset.relative
        prior=known.get(asset.relative,{})
        if not isinstance(prior,dict):prior={}
        cached=(target.is_file() and not target.is_symlink() and
                prior.get('url')==asset.url and prior.get('bytes')==target.stat().st_size and
                prior.get('sha256')==_sha256(target))
        if cached:
            log(f'[TEXTURE CACHE] {asset.relative}')
        else:
            log(f'[TEXTURE] {asset.relative}')
            _download_asset(asset,target,opener=opener,max_file_bytes=min(30,max_total_mb)*1048576,cancel=cancel)
        size=target.stat().st_size
        total+=size
        if total>max_total_mb*1048576:
            raise ValueError(f'テクスチャ合計が{max_total_mb}MiBを超過しました')
        report_images[asset.relative]={'url':asset.url,'bytes':size,'sha256':_sha256(target)}
        # Persist validated successes even if a later image download fails; resume next time.
        manifest.write_text(json.dumps({'schema':1,'source_url':source.url,'gml_sha256':digest,
                                        'images':report_images,'complete':False},ensure_ascii=False,indent=2),encoding='utf-8')
    report={'schema':1,'source_url':source.url,'gml_sha256':digest,'images':report_images,
            'total_image_bytes':total,'complete':True}
    manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return gml_target, {'images':len(assets),'bytes':total,'path':str(gml_target)}
