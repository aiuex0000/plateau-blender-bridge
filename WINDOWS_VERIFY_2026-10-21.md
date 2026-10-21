# Windows実機検証 2026-10-21（ルナ）

対象：PLATEAU-Blender-Bridge v0.10 + ローカル修正 `860f698`（原本 `0d17dfc`）
OS：Windows 11 Pro 10.0.26200
Python（ビルド環境のみ）：3.13.12 x64（uv venv `.build-venv`）、PyInstaller 6.22.3、Pillow 12.3.0、pyproj 3.8.0
Blender：`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`（5.2.2 LTS）
nusamai CLI：未導入（公式リリースにWindows用CLIバイナリが無い。後述）

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
8. 本番小範囲：未実施（nusamai CLI待ち）

## 未解決・気づき

- **nusamai CLIのWindows配布物が公式に無い**（v0.1.17〜v0.1.19とも、CLIはmacOS/Linuxのみ。WindowsはGUIインストーラーのみ）。MITライセンス、手元にcargo 1.99あり → ソースからビルドして同梱する案が有力
- `console=False` のため `--cli` はリダイレクトしないと出力が見えない。リダイレクト時の文字コードはcp932（地名がUTF-8想定のツールで化ける）
- Blender 5.2で `Material.use_nodes` がDeprecationWarning（6.0で削除予定）。今は動作に影響なし
- GUIの進捗バーが待機中にも小さく緑で表示される（見た目のみ）
