"""PLATEAU trial CityGML per-file discovery. Strict URL/budget validation."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import urllib.parse
import urllib.request
from xml.parsers import expat

API = 'https://api.plateauview.mlit.go.jp/datacatalog/citygml/'
ALLOWED_ASSET_HOSTS = {'assets.cms.plateau.reearth.io', 'api.plateauview.mlit.go.jp'}
USER_AGENT = 'PLATEAU-Blender-Bridge/0.9 (desktop research tool)'


def safe_source_url(value: str) -> str:
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or url.hostname not in ALLOWED_ASSET_HOSTS or url.username or url.password or url.port not in (None,443):
        raise ValueError(f'未許可のデータ提供元: {value}')
    return value


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_source_url(newurl)  # Validate *before* following the redirect.
        return super().redirect_request(req,fp,code,msg,headers,newurl)


_safe_open=urllib.request.build_opener(_SafeRedirect()).open


def _sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):h.update(chunk)
    return h.hexdigest()


def citygml_query(condition: str, types: tuple[str, ...] = ('bldg',)) -> str:
    if not re.fullmatch(r'(?:m:|mm:)[0-9,]{6,120}|r:-?[0-9.,-]{5,100}|g:[^/?#]{1,80}',condition):
        raise ValueError('範囲は r:lng1,lat1,lng2,lat2 / m:mesh / g:地名 を指定してください')
    if condition.startswith('r:'):
        coords = tuple(map(float, condition[2:].split(',')))
        if len(coords)!=4 or not (-180<=coords[0]<coords[2]<=180 and -85<=coords[1]<coords[3]<=85):
            raise ValueError('r: の範囲は west,south,east,north（経度緯度）です')
    if any(t not in ('bldg','dem') for t in types): raise ValueError('未対応の地物型')
    return API + urllib.parse.quote(condition, safe=':,') + '?' + urllib.parse.urlencode({'types':','.join(types)})


def fetch_catalog(condition: str, *, opener=None) -> list[dict]:
    opener = opener or _safe_open
    req = urllib.request.Request(citygml_query(condition),headers={'User-Agent':USER_AGENT, 'Accept':'application/json'})
    with opener(req, timeout=30) as response:
        payload = response.read(15_000_001)
    if len(payload)>15_000_000: raise ValueError('PLATEAU APIレスポンスが大きすぎます')
    data = json.loads(payload)
    if isinstance(data,dict):
        if 'error' in data: raise ValueError('PLATEAU API: '+str(data['error'])[:300])
        # The documented response is a city object; some API variants return
        # a 'cities' envelope. Reject other schemas rather than zero results.
        data=data['cities'] if isinstance(data.get('cities'),list) else [data]
    if not isinstance(data,list) or any(not isinstance(row,dict) for row in data):
        raise ValueError('PLATEAU API応答形式が不正')
    return data


@dataclass(frozen=True)
class Source:
    kind: str
    city: str
    mesh: str
    year: int
    url: str
    size: int | None
    max_lod: int


def select_sources(records: list[dict], kind: str = 'bldg', *, max_files: int = 24,
                   max_known_mb: int = 600) -> list[Source]:
    # A city's newest *dataset* may omit meshes available in earlier years.
    # Prefer the newest year PER (city code, mesh) and supplement missing
    # mesh cells from historical releases. Preserve survey year in provenance.
    # This is not guaranteed seamless; warn about mixed-year sources upstream.
    newest: dict[tuple[str,str], tuple[tuple[int,int],Source]] = {}
    for city in records:
        code=str(city.get('cityCode',''))
        files=city.get('files')
        if not isinstance(files,dict): continue
        year=int(city.get('year') or 0)
        registered=int(city.get('registrationYear') or 0)
        items=files.get(kind,[])
        if not isinstance(items,list): raise ValueError('PLATEAUファイルリストが不正')
        for item in items:
            if not isinstance(item,dict): raise ValueError('PLATEAUファイル項目が不正')
            url=safe_source_url(str(item['url']))
            size=item.get('fileSize')
            size=int(size) if size is not None else None
            if size is not None and size<0: raise ValueError('不正なファイルサイズ')
            mesh=str(item.get('code',''))
            if not re.fullmatch(r'[0-9]{4,10}',mesh): raise ValueError('メッシュコードが不正')
            key=(code,mesh)
            value=Source(kind,str(city.get('cityName','')),mesh,year,url,size,int(item.get('maxLod') or 0))
            score=(year,registered)
            if key not in newest or score>newest[key][0]:
                newest[key]=(score,value)
    sources=[]
    seen=set()
    for key in sorted(newest):
        source=newest[key][1]
        # Ward-border meshes are republished byte-identically by each ward (e.g. Shinjuku/Shibuya);
        # same mesh+year+size is fetched once. auto.generate still dedups by content hash.
        twin=(source.mesh,source.year,source.size) if source.size else None
        if source.url in seen or (twin and twin in seen):continue
        seen.add(source.url)
        if twin: seen.add(twin)
        sources.append(source)
    if not sources: raise ValueError(f'指定範囲に {kind} データが見つかりません')
    known = sum(s.size or 0 for s in sources)
    if len(sources)>max_files or known>max_known_mb*1024*1024:
        raise ValueError(f'広すぎる範囲: {len(sources)} GML / 判明分 {known/1048576:.1f} MiB。範囲を狭めてください（上限 {max_files}件・{max_known_mb}MiB）')
    return sources


def download(source: Source, dest_root: Path, *, opener=None, max_file_mb: int = 300, cancel=None) -> Path:
    """Atomic streaming download with one retry-on-next-run cache. Untrusted redirects denied."""
    opener = opener or _safe_open
    if cancel: cancel.check()
    dest_root.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(source.url.encode()).hexdigest()[:20]
    target=dest_root/f'{source.kind}_{source.mesh}_{digest}.gml'
    meta=target.with_suffix('.json')
    if target.is_symlink() or meta.is_symlink():
        raise ValueError('GMLキャッシュにシンボリックリンクがあります')
    if target.is_file() and meta.is_file():
        try:
            prior=json.loads(meta.read_text(encoding='utf-8'))
            if (isinstance(prior, dict) and prior.get('url')==source.url and
                    prior.get('size')==target.stat().st_size and
                    target.stat().st_size>10 and prior.get('sha256')==_sha256_file(target)):
                validate_citygml(target)
                return target
        except (OSError, ValueError, TypeError):
            # Recover from malformed metadata or invalid cached XML.
            pass
    req=urllib.request.Request(source.url,headers={'User-Agent':USER_AGENT})
    limit=max_file_mb*1024*1024
    if source.size is not None and source.size>limit: raise ValueError(f'{source.mesh}: 単独のGMLが上限を超えます')
    tmp=target.with_suffix('.part')
    total=0
    try:
        with opener(req,timeout=45) as r, tmp.open('wb') as out:
            safe_source_url(r.geturl())
            while True:
                if cancel: cancel.check()
                chunk=r.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>limit: raise ValueError(f'ダウンロード上限 {max_file_mb}MiB を超過')
                out.write(chunk)
        if total < 20: raise ValueError('GMLファイルが空または短すぎます')
        # Reject HTML error pages even when they start with an XML declaration.
        validate_citygml(tmp)
        if cancel: cancel.check()
        tmp.replace(target)
        tmpmeta=meta.with_suffix('.json.part')
        tmpmeta.write_text(json.dumps({'url':source.url,'size':total,'sha256':_sha256_file(target),
                                      'year':source.year,'city':source.city,'mesh':source.mesh},ensure_ascii=False,indent=2),encoding='utf-8')
        tmpmeta.replace(meta)
        return target
    finally:
        if tmp.exists(): tmp.unlink()


def validate_citygml(path: Path) -> None:
    """Stream-parse the *whole* XML without retaining the large CityGML tree.

    The old root-only gate accepted a truncated CityModel or malformed trailing
    content and only detected failure much later inside nusamai. Expat validates
    entire syntax while StartDoctypeDeclHandler refuses entity declarations.
    It is a well-formedness check, *not* a CityGML schema validator.
    """
    parser=expat.ParserCreate(namespace_separator='}')
    observed=[]

    def first_start(name, attrs):
        if not observed:
            if name.rsplit('}',1)[-1]!='CityModel':
                raise ValueError(f'CityGMLのルート要素が違います: {name}')
            observed.append(True)
        # Avoid per-element allocations: no tree is constructed.

    def reject_doctype(*args):
        raise ValueError('DTD/ENTITY を含むGMLは拒否')

    def reject_external(*args):
        raise ValueError('外部XMLエンティティを含むGMLは拒否')

    parser.StartElementHandler=first_start
    parser.StartDoctypeDeclHandler=reject_doctype
    parser.ExternalEntityRefHandler=reject_external
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                parser.Parse(block,False)
            parser.Parse(b'',True)
    except expat.ExpatError as exc:
        if not observed:
            raise ValueError('CityGMLのルート要素 CityModel が見つかりません') from exc
        raise ValueError(f'CityGML XML解析失敗: {exc}') from exc
    if not observed:
        raise ValueError('CityGMLのルート要素 CityModel が見つかりません')
