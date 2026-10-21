# PLATEAU → Blender Bridge 開発・検証引き継ぎ（ルナ初回着手用）

**作成：リナ / 2026-10-21**  
**基準成果物：`PLATEAU-Blender-Bridge-v0.10-source.zip`**  
**位置づけ：最初の要件から現在の実装、実機テストの入口までを統合した資料。旧 `HANDOFF_LUNA.md` の差分資料ではなく、この文書単体から開始できる。**

## 0. 最初に伝えたいこと・現在地

悠が実現したいのは、**地域または範囲・品質・保存先を選んで「生成」を押すだけで、PLATEAUの建物、実地形、航空写真が一致するBlender `.blend` を作るWindows用GUIツール**。GISやPythonの専門知識を利用者に要求しないことが重要。

- 現在の最新版 **v0.10はソースコード一式**。配布用Windows `.exe` のビルド設定はあるが、**exeそのものはまだ完成・実機検証されていない**。
- 利用者向け方針は**「Python/venv/pipなし。ZIPを展開してEXEをダブルクリック」**。内部実装にPythonを残すことは許容。開発者がビルドする際の隔離venvは問題ない。
- 現行ソースのオフラインテストは、リナ側で **Python 3.13.5 / Linuxで84件成功（2026-10-21に再確認）**。ZIPの構造・CRC検査も通過。
- **Windows版PyInstallerビルド、Windows製EXE、実際のBlender 5.2.2、nusamai CLI、PLATEAU/GSI実APIを通した最終成果物は未検証**。モックによる通過を実機成功と混同しないでほしい。
- 悠のWindowsでは以前のv0.9セットアップ時に **82件中エラー2件・スキップ1件**が出た。依存のインストール自体は成功。v0.10でコード修正したが、Windows再確認待ち。

**ルナへの最初の依頼：** v0.10のソースからWindows用EXEをビルドして起動確認し、Blender単独自己診断（外部データダウンロードなし）まで通す。実データはその後、小さな範囲だけで確認する。エラーが出れば環境・ログ・再現条件を残し、原因を切り分けて修正してよい。元ソースと元データは残すこと。

## 1. 背景・最初の要件

悠は池田市周辺のPLATEAUデータで、次の**手動ワークフローの表示成功**を確認している。

1. PLATEAU GIS ConverterでCityGMLをOBJへ変換。
2. Blender 5.2.2 LTSへ建物とDEMを取り込む。
3. QGISで国土地理院の航空写真を取得し、PNGとPGW（ワールドファイル）を作成。
4. Blender Pythonで地形のUVマッピングを行い、建物＋地形＋航空写真を表示。

**注意：** これは悠による手動操作の成功であり、今回の自動ツールの成功実績ではない。建物が一部の区域にしか出ない問題、OBJの軸順、原点と測量座標の扱いは未解決の検証事項として出発した。

### 最終ユーザー体験

- Windows GUIの起動時にBlender本体の場所を自動走査。検出できなければ参照ボタンで選択。
- 地域名・標準地域メッシュ・緯度経度の矩形などから必要な建物データを探索・取得。
- 建物LOD、航空写真解像度、DEM密度、保存先を設定。対象や容量を事前プレビュー。
- CityGML変換、地形・写真取得、CRS整合、Blenderシーン生成、保存後の再読込検査まで自動化。
- 成果物 `.blend` をそのままBlenderで開ける。進捗・ログ・中断と再開を用意。
- 元データは上書きしない。作業ファイル、キャッシュ、失敗ログ、出典情報を残す。
- CPU/オンボードGPUの事業所PCを考慮し、無理な規模や高解像度は事前に制限。
- 写真・標高・都市モデルの出典と再配布条件を尊重する。

### 特に譲れない技術要件

