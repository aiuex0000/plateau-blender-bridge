# 引き継ぎ：PLATEAU → Blender Bridge（2026-10-29 / ルナ → Codex または Claude）

この文書は、次の器官がそのまま作業を続けられるように書いた引き継ぎプロンプトです。
まずこの文書を最後まで読み、次に `AGENTS.md`（workspace の正本）へ従ってください。

## 0. 最初に結論

- 対象は Windows 用 GUI ツール **PLATEAU → Blender Bridge**。場所と広さを選ぶと、PLATEAU の建物・国土地理院の地形と航空写真が一致した `.blend` を作る。
- **エンジンは完成していて、実データで通っている**。いまの作業は「初心者向けの入口（GUI）と配布」の仕上げ。
- 2026-10-28 に GUI の残り（完了表示・ログ保存・DPI・黒い画面の抑止）を実装し、検証済みビルドを `dist` へ昇格した。**次は配布物の置き場所を決めて、そこから起動できる形にする。**
- マスターの決定：**配布用アプリは `C:\Users\You\Workspace\Workbench` 配下に Project 用フォルダを作ってそこへ置く。`C:\apps` は常用アプリの置き場なので使わない。** フォルダ名・構成は次の担当が決めてよい（マスター了承済み）。

## 1. どこに何があるか

| 役割 | パス | 注意 |
| --- | --- | --- |
| **開発の正本** | `C:\Users\You\Workspace\PLATEAU-Blender-Bridge` | git master、remote なし。HEAD `69a59df` |
| repo への写し | `projects/plateau-blender-bridge`（Workbench repo 内） | バックアップ用。`main` へ push 済み |
| 開発用実行環境 | `C:\apps\PLATEAU-Blender-Bridge`（`.venv` = Python 3.13.12 / pyproj 3.8.0 / Pillow 12.3.0、`tools\nusamai\nusamai.exe`） | **配布物の置き場ではない**。venv と nusamai の開発用 |
| ビルド用 venv | `<正本>\.build-venv`（PyInstaller 6.22.3） | `build_windows.bat` が使う |
| 配布ビルド | `<正本>\dist\PLATEAU-Blender-Bridge\`（105.2MB）+ `dist\PLATEAU-Blender-Bridge.zip`（37.3MB） | 2026-10-28 ビルド。self-check passed |
| 旧ビルド | `<正本>\dist-old\PLATEAU-Blender-Bridge-2026-10-21\` と同 ZIP | 戻す時用 |
| Blender | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`（5.2.2 LTS） | 自動検出される |

`start_gui.bat` は **`C:\apps` の venv を最優先**で起動する（＝ソース実行）。そのため `dist` の EXE を直接ダブルクリックしない限り、EXE 側の変更は見えない。

## 2. 済んでいること（触る前に知っておく）

### 2.1 エンジン（2026-10-21 に実機検証済み）

