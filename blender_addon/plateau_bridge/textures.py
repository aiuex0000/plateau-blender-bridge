# SPDX-License-Identifier: MIT
"""CityGML が参照する建物テクスチャ画像を安全に保存する。"""

import concurrent.futures
import hashlib
import os
import posixpath
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import catalog

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
_MIB = 1024 * 1024


def _dataset_path(gml_url):
    try:
        catalog.safe_source_url(gml_url)
        parsed = urllib.parse.urlsplit(gml_url)
    except (TypeError, ValueError):
        raise ValueError("CityGML URLが不正です")
    raw = urllib.parse.unquote(parsed.path)
    if raw.count("/udx/") != 1 or not raw.lower().endswith(".gml"):
        raise ValueError("CityGML URLに /udx/ 階層がありません")
    prefix, suffix = raw.split("/udx/", 1)
    if not prefix or not suffix or "\\" in raw or "%" in raw:
        raise ValueError("CityGML URLパスの構造が不正です")
    return prefix, "udx/" + suffix


def resolve_asset(gml_url, reference):
    """CityGML URLと imageURI から画像URLとデータセット内の相対pathを返す。"""
    prefix, gml_inside = _dataset_path(gml_url)
    if not isinstance(reference, str):
        raise ValueError("画像参照が不正です")
    raw = reference.strip().replace("\\", "/")
    if (not raw or len(raw) > 512 or "?" in raw or "#" in raw or
            "%" in raw or "\x00" in raw):
        raise ValueError("画像参照に禁止された文字があります")
    try:
        parsed_reference = urllib.parse.urlsplit(raw)
    except ValueError:
        raise ValueError("画像参照が不正です")
    if parsed_reference.scheme or raw.startswith("/") or raw.startswith("//"):
        raise ValueError("絶対URLや外部ホストへの画像参照は使用できません")
    if re.search(r"(^|/)\.(?:/|$)", raw):
        raw = raw.replace("/./", "/")
    relative = posixpath.normpath(posixpath.join(posixpath.dirname(gml_inside), raw))
    if (relative in ("", ".") or relative == ".." or relative.startswith("../") or
            relative.startswith("/")):
        raise ValueError("画像参照がデータセット外へ出ています")
    if Path(relative).suffix.lower() not in SUPPORTED:
        raise ValueError("画像参照に未対応の拡張子があります")
    source = urllib.parse.urlsplit(gml_url)
    if source.query or source.fragment:
        raise ValueError("GML URLのquery/fragmentは対応していません")
    url = urllib.parse.urlunsplit((
        source.scheme, source.netloc,
        urllib.parse.quote(prefix + "/" + relative, safe="/:-_.~"), "", "",
    ))
    try:
        catalog.safe_source_url(url)
    except (TypeError, ValueError):
        raise ValueError("未許可のデータ提供元です")
    return url, relative


def _download(url, target, extension, opener, max_file_bytes):
    request = urllib.request.Request(
        url, headers={"User-Agent": catalog.USER_AGENT, "Accept": "image/*"}
    )
    partial = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    header = bytearray()
    try:
        with opener(request, timeout=30) as response:
            if response.geturl() != url:
                raise ValueError("画像取得で想定外のリダイレクトです")
            with partial.open("wb") as output:
                while True:
                    data = response.read(1024 * 1024)
                    if not data:
                        break
                    size += len(data)
                    if size > max_file_bytes:
                        raise ValueError("単一画像が上限%d MiBを超えました" %
                                         (max_file_bytes // _MIB))
                    if len(header) < 4:
                        header.extend(data[:4 - len(header)])
                    output.write(data)
        if size < 12:
            raise ValueError("画像ファイルが短すぎます")
        if extension in (".jpg", ".jpeg"):
            valid = header.startswith(b"\xff\xd8")
        elif extension == ".png":
            valid = header.startswith(b"\x89PNG")
        else:
            valid = header.startswith((b"II*\0", b"MM\0*"))
        if not valid:
            raise ValueError("画像データの形式が拡張子と一致しません")
        os.replace(str(partial), str(target))
        return size
    finally:
        if partial.exists():
            partial.unlink()


class TextureStore:
    """テクスチャ画像を保存し、取得量と件数の上限を管理する。"""

    def __init__(self, root, max_total_mb=300, max_file_mb=30, max_images=6000,
                 opener=None, workers=8):
        self.root = Path(root).resolve()
        self.max_total_bytes = max_total_mb * _MIB
        self.max_file_bytes = max_file_mb * _MIB
        self.max_images = max_images
        self.opener = opener or catalog._OPEN
        self.workers = workers
        self.total_bytes = 0
        self.downloaded_bytes = 0
        self.downloaded_count = 0
        self._sizes = {}
        self._downloaded = set()

    def fetch(self, gml_url, references):
        """imageURI の反復可能な参照を取得し、参照から絶対pathへの辞書を返す。"""
        ident = hashlib.sha256(gml_url.encode("utf-8")).hexdigest()[:20]
        outputs = {}
        items = {}
        for reference in references:
            url, relative = resolve_asset(gml_url, reference)
            # 相対pathのまま置くと全長が Windows の260文字に届きうるので、短い名前にする
            short = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
            target = self.root / ident / (short + Path(relative).suffix.lower())
            key = str(target)
            outputs[reference] = key
            items.setdefault(key, (url, target, Path(relative).suffix.lower()))

        new_items = {key: item for key, item in items.items() if key not in self._sizes}
        count = len(self._sizes) + len(new_items)
        if count > self.max_images:
            raise ValueError("異なる画像が上限%d件を超えます" % self.max_images)

        cached = {}
        pending = {}
        for key, (url, target, extension) in new_items.items():
            if target.is_symlink():
                raise ValueError("保存先がシンボリックリンクです")
            if target.is_file() and target.stat().st_size >= 12:
                size = target.stat().st_size
                if size > self.max_file_bytes:
                    raise ValueError("単一画像が上限%d MiBを超えます" %
                                     (self.max_file_bytes // _MIB))
                cached[key] = size
            else:
                pending[key] = (url, target, extension)
        cached_total = self.total_bytes + sum(cached.values())
        if cached_total > self.max_total_bytes:
            raise ValueError("テクスチャ合計が上限%d MiBを超えます" %
                             (self.max_total_bytes // _MIB))

        completed = {}
        failures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(self._fetch_one, url, target, extension): key
                for key, (url, target, extension) in pending.items()
            }
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    completed[key] = future.result()
                except Exception as error:
                    failures.append(error)

        available = dict(cached)
        available.update(completed)
        for key, size in available.items():
            if key not in self._sizes:
                self._sizes[key] = size
                self.total_bytes += size
                if key in completed and key not in self._downloaded:
                    self._downloaded.add(key)
                    self.downloaded_bytes += size
                    self.downloaded_count += 1
        if failures:
            raise failures[0]
        if self.total_bytes > self.max_total_bytes:
            raise ValueError("テクスチャ合計が上限%d MiBを超えます" %
                             (self.max_total_bytes // _MIB))
        return outputs

    def _fetch_one(self, url, target, extension):
        return _download(url, target, extension, self.opener, self.max_file_bytes)