- WGS84の入力矩形は **西経度, 南緯度, 東経度, 北緯度**。PROJ/pyprojの変換では `always_xy=True`。
- 建物、DEM、写真を同じ**メートル単位の投影CRS**へ置く。EPSG・XY軸順を推測でごまかさない。
- OBJの `xyz` / `yxz` とBlender Z-upの向きを検証。建物・地形の相対位置と高さを維持。
- Blenderに数十万～数百万mの大座標値を直接持たせず、共通のローカル原点へ移す。元の地理座標はメタデータへ保持。
- PGWの6係数・画素中心・回転成分を正しく扱う。画像の範囲一致を検証。
- 写真の黒/透明領域、DEMの欠損、GML/MTL/画像参照切れ、途中キャッシュ破損を検出。
- 「保存できた」だけでは合格としない。Blenderで保存後に独立プロセスで**開き直し、ジオメトリ/UV/画像接続・埋め込み**を検査する。

## 2. 開発体制と判断権限

- **リナ：** 要件・構成設計、コード実装、机上でできるテスト、レビュー、追加修正。
- **ルナ：** 今回初着手。Windowsでのビルド・実行・依存解決・ログ採取・再現・修正・実データ互換性確認。
- **悠：** 要件の最終決定、操作感・完成した3Dモデルの評価。必要なときのみ質問・承認。

悠は「実機確認が必要なときだけ手を借り、開発者側で進められる作業は細かく止めない」方針。**GitHubや外部保存先へのpush/merge/公開・既存ファイルの上書きは、過去の許可を自動流用せず、その時点の許可を確認してほしい。** ローカルの作業コピーでの検証・修正は進めてよい。

## 3. 実際の設計・データフロー（v0.10）

```text
Windows GUI (Tkinter) / launcher
  ├─ Blender.exeの候補スキャン、nusamai.exeの候補スキャン
  ├─ 入力: r:west,south,east,north / m:mesh / g:地名
  ├─ PLATEAU API: CityGMLファイル検索・メッシュごとの年次選定
  ├─ GML取得・XML妥当性(構文)検査・画像imageURI参照の安全な復元
  ├─ 外部 nusamai CLI: CityGML -> OBJ (--sink obj --epsg ... -t use_lod=...)
  ├─ OBJ座標軸/地理範囲照合、必要なら指定投影XY矩形で建物面を切り出し
  ├─ 地理院: 航空写真タイルとDEMタイル -> 画像/PGW/地形OBJ
  ├─ Blender Python (bpy): 原点移動、OBJ・DEM読込、UV/材質、画像pack
  ├─ 一時 .blend の保存
  ├─ 別のBlenderプロセスで再読込し検証
  └─ 検証合格時だけ正式な .blend を発行 + provenance/ログ/キャッシュ保持
```

QGISは**手動実証時のツールであり、完成版の実行に必須ではない**。GIS Converterの通常GUIアプリと、`nusamai.exe` **CLI本体は別**。普通のGUI版のexeをCLIの代わりに渡しても動くとは限らない。最新版のCLI引数互換性とWindowsでの配布方法は要確認。

座標参照系は日本のJGD2011平面直角座標系をAUTO推定し、明示指定もできる。池田市周辺の例では `EPSG:6674` を想定。矩形は投影XYの**近似外接矩形**として建物面をクリップ（厳密な行政区域境界でも水密切断でもない）。地名/メッシュ指定は返却されたメッシュ全体が対象になり得る。

## 4. バージョン経緯（全部入りの最新版はv0.10）

| 版 | 追加・修正した主な内容 |
|---|---|
| v0.1 | 手動OBJ/DEM/PNG/PGWの合成、Blender保存、元データ非破壊、Tkinter GUI |
| v0.2 | PLATEAU API取得、nusamai接続、地理院写真/DEM自動取得、BlenderとCLIの検出 |
| v0.3 | 建物の投影矩形切り出し、EPSG AUTOと空間範囲の一致改善 |
| v0.4 | CityGML imageURIテクスチャ取得、MTL検査、地域メッシュからEPSG推定、キャッシュハッシュ化 |
| v0.5 | キャンセル、容量・処理量の事前見積、環境診断、通信・子プロセス安全対策 |
| v0.6 | 大きなOBJのディスク索引化、航空写真再投影の高速化、ログとキャッシュ復旧 |
| v0.7 | 正規化・切り出しOBJのハッシュ整合、壊れたGML/XMLの検出、画像pack失敗時停止 |
| v0.8 | `.blend`を独立プロセスで再読込検証、Blenderローカル自己診断、メッシュ単位の年度補完、出力競合の上書き防止 |
| v0.9 | UV/画像とマテリアル接続の検査強化、DEM欠損を0mで埋めない仕様 |
| **v0.10** | **開発環境を利用者に要求しないEXE配布設計**、PyInstaller onedir、Windows CI、v0.9 Windowsエラーの修正 |

