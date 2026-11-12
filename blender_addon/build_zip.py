"""アドオンを、Blender へそのまま入れられる zip にまとめる。

zip の中はフォルダ1つ（例: plateau_bridge/）にする。この形なら
4.2 以降の「ディスクからインストール」でも、4.1 以前の「インストール」でも入る。

    python blender_addon/build_zip.py            # 全部
    python blender_addon/build_zip.py plateau_bridge
"""

import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
NAMES = ("plateau_bridge", "plateau_bridge_trial")


def build(name):
    source = ROOT / name
    manifest = (source / "blender_manifest.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', manifest, re.MULTILINE)
    if match is None:
        raise SystemExit("version が見つかりません: %s" % name)
    out = ROOT / "dist" / ("%s-%s.zip" % (name, match.group(1)))
    out.parent.mkdir(exist_ok=True)
    files = sorted(source.glob("*.py")) + [source / "blender_manifest.toml"]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, "%s/%s" % (name, file.name))
        archive.write(ROOT.parent / "LICENSE", "%s/LICENSE" % name)
    print(out)


if __name__ == "__main__":
    for name in sys.argv[1:] or NAMES:
        build(name)
