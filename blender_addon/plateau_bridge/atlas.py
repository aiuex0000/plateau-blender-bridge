# SPDX-License-Identifier: MIT
"""建物のテクスチャ画像（数千枚）を、数枚の大きな画像（アトラス）へ詰める。

画像1枚ごとにマテリアルを作ると Blender が重くなるため。全画像を同じ倍率で縮め、
決めた枚数のアトラスに収まる一番大きい倍率を選ぶ。画素の読込みと縮小は Blender、
詰め込みは numpy で行う。
"""

import os
import struct

import bpy
import numpy as np

PAGE = 4096
PAD = 2  # 隣の画像の色がにじまないよう、縁の画素を外へ伸ばす幅
# 倍率の候補。半分ずつだと枚数の枠を余らせるので、2 の 1/4 乗ずつ下げる（1 から 1/16 まで）
SCALES = tuple(2.0 ** (-k / 4.0) for k in range(17))
MIN_SIDE = 4


def image_size(path):
    """画像の (幅, 高さ) を、ファイルの先頭だけ読んで返す。JPEG と PNG。読めなければ None。"""
    try:
        with open(path, "rb") as file:
            head = file.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:2] != b"\xff\xd8":
                return None
            file.seek(2)
            while True:
                marker = file.read(4)
                if len(marker) < 4 or marker[0] != 0xFF:
                    return None
                kind, length = marker[1], struct.unpack(">H", marker[2:4])[0]
                # SOF0〜SOF15（DHT・JPG・DAC を除く）に寸法がある
                if 0xC0 <= kind <= 0xCF and kind not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">xHH", file.read(5))
                    return width, height
                file.seek(length - 2, os.SEEK_CUR)
    except (OSError, struct.error):
        return None


def _scaled(sizes, scale):
    return [
        (min(PAGE - 2 * PAD, max(MIN_SIDE, round(w * scale))),
         min(PAGE - 2 * PAD, max(MIN_SIDE, round(h * scale))))
        for w, h in sizes
    ]


def _pack(sizes):
    """棚に並べる詰め方。返すのは画像ごとの (ページ, x, y) とページ数。x, y は余白の内側。"""
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    places = [None] * len(sizes)
    page = x = y = shelf = 0
    for i in order:
        w, h = sizes[i][0] + 2 * PAD, sizes[i][1] + 2 * PAD
        if x + w > PAGE:
            x, y = 0, y + shelf
            shelf = 0
        if y + h > PAGE:
            page, x, y, shelf = page + 1, 0, 0, 0
        places[i] = (page, x + PAD, y + PAD)
        x += w
        shelf = max(shelf, h)
    return places, (page + 1 if sizes else 0)


def plan(sizes, max_pages):
    """max_pages 枚に収まる一番大きい倍率で詰める。返すのは (倍率, 縮めた寸法, 置き場所, 枚数)。"""
    for scale in SCALES:
        scaled = _scaled(sizes, scale)
        places, pages = _pack(scaled)
        if pages <= max_pages:
            return scale, scaled, places, pages
    raise ValueError(
        "テクスチャが多すぎて、アトラス %d 枚に収まりません。範囲を狭めるか、枚数の上限を上げてください"
        % max_pages
    )


def _pixels(path, size):
    """画像を size（幅, 高さ）へ縮めて読む。行は下から。読めなければ None。"""
    image = bpy.data.images.load(path, check_existing=False)
    try:
        if image.size[0] == 0 or image.channels != 4:
            return None
        if tuple(image.size) != size:
            image.scale(size[0], size[1])
        buffer = np.empty(size[0] * size[1] * 4, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        return buffer.reshape(size[1], size[0], 4)
    finally:
        bpy.data.images.remove(image)


class Atlas:
    """詰め終えたアトラス。rects は画像ごとの (ページ, u0, v0, du, dv)。貼れない画像は None。"""

    def __init__(self, images, rects, scale, used, failed):
        self.images = images
        self.rects = rects
        self.scale = scale
        self.used = used
        self.failed = failed


def build(paths, name, save_dir, max_pages=4):
    """paths（画像ファイルの並び、無い物は None）をアトラスに詰め、.blend に埋め込む。"""
    sizes = [image_size(path) if path else None for path in paths]
    known = [i for i, size in enumerate(sizes) if size]
    scale, scaled, places, pages = plan([sizes[i] for i in known], max_pages)

    rects = [None] * len(paths)
    by_page = [[] for _ in range(pages)]
    for slot, i in enumerate(known):
        by_page[places[slot][0]].append((i, scaled[slot], places[slot]))

    os.makedirs(save_dir, exist_ok=True)
    images = []
    failed = len(paths) - len(known)
    for page, items in enumerate(by_page):
        canvas = np.zeros((PAGE, PAGE, 4), dtype=np.float32)
        canvas[:, :, 3] = 1.0
        for i, (w, h), (_page, x, y) in items:
            pixels = _pixels(paths[i], (w, h))
            if pixels is None:
                failed += 1
                continue
            canvas[y:y + h, x:x + w] = pixels
            # 縁の画素を余白へ伸ばす
            canvas[y:y + h, x - PAD:x] = pixels[:, :1]
            canvas[y:y + h, x + w:x + w + PAD] = pixels[:, -1:]
            span = slice(x - PAD, x + w + PAD)
            canvas[y - PAD:y, span] = canvas[y:y + 1, span]
            canvas[y + h:y + h + PAD, span] = canvas[y + h - 1:y + h, span]
            rects[i] = (page, x / PAGE, y / PAGE, w / PAGE, h / PAGE)

        image = bpy.data.images.new("%s_%d" % (name, page), PAGE, PAGE, alpha=False)
        image.pixels.foreach_set(canvas.ravel())
        # JPEG で保存してから埋め込む。そのまま埋め込むと PNG になり .blend が大きくなる
        image.filepath_raw = os.path.join(save_dir, "%s_%d.jpg" % (name, page))
        image.file_format = "JPEG"
        image.save()
        image.pack()
        images.append(image)
    return Atlas(images, rects, scale, len(known), failed)