## 5. 渡すファイルと主な場所

### 引き継ぎ対象の正本

`PLATEAU-Blender-Bridge-v0.10-source.zip`（53エントリ）。このZIPに全ソース・テスト・ビルド設定・従来の `README.md` / `HANDOFF_LUNA.md` / `CHANGELOG.md` が入っている。**旧v0.9のZIPではなくv0.10から始めること。**

### ファイル一覧の入口

- `plateau_launcher.py` — Windows EXEの入口。`--self-check` / `--manual` / `--cli` を受け付ける。
- `plateau_blend/auto_gui.py` — 自動取得GUI、検出・診断・中止・自己診断。
- `plateau_blend/auto_cli.py` — ログ採取・プレビュー・自動生成用CLI。
- `plateau_blend/auto.py` — 自動パイプラインの統括と中間成果物。
- `plateau_blend/catalog.py`, `appearance.py` — PLATEAU APIとGML/画像参照。
- `plateau_blend/converter.py` — nusamai実行アダプター。
- `plateau_blend/crs.py`, `clip.py`, `geo.py`, `obj.py` — 地理座標・OBJ・クリップ。
- `plateau_blend/gsi.py`, `resources.py` — 国土地理院写真・標高、処理量の見積。
- `plateau_blend/pipeline.py`, `blender_scene.py`, `blender_verify.py`, `verification.py`, `smoke.py` — Blender保存、再読込、品質検査、ローカル自己診断。
- `plateau_blend/runtime.py`, `diagnostics.py`, `discovery.py`, `artifact_cache.py` — 実行停止、診断、検出、キャッシュ。
- `build_windows.bat` — **開発者向け**Windowsビルド。専用 `.build-venv` を生成してテスト後にexeを作る。
- `dev_setup_windows.bat` — **開発者向け**ソース環境準備。通常利用者には案内しない。
- `packaging/plateau_bridge.spec`, `.github/workflows/windows-build.yml` — PyInstallerとWindows GitHub Actionsのビルド定義。
- `tests/` — 84件のPythonユニット/モック統合テスト。

**注意：** v0.10のソースZIPにEXEは含まれていない。CI workflowも設定ファイルの存在を確認したのみで、Windows上での実行成功は未確認。GitHub Actionsはコードが適切なリポジトリにpushされて初めて利用できる。

## 6. Windowsで初めてやってほしい手順（優先順）

**第一優先：Windows配布経路の成立。** 利用者向けにはvenvやテストを要求しない。ルナのビルド環境内で使うのは問題ない。

1. v0.10 ZIPを**ローカルの短い書き込み可能なパス**へ展開（例 `C:\work\plateau-bridge\`）。最初の切り分けではOneDrive同期フォルダを避けると、同期・権限・ファイルロックの影響を減らせる。
2. Windows開発環境にPython（CI設定は3.13 x64）、pip、ネットワーク接続を用意し、`build_windows.bat` を実行する。これは開発者専用の作業。`build_windows.bat` はテスト → PyInstaller → frozen self-checkの順で動く。
3. 成功したら `dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe` と同階層の同梱ファイル一式を維持して起動する。`exe`単体コピーは不可（`onedir`配布）。
4. PowerShellまたはcmdで `PLATEAU-Blender-Bridge.exe --self-check frozen-check.json` を実行し、生成されたJSONの `passed: true`、`proj_data_found: true`、`missing_scripts: []` を確認。Python/Pillow/pyproj、Tk、Blenderスクリプトの同梱診断であり、Blender本体の起動までは確認しない。
5. `.exe` をダブルクリックし、GUIが出るか、Blenderが自動検出されるか、「環境診断」が正常に動くか確認。Portable Blenderなどが検出されなければ実在する `blender.exe` を手動指定。
6. GUIの **「Blender自己診断」** を実行。ネットワーク・PLATEAU・nusamaiを必要としない合成データを使い、**本物のBlenderで `.blend` の保存・再読込を検査**する。生成された `smoke.blend`、ログと検証JSONを必ず確認する。
7. ここまで成功したら、「取得対象の確認」を小さな池田市周辺の**経緯度矩形**（サンプル：`135.429,34.819,135.431,34.821`、入力順は west,south,east,north）で実行。APIの返すメッシュ・ファイル数・年次・EPSG AUTO・予定容量を見る。**プレビューは通信を伴うがGML本体のダウンロードはしない設計。**
8. `nusamai.exe` CLIを検出・指定できたら、同じ小矩形で **`min_lod` / 写真z14～16 / DEM密度低め** の生成を試す。CLIの `--epsg` オプション、OBJ座標値、建物/地形/写真の位置・高さをチェック。`textured_max_lod` は最初の幾何検証が終わってから試す。
9. 完成`.blend`をBlenderのGUIで開き、最低でも **建物の位置・向き、地形との相対高さ、航空写真の向き・範囲、材料/画像、ファイル再オープン** を目視チェック。成功例だけでなく不一致のスクリーンショット・ログを残す。

### ビルド/診断のコマンド例

```bat
rem 開発者だけが実行
build_windows.bat

