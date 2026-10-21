# Windows実機検証 2026-10-21（ルナ）

対象：PLATEAU-Blender-Bridge v0.10 + ローカル修正 `860f698`（原本 `0d17dfc`）
OS：Windows 11 Pro 10.0.26200
Python（ビルド環境のみ）：3.13.12 x64（uv venv `.build-venv`）、PyInstaller 6.22.3、Pillow 12.3.0、pyproj 3.8.0
Blender：`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`（5.2.2 LTS）
nusamai CLI：v0.1.19 をソースからビルドして同梱（下記）

1. ビルド（build_windows.batと同じ手順を手動実行）：成功。`dist\PLATEAU-Blender-Bridge\` 69.5MB（onedir）
   - 単体テスト：`PYTHONUTF8=0` で 85件成功（v0.10の84件＋圧縮.blend回帰1件）。v0.9のWindows失敗2件は解消済み
   - PyInstaller警告 `Datas for pyproj not found at:` は出るが、proj.dbは同梱されている（下記）
2. frozen-check.json：`passed: true`、`proj_data_found: true`、`missing_scripts: []`、Tcl 8.6.12
3. EXE直接起動：成功。自動生成GUI（v0.10）が表示される
4. Blender自動検出：成功（Program Files配下の5.2）
5. Blender自己診断（`--cli --smoke-test`）：**初回失敗→修正後成功**（約5秒）
   - 失敗：`出力の先頭がBLENDERではありません`。`blender_scene.py` が `compress=True` で保存し、実ファイル先頭はzstdマジック `28 B5 2F FD`。`pipeline.py` は生の `BLENDER` だけを許していた。モックは生ヘッダを書くためテストでは見逃されていた
   - 修正：`BLEND_MAGICS`（raw / zstd / gzip）を許可。中身の妥当性は従来どおり別プロセス再読込検証が担保
   - 再読込検証：建物1・地形1・UV・画像2枚pack・CRS EPSG:6674・原点(500025,130025)へのローカル化を確認
   - PATHからPythonを除いた環境変数でも同じく成功
6. 環境診断（`--cli --diagnose`）：凍結EXEでは dist-info が無く Pillow/pyproj が `null` → モジュールの `__version__` へフォールバックする修正で `12.3.0` / `3.8.0` 表示
7. PLATEAUプレビュー（`r:135.429,34.819,135.431,34.821`）：成功
   - 池田市 2025年度、メッシュ52351384、bldg GML 1件（87,297,191 bytes、max_lod 2）
   - 写真z16 1タイル（94×114px）、DEM 1タイル位置、地形8,484頂点
8. 本番小範囲：**成功**（EXE `--cli`、`r:135.429,34.819,135.431,34.821`、max_lod / 写真z16 / DEM100、約30秒）
   - 出力：`_runs\ikeda\ikeda.blend` ＋ `ikeda.provenance.json`。再読込検証合格
   - 切り出し：206,415面 → 5,511面、約184×223m（EPSG:6674）
   - 高さ：建物頂点z − 地形z は min −1.86m / p10 −0.61m / 中央値 6.49m / max 26.27m。建物は地面にほぼ接地（PLATEAUとGSIの鉛直基準差の範囲）
   - 位置：切り出しOBJ（投影座標）の水平面を、PGWつき航空写真へ直接描画して一致を確認（約1画素≒2m以内）。`docs/verify_2026-10-21/ikeda_overlay.png`
   - 描画：`ikeda_oblique.png` / `ikeda_top.png`（地形の縞模様はEEVEEの影アクネで、検証用ライトの設定によるもの）

## nusamai CLI（同梱）

- 公式にWindows用CLIが無いため、`MIERUNE/plateau-gis-converter` v0.1.19（`a038cff`）を cargo 1.99 でビルド（6分30秒、37.4MB）。`build_nusamai.bat` がタグ固定でclone→build→`tools\nusamai\` に配置。MITライセンス文を `LICENSE-nusamai.txt` として同梱
- 配布物では `dist\PLATEAU-Blender-Bridge\tools\nusamai\nusamai.exe`。`find_nusamai` は環境変数の次にこれを優先。frozen self-checkは同梱nusamaiを必須にした
- 実測したv0.1.19のOBJ仕様（ソースと実データの両方で確認）
  - `center_at_origin` が**既定true**（重心を原点へ移動）→ `-o center_at_origin=false` を必須で付与
  - 出力は常に**Y-up `[東, 高さ, −北]`** → 変換直後に `(a,b,c)→(a,−c,b)` へ回転（`converter.yup_to_zup`、回転なので面の向き・UVは不変）。池田市で東 −52,642〜−51,436 / 北 −131,143〜−130,165 を確認
  - `min_lod` はLOD0の足元平面で高さが全部0 → 既定LODを `max_lod` に変更（GUI・CLI・AutoRequest）
  - `textured_max_lod` は外観画像フォルダが無いと `NotFound` でpanic（GML単体での試験時）。Bridge経由（画像を揃えた状態）での確認は未実施
- 変換キャッシュ署名の版を3→4へ（旧版の重心移動済みOBJを再利用しない）

## 未解決・気づき

- **nusamai CLIのWindows配布物が公式に無い**（v0.1.17〜v0.1.19とも、CLIはmacOS/Linuxのみ。WindowsはGUIインストーラーのみ）。MITライセンス、手元にcargo 1.99あり → ソースからビルドして同梱する案が有力
- `console=False` のため `--cli` はリダイレクトしないと出力が見えない。リダイレクト時の文字コードはcp932（地名がUTF-8想定のツールで化ける）
- Blender 5.2で `Material.use_nodes` がDeprecationWarning（6.0で削除予定）。今は動作に影響なし
- GUIの進捗バーが待機中にも小さく緑で表示される（見た目のみ）

## テクスチャ付きLOD（textured_max_lod）

9. 同じ矩形で `--lod textured_max_lod`：**修正2件の後に成功**（約18秒、`_runs\ikeda_tex2\ikeda_tex.blend`、再読込検証合格）
   - 1回目：`画像が96件を超えました` で停止。メッシュGML（約1km四方）全体の外観画像746枚を集めようとしていた
     - 対応：`plateau_blend/subset.py` を追加。矩形指定（切り出しあり）の時、変換前にGMLを矩形＋約50m周辺の建物（cityObjectMember単位）へ絞り、全体appearanceも残った面を指すtargetだけ残す。テキストのまま要素単位で残す/捨てるので名前空間や数値は原文どおり。元GMLは読み取りのみ
     - 池田市：建物3,937→372棟、画像746→45枚、2秒。nusamai変換も2.6秒→0.3秒
   - 2回目：生成は成功したが、テクスチャの無い建物が描画に出ない。分割出力で数えると372棟中327棟が**面ゼロ**
     - 原因：nusamai v0.1.19の `TexturedHighest` のバグ（探索中に元の木を削る）。詳細と差分は `patches/README.md`
     - 対応：同梱nusamaiにパッチを当てて再ビルド（`build_nusamai.bat` がclone直後に `git apply`）。修正後は空0棟、切り出し後5,511面で `max_lod` と一致
   - 最終：埋め込み画像2枚（航空写真＋nusamaiのテクスチャアトラス1枚）、建物マテリアル2（アトラス付き1・無地1）。LOD2の外観付きビル群と、LOD1の無地の建物が同じ範囲にそろう（`docs/verify_2026-10-21/ikeda_textured_oblique.png`、修正前は `ikeda_textured_before_patch.png`）
   - テスト：90件成功（絞り込み3件、Y-up回転・引数2件を追加）

## 西新宿（東京都）

10. `r:139.690,35.686,139.694,35.690`、`textured_max_lod`：成功（EXE、約62秒、`_runs\shinjuku2\shinjuku.blend`、EPSG:6677自動）
   - 最初の矩形 `139.690,35.688,139.694,35.692` は3次メッシュ境界をまたぎ、4 GML / 414.7MiB で上限停止。1メッシュ（53394525）に収まるよう南へずらした
   - 画像枚数の上限96で停止（範囲内131棟でテクスチャ393グループ）→ 既定を1000枚へ。容量上限120MiB/GMLは維持
   - 同じmesh 53394525 が新宿区版と渋谷区版で返り、中身はSHA-256一致。ダウンロード後に内容ハッシュで2件目以降を除外（`[DEDUP]`）。なお除外前もnusamaiの出力面数は同じ（36,106）で、建物の二重化は起きていなかった。効果は取得・変換の省略
   - 最高点295m。高層ビル群に外観テクスチャ（`docs/verify_2026-10-21/shinjuku_textured_oblique.png`）
   - テスト91件成功

## 地名検索の置き換えと実機フィードバック（「テクスチャだいぶ滅茶苦茶」）

11. 地名（`g:`）はPLATEAU APIが市区町村名しか受けず `新宿駅` で404。GUIの地名モードを「中心の 緯度, 経度（, 一辺m）」へ置換（Googleマップの右クリックでコピーした値をそのまま貼れる。一辺省略は400m）。LOD既定は `textured_max_lod`
   - 新宿駅中心400m：4メッシュ・約398MiBで上限超え → 区境メッシュの双子（同mesh・年・サイズ）をダウンロード前に1件へ畳み、上限を合計600MiB・1件300MiBへ。成功（約257秒）
12. 実機の見た目崩れを切り分け
   - ビル壁の別画像の貼り付き：nusamai生出力とBridge完成品を同カメラで描画して同一。atlasの該当領域を切り出すと、元テクスチャ自体が斜め航空写真で手前の都庁屋上が写り込んだもの（`docs/verify_2026-10-21/shinjuku_source_texture_occlusion.png`）。**PLATEAU元データ由来で変換側の不具合ではない**
   - 地面の縞・ギザギザ・ぼやけ：**Bridgeのバグ**。`make_terrain` の三角形が1列ずれ（(i+1,j+1)の代わりに(i-1,j+1)）、各行の左端から前行の右端へ伸びる裏返りの細長い面が100枚、右上隅の頂点が未使用。修正して壊れた面0
     - あわせてDEMを最近傍→双線形、航空写真の既定ズーム16（約1.9m/px）→18（約0.5m/px）、地形をスムーズシェーディング
     - z18で地形の小数6桁丸め（1µm）が被覆判定の許容1e-6pxを超え88.9%で停止したため、許容を1e-3pxへ
     - 前後比較：`shinjuku_ground_before.png` / `shinjuku_ground_after.png`
   - テスト99件成功（三角形分割・双線形・被覆の回帰テストを追加。旧コードでは失敗することを確認）。`dist-next` 再ビルド、self-check passed、凍結EXEのCLIで新宿生成・壊れた面0

## 運用の注意（反省）

- マスターがGUIを開いたまま `dist` へ `--clean` で再ビルドし、使用中でないファイル（同梱nusamai）だけが消えた。すぐ `tools\nusamai` を戻して復旧。以降、検証用ビルドは `--distpath dist-next` に分け、GUI終了を確認してから入れ替える。旧版は `dist-old` に残してある
