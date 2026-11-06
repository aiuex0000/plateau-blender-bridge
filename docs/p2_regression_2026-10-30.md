# P2 回帰確認（2026-10-30）

対象: `db5270b`（CLI結果JSON・起動batの固定パス除去）を含む再ビルド。配布先 `C:\Users\You\Workspace\Workbench\Projects\PLATEAU-Blender-Bridge\app\PLATEAU-Blender-Bridge.exe`（v0.10.0）
環境: K12、Blender 5.2.3 LTS

## ビルドと配置

- `--distpath dist-next --workpath build-next` でビルド → self-check 合格 → `dist` へ昇格（旧 `dist` は `dist-old\dist-2026-10-29`）。ZIP を作り直した。
- 配布先 `app` を入れ替え（旧版は `app-prev-2026-10-29`、旧ZIPは `PLATEAU-Blender-Bridge-prev-2026-10-29.zip`）。1,085ファイルのハッシュ一致、配布先 self-check 合格（`verification\self-check-2026-10-30-p2.json`）。

## 結果

| 項目 | 結果 |
| --- | --- |
| 新宿 `r:139.690,35.686,139.694,35.690` / `textured_max_lod`（配布EXEの `--cli`） | exit 0、107秒、`shinjuku.blend` 13.4MB。`shinjuku.result.json` は `ok: true`、`verify_json` の場所も入る。検証: EPSG:6677、建物・地形メッシュ各1、地形のUV不良0、テクスチャ同梱 |
| PATH から Python を外した起動 | `python`・`python3`・`py` がどれも見つからない状態（Python 3.10/3.14、uv、WindowsApps、Inkscape 同梱の python を除外し、`PYTHONHOME` 等も外す）で self-check 合格（`verification\self-check-2026-10-30-nopython.json`）、GUI がタイトル付きで起動し、正常に閉じた |

## 確認していないこと

- Python を一度も入れていない別PCでの起動（今回は同じPCで PATH から外しただけ）。
- 池田の `max_lod` / `textured_max_lod` は今回は流していない（直近は 2026-10-29 に `textured_max_lod` で成功）。
- 正本の `start_gui.bat` の実起動（GUI が開くため）。写しでは EXE が無い時の案内と exit 1 を確認した。