rem ビルド後（展開したEXEディレクトリから）
dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe --self-check frozen-check.json

rem ソース開発時の明示テスト（利用者は実行しない）
.build-venv\Scripts\python.exe -m unittest discover -s tests -v
```

**事前に疑うべき実機固有の点：** WindowsでのPyInstaller/Tcl/pyproj同梱、`blender_scene.py`と`blender_verify.py`の実行パス、GUIウィンドウ起動、フリーズ時のキャンセル、Blender 5.2.2 の `bpy` API、`nusamai.exe`のCLI存在と引数、PROJ座標の軸順。`console=False`でビルドしているので `--cli` の標準出力が見えにくい可能性も確認したい。

## 7. 過去のWindowsエラー（実測）

悠がv0.9の `setup_windows.bat` を実行したログ（Python 3.14・OneDriveデスクトップ上）：

- Pillow **12.3.0**、pyproj **3.8.0**、certifi **2026.7.22**、ローカルプロジェクト自体のインストールは**成功**。
- 82件のテストのうち**エラー2件、スキップ1件**。`test_appearance` が `.read_text()` の既定文字コード `cp932` でUTF-8のJSONを読んで `UnicodeDecodeError`。
- `test_pipeline` がUnix `#!` スクリプトをWindowsで実行して `WinError 193`（有効なWin32アプリケーションでない）。
- v0.10では前者のUTF-8明示と後者のWindows `.cmd` モック切り替えをソース側で修正済み。**Windowsでその修正が成功したかは未確認**。
- **この2件はテストコードの移植性の問題であり、本物のBlender生成を行って失敗したログではない。** 利用者セットアップ時に開発テストが走る設計そのものもv0.10で廃止。

## 8. 既知の制限・安全設計

- CityGML変換は外部の `nusamai.exe` CLIが必要。Windowsでの配布形態とバージョン互換性は未検証。普通のGIS Converter GUI exeを代用しない。
- PLATEAU APIと地理院タイルの実API互換性、リダイレクト先、画像相対パスは実際のデータで未確認。APIは変更され得る。
- 大きすぎる選択は安全上限で停止（コード既定例：GML24ファイル、判明サイズ合計250MiB、単独GML150MiB、生成範囲15km、写真画像1辺最大2048px、航空写真ズーム14～18）。条件や取得先によって実際に効く上限をコードで再確認すること。
- 画像はCityGML `imageURI` の一般的なラスター参照を対象。外部URL・不正パスや参照不能ファイルは安全側で止まる。画像はpack前提。
- DEMは現在**有効標高サンプル100%必須**。海岸・水域や欠損タイルが含まれると失敗し得る。欠損を勝手に0mで埋めない。
- 違う調査年度の建物をメッシュ単位で補完する設計。境界の整合や現況一致は保証しない。
- PLATEAU建物と地理院DEMで鉛直基準・測量年次が異なる可能性がある。自動ジオイド補正は未実装。
- 矩形切り出しはXY外接矩形での面クリップ。建物境界の断面を塞がず、水密メッシュは保証しない。
- 画像や測量データの利用条件・追加出典・再配布可否は**成果物の公開前に人が確認**する必要がある。
- 正式な`.blend`は元データ上書きを避け、検証後に確定する。中間ファイルや入力をむやみに削除しない。