- PLATEAU API のカタログ取得、メッシュ/年度選択、区境の同一 GML のハッシュ重複排除
- nusamai v0.1.19 をソースからビルドして同梱。`textured_max_lod` の空メッシュ不具合は `patches\nusamai-v0.1.19-textured-lod-fallback.patch` で修正済み（`build_nusamai.bat` が clone 直後に適用）
- OBJ は Y-up `[東, 高さ, −北]` → `(a,b,c)→(a,−c,b)` で Z-up へ。`center_at_origin=false` 必須
- 地形は DEM 双線形＋航空写真 z18（約0.5m/px）、三角形分割のずれ修正済み
- 矩形指定時は GML を矩形＋約50mへ絞ってから変換（画像枚数・時間の抑制）
- 生成後に**別プロセスで `.blend` を開き直して検証**。失敗時は正式ファイルを発行しない
- 検証記録：`WINDOWS_VERIFY_2026-10-21.md`、画像は `docs\verify_2026-10-21\`

### 2.2 GUI（2026-10-27〜28）

- 簡単画面（初期表示）：タイトル「好きな街を3Dにしよう」、Googleマップ導線、座標貼り付け、有名な場所プリセット10件、中心点/矩形/メッシュ、200/400/800m、保存先はファイル名だけ表示
- 詳細設定（折りたたみ）：EPSG、LOD（既定 `textured_max_lod`）、ズーム18、DEM 100、OBJ軸、矩形クリップ、Blender/nusamai パス、保存先フルパス、取得対象の確認、環境診断、Blender自己診断、手動OBJモード、ログ欄＋**ログ保存/コピー**
- 進行は4段階（データを探す→建物→地形と写真→Blenderで保存・確認）。％は捏造しない
- 完了時「作成できました。保存先: <path>」を状態行とダイアログに表示し、**「Blenderで街を開く」を主CTAとして出現**（生成前は非表示）
- 上書き禁止：既存ファイルがあれば `scene_2.blend` のように別名
- **黒い画面を出さない**：`runtime.hidden_process_kwargs()`（`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`）を `run_logged` / `taskkill` / `--version` 確認 / Blender起動 / 手動モード起動に適用。進捗はGUI側で見せる
- DPI：`runtime.enable_dpi_awareness()`＋`tk scaling`（**実表示 125%/150% は未確認**）
- `__version__ = "0.10.0"` に統一（タイトル・フッターが参照）

### 2.3 検証とビルドの現状

- 単体テスト：正本で **115件成功**。repo写しは **115件・skip 1**（`nusamai.exe` を置かないため自己検査の契約テストだけ skip。CI と配布ビルドでは実行される）
- `dist` の EXE：`--self-check` passed、`--cli` 池田 `r:135.429,34.819,135.431,34.821` / `max_lod` で exit 0、独立再読込検証 JSON 合格
- 記録：`docs\gui_v1_2026-10-28\README.md`、スクショ3枚、`frozen-check-dist.json`
- リナの仕様原本：`docs\gui_ux_v1_source\`（仕様 v1、操作画面プロトタイプ HTML、実装引き継ぎ、14件の入力テスト）

## 3. 次にやること（優先順）

### P0. 配布物の置き場所を決めて、そこから起動する

1. `C:\Users\You\Workspace\Workbench` 配下に配布用フォルダを決める（例：`Workbench\Projects\PLATEAU-Blender-Bridge\`）。**repo の中に置かない**理由は下の 4.1。
2. `dist\PLATEAU-Blender-Bridge\` 一式をそこへコピーし、ZIP も置く。
3. 入力（マスター）用に、その EXE へのショートカットか `start.cmd` を用意する。既定の保存先（`Documents\PLATEAU`）へ実際に書けることを**必ず実機で確認**する。
4. `start_gui.bat` の入口を整理する（開発用＝venv、利用者用＝配布EXE）。混ぜない。
5. 旧 `C:\apps\PLATEAU-Blender-Bridge` は開発専用と分かるようにする（削除はしない。venv と nusamai が入っている）。

### P1. GUI の残りの確認

- 125% / 150% 表示倍率で、簡単画面の主ボタンと詳細設定に到達できるか
- キーボードのみ（Tab順・フォーカス表示）で生成まで辿れるか
- 生成中のキャンセル、失敗からの再試行、ログの保存とコピーを実操作で
- 小さい画面（1366×768 相当）で見切れないか

### P2. 回帰

- 新宿 `r:139.690,35.686,139.694,35.690` / `textured_max_lod` の再生成（10/21 は成功。所要約62秒〜）
- 池田の `max_lod` と `textured_max_lod`（直近は 2026-10-28 に成功）
- Python を入れていない環境、または PATH から Python を外した環境で EXE 起動

## 4. 落とし穴（ここで一度踏んでいる）

1. **repo 内の EXE は Low 整合性で動き、`Documents` へ書けない。** だから開発環境を `C:\apps` へ出した経緯がある。配布物も repo 内に置くと既定の保存先で失敗しうる。
2. **動いている GUI を開いたまま `dist` へ `--clean` ビルドしない。** 2026-10-21 に使用中でないファイル（同梱 nusamai）だけ消えた。検証ビルドは `--distpath dist-next --workpath build-next` に分け、GUI 終了を確認してから昇格する。
3. **旧 v0.10 のソースで現行を上書きしない。** リナの仕様書にも明記がある。正本は `C:\Users\You\Workspace\PLATEAU-Blender-Bridge`。
4. `build_nusamai.bat` は `tools\nusamai\nusamai.exe` が既にあると即終了する。パッチを当て直す時は明示的に消すか、タグ固定で作り直す。
5. `console=False` のため `--cli` の標準出力は見えない。確認は `Start-Process ... -RedirectStandardOutput` と出力ファイル・`verify_*.json` で行う。
6. PowerShell では `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;`、Python 実行は `$env:PYTHONUTF8='1'; python ...`。
7. repo 写しでは `nusamai.exe` を置かない方針（35MB のバイナリを git に入れない）。そのため `test_packaging_v010` は skip する。**skip を「通った」と報告しない。**
8. 既存の未コミット差分（`luna/memory/` など）はマスターのもの。巻き込まない。

## 5. 検証コマンド

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
$repo = 'C:\Users\You\Workspace\PLATEAU-Blender-Bridge'
$py   = 'C:\apps\PLATEAU-Blender-Bridge\.venv\Scripts\python.exe'

# 単体テスト（115件）
cd $repo; & $py -m unittest discover -s tests -q

# ビルド（動いているGUIがないことを確認してから）
& $repo\.build-venv\Scripts\python.exe -m PyInstaller --noconfirm --distpath dist-next --workpath build-next packaging\plateau_bridge.spec

# 凍結EXEの自己検査
$exe = "$repo\dist\PLATEAU-Blender-Bridge\PLATEAU-Blender-Bridge.exe"
$json = "$env:TEMP\frozen-check.json"
$p = Start-Process $exe -ArgumentList @('--self-check', ('"'+$json+'"')) -PassThru -Wait -WindowStyle Hidden
"exit=$($p.ExitCode)"; Get-Content $json

# 実データ小範囲（出力と検証JSONを確認）
$out = "$repo\_runs\handoff\check.blend"
$p = Start-Process $exe -ArgumentList @('--cli','--condition','r:135.429,34.819,135.431,34.821','--output',('"'+$out+'"'),'--lod','max_lod') -PassThru -Wait -WindowStyle Hidden -RedirectStandardOutput "$repo\tmp.out.log" -RedirectStandardError "$repo\tmp.err.log"
"exit=$($p.ExitCode)"; Get-ChildItem "$repo\_runs\handoff\check.work\verify_*.json"
```

## 6. 判断してよいこと / 聞くこと

- 判断してよい：配布フォルダ名と構成、ショートカットの作り方、GUI の文言・余白、テストの追加、ドキュメント整備。
- マスターに聞く：`C:\apps` の整理・削除、repo 外の新しい場所への書込み方針、配布（公開・共有）、既存 `dist-old` の削除、Blender のバージョン変更、`Documents` 以外を既定保存先にするか。

## 7. 参照

- 要件と画面仕様：`docs\gui_ux_v1_source\PLATEAU_Bridge_GUI_UX_v1_初期仕様.md`
- 操作画面プロトタイプ（本体未接続）：`docs\gui_ux_v1_source\PLATEAU_Bridge_操作画面プロトタイプ.html`
- 前回の実装引き継ぎ：`docs\gui_ux_v1_source\ルナ向け_実装引き継ぎ.md`
- 2026-10-21 の実機検証：`WINDOWS_VERIFY_2026-10-21.md`、`docs\verify_2026-10-21\`
- GUI v1 の変更点と証拠：`docs\gui_v1_2026-10-28\README.md`
- 開発全体の引き継ぎ：`docs_handoff_2026-10-21.md`
