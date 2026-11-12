# SPDX-License-Identifier: MIT
"""PLATEAU のデータカタログ API から CityGML（建物）を探して取得する。bpy に依存しない。

exe 版の plateau_blend/catalog.py と同じ探し方・選び方・提供元の制限。
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.plateauview.mlit.go.jp/datacatalog/citygml/"
ALLOWED_HOSTS = {"assets.cms.plateau.reearth.io", "api.plateauview.mlit.go.jp"}
USER_AGENT = "PLATEAU-Blender-Bridge-Addon/0.1"
ATTRIBUTION = "出典：国土交通省 PLATEAU"


def safe_source_url(value):
    url = urllib.parse.urlsplit(value)
    if (
        url.scheme != "https"
        or url.hostname not in ALLOWED_HOSTS
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        raise ValueError("未許可のデータ提供元です: %s" % value)
    return value


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_source_url(newurl)  # 転送先も、たどる前に確かめる
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPEN = urllib.request.build_opener(_SafeRedirect()).open


class NoPlateauData(ValueError):
    def __init__(self):
        super().__init__(
            "この場所には PLATEAU の3D都市データがまだありません。"
            "対応している都市で試してください（例: 新宿駅西口 35.6905, 139.6995）"
        )


class Source:
    """取得する CityGML 1ファイル。"""

    def __init__(self, city, mesh, year, url, size, max_lod):
        self.city = city
        self.mesh = mesh
        self.year = year
        self.url = url
        self.size = size  # バイト数。API が教えてくれない時は None
        self.max_lod = max_lod


def fetch_catalog(bbox):
    """範囲 bbox（西, 南, 東, 北 の経度緯度）に掛かる都市の一覧を API から取る。"""
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValueError("範囲は 西, 南, 東, 北（経度緯度）で指定します")
    condition = "r:%.6f,%.6f,%.6f,%.6f" % (west, south, east, north)
    url = API + urllib.parse.quote(condition, safe=":,") + "?" + urllib.parse.urlencode(
        {"types": "bldg"}
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with _OPEN(request, timeout=30) as response:
            payload = response.read(15000001)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise NoPlateauData() from error
        raise
    if len(payload) > 15000000:
        raise ValueError("PLATEAU API の応答が大きすぎます")
    data = json.loads(payload)
    if isinstance(data, dict):
        if "error" in data:
            raise ValueError("PLATEAU API: " + str(data["error"])[:300])
        data = data["cities"] if isinstance(data.get("cities"), list) else [data]
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError("PLATEAU API の応答の形式が想定と違います")
    return data


def select_sources(records, max_files=24, max_known_mb=600):
    """メッシュごとに一番新しい年度のファイルを選ぶ。古い年度にしか無いメッシュは補う。"""
    newest = {}
    for city in records:
        code = str(city.get("cityCode", ""))
        files = city.get("files")
        if not isinstance(files, dict):
            continue
        year = int(city.get("year") or 0)
        registered = int(city.get("registrationYear") or 0)
        items = files.get("bldg", [])
        if not isinstance(items, list):
            raise ValueError("PLATEAU のファイル一覧の形式が想定と違います")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("PLATEAU のファイル項目の形式が想定と違います")
            url = safe_source_url(str(item["url"]))
            size = item.get("fileSize")
            size = int(size) if size is not None else None
            if size is not None and size < 0:
                raise ValueError("ファイルサイズが不正です")
            mesh = str(item.get("code", ""))
            if not re.fullmatch(r"[0-9]{4,10}", mesh):
                raise ValueError("メッシュコードが不正です")
            source = Source(
                str(city.get("cityName", "")), mesh, year, url, size, int(item.get("maxLod") or 0)
            )
            score = (year, registered)
            key = (code, mesh)
            if key not in newest or score > newest[key][0]:
                newest[key] = (score, source)

    sources = []
    seen = set()
    for key in sorted(newest):
        source = newest[key][1]
        # 区の境のメッシュは、両方の区が同じ中身で公開している。同じ物は一度だけ取る
        twin = (source.mesh, source.year, source.size) if source.size else None
        if source.url in seen or (twin and twin in seen):
            continue
        seen.add(source.url)
        if twin:
            seen.add(twin)
        sources.append(source)
    if not sources:
        raise NoPlateauData()
    known = sum(source.size or 0 for source in sources)
    if len(sources) > max_files or known > max_known_mb * 1024 * 1024:
        raise ValueError(
            "範囲が広すぎます: CityGML %d ファイル / 分かっている分で %.1f MiB。"
            "範囲を狭めてください（上限 %d ファイル・%d MiB）"
            % (len(sources), known / 1048576.0, max_files, max_known_mb)
        )
    return sources


def download(source, dest_root, max_file_mb=300, progress=None):
    """CityGML を保存し、ファイルの場所と「今回新しく取ったか」を返す。

    progress(取れたバイト数, 全体のバイト数か None) を、1 MiB 取るごとに呼ぶ。
    progress が例外を上げると取得を止め、書きかけのファイルを消す（取り消しに使う）。
    """
    os.makedirs(dest_root, exist_ok=True)
    digest = hashlib.sha256(source.url.encode()).hexdigest()[:20]
    target = os.path.join(dest_root, "bldg_%s_%s.gml" % (source.mesh, digest))
    meta = target[:-4] + ".json"
    if os.path.isfile(target) and os.path.isfile(meta):
        try:
            with open(meta, encoding="utf-8") as file:
                prior = json.load(file)
            if prior.get("url") == source.url and prior.get("size") == os.path.getsize(target) > 20:
                return target, False
        except (OSError, ValueError, AttributeError):
            pass  # 壊れた記録は無視して取り直す

    limit = max_file_mb * 1024 * 1024
    if source.size is not None and source.size > limit:
        raise ValueError("メッシュ %s の CityGML が上限 %d MiB を超えます" % (source.mesh, max_file_mb))
    request = urllib.request.Request(source.url, headers={"User-Agent": USER_AGENT})
    partial = target + ".part"
    total = 0
    try:
        with _OPEN(request, timeout=45) as response, open(partial, "wb") as out:
            safe_source_url(response.geturl())
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError("ダウンロードが上限 %d MiB を超えました" % max_file_mb)
                out.write(chunk)
                if progress is not None:
                    progress(total, source.size)
        if total < 20:
            raise ValueError("CityGML が空か、短すぎます")
        os.replace(partial, target)
        with open(meta, "w", encoding="utf-8") as file:
            json.dump(
                {"url": source.url, "size": total, "year": source.year,
                 "city": source.city, "mesh": source.mesh},
                file, ensure_ascii=False, indent=2,
            )
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return target, True