## 9. 最小限の結果報告テンプレート（悠・リナへ返すもの）

```text
対象：PLATEAU-Blender-Bridge v0.10 / 修正した場合は差分・コミットID
OS：Windows 11/10 + build番号
Python（ビルド環境のみ）：3.x / x64
Blender：実ファイルのパス・バージョン
nusamai CLI：実ファイルのパス・バージョン（未導入なら未導入）

1. Windows build_windows.bat：成功/失敗
2. frozen-check.json：passed値、エラー項目
3. EXE直接起動：成功/失敗（何が表示されたか）
4. Blender自動検出：成功/失敗（必要なら手動指定）
5. Blender自己診断：成功/失敗（smoke.blendと再読込ログ）
6. PLATEAUプレビュー：成功/失敗（矩形・ファイル数・EPSG・年度・サイズ）
7. 本番小範囲：成功/失敗（LOD、航空ズーム、DEM密度、処理時間）
8. 目視：建物/地形/画像の位置・向き・相対高さ・見た目
9. 発生した問題：操作手順→期待値→実際→ログ/写真→推定原因
10. 変更したコード、回避策、未解決点、次に必要な判断
```

調査時の主要ログ候補：`<scene>.work/last_blender.log`、`<scene>.work/last_verify.log`、`<scene>.work/verify_*.json`、`<scene>.work/auto/converter/last_converter.log`、`<scene>.work/auto/acquisition.json`。ランチャー例外は `%LOCALAPPDATA%\PLATEAU-Blender-Bridge\logs\crash-*.log`。実際のパスは生成設定で変わることがあるので確認してほしい。

## 10. 次の開発優先順位・完了条件

**P0（今回）**：Windows用EXEがビルドできること。Python非導入の利用者環境でもEXE起動できること。PyInstaller自己検査とBlender自己診断を通すこと。ログを提出できること。

**P1**：小矩形の実API検索、`nusamai`の実データOBJ変換、Blender保存→再読込→画面確認。座標軸・EPSG・大原点・DEM/航空写真の位置合わせを確認。必要ならPython/GIS処理を修正。

**P2**：EXE配布の磨き込み（バージョン固定、わかりやすいエラー、操作の簡略化、同梱範囲・インストール/更新方針、署名なし配布時の注意）。容量・速度・タイル欠損、CityGML画像の相対パス、現実のメッシュ混在の検証。

**最終合格ライン：** 悠がWindows PCでPythonを触らずGUIを起動し、地域/小範囲/LOD・画質/保存先を選んで生成し、Blenderで開いた建物・実地形・航空写真が、位置・向き・高さの整合を持って表示できること。生成データに出典・再現情報があり、失敗時に元データを壊さず原因を追えること。

## 11. 参照先（資料内の記述を起点に再確認）

- PLATEAU GIS Converter: https://github.com/Project-PLATEAU/PLATEAU-GIS-Converter
- MIERUNE upstream CLI: https://github.com/MIERUNE/plateau-gis-converter
- CLIドキュメント: https://mierune.github.io/plateau-gis-converter/manual/use_command_line.html
- PLATEAU APIドキュメント: https://docs.plateauview.mlit.go.jp/api/rest/operations/datacatalogcitygmlconditions/
- 地理院タイル: https://maps.gsi.go.jp/development/ichiran.html
- 地理院DEMタイル: https://maps.gsi.go.jp/development/demtile.html
- 地理院の利用案内: https://maps.gsi.go.jp/help/use.html

---

**リナからルナへ：** 最初から全部を作り直す必要はない。v0.10を基準に、まず「利用者がEXEを起動できるか」「本物のBlenderで自己診断が通るか」の2点を切り分けてほしい。失敗した場合は曖昧な「動かない」ではなく、段階とログを特定して修正する。こちらがオフラインで通したテスト結果は尊重しても、Windows実環境の観測を上位として扱って。完成宣言より、再現性のある動作記録を優先して進めよう。
