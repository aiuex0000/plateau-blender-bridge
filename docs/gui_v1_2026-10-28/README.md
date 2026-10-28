# 初心者向けGUI v1 実装と検証（2026-10-28）

対象：`b3e87bd`（初心者向けGUIの初回整理）に、リナの仕様 v1 の残りを足した版。
配布ビルドは `dist-next`。既存の `dist` は据え置き（起動中の可能性があるため触っていない）。

## 変えたこと

1. **完了時に保存先を出す** — 「完了」だけのダイアログをやめ、「作成できました。保存先: <path>」を状態行とダイアログの両方に出す。`completion_message()` を純関数にしてテスト対象にした。
2. **メイン画面の「Blenderで街を開く」を完了まで隠す** — 未生成時に押せないボタンが並ばない。結果が出た時点で主CTAとして現れる。生成開始時は再び隠す。
3. **詳細設定に「ログを保存」「ログをコピー」** — UTF-8テキストで保存（`plateau_bridge_log.txt` 既定）。コピーはクリップボードへ。仕様P1の「詳細ログの保存」。
4. **保存先はファイル名を先に表示** — メイン画面は `scene.blend` のファイル名と「上書きしない」説明だけ。フルパスは詳細設定の入力欄（`output_entry`）へ移した。
5. **125%/150% 表示倍率** — `runtime.enable_dpi_awareness()`（`SetProcessDpiAwareness(1)`、失敗時 `SetProcessDPIAware()`）を `auto_gui.main()` と `gui.main()` で有効化。Tk の `tk scaling` を実DPI（`winfo_fpixels('1i')/72`）に合わせた。
6. **マウスホイールでフォームがスクロール** — Canvas はホイールを拾わず、小画面でスクロールバーを掴む必要があった。ログ欄の上ではログ側に譲る。
7. **バージョン表記の統一** — `__version__` が 0.9.0、pyproject が 0.10.0、タイトルが v0.10 とずれていた。`__version__ = "0.10.0"` に一本化し、タイトルとフッター（出典併記）がそこを参照する。

## 検証

- 単体テスト：**112件成功**（`b3e87bd` の108件＋新規4件：完了文言、DPIヘルパー、バージョン形式、保存先のファイル名表示、完了時のCTA出現）
- 凍結EXE：`dist-next\PLATEAU-Blender-Bridge\`（PyInstaller 6.22.3、onedir、同梱nusamai v0.1.19パッチ済み）
  - `--self-check` → `passed: true`、`proj_data_found: true`、`missing_scripts: []`、`bundled_nusamai` 解決済み、Tcl 8.6.12
- 実データ（池田市 `r:135.429,34.819,135.431,34.821`、凍結EXEの `--cli`）
  - `max_lod` → `_runs\gui_v1_ikeda\ikeda.blend`、再読込検証JSON `verify_81d7a2847f53.json`：EPSG:6674、建物1・地形1、地形UV不良0、画像1枚pack
  - `textured_max_lod` → `_runs\gui_v1_ikeda_tex\ikeda_tex.blend`、`verify_b53a940970ae.json`：建物テクスチャ元1件、画像2枚pack
- 画面：`gui_easy.png`（簡単画面）、`gui_advanced.png`（詳細展開）、`exe_easy.png`（凍結EXE起動、状態「準備OK」）

## 未確認

- 125%/150% 実表示での見切れ（コード対応のみ。表示倍率を変えた実機確認は未実施）
- キーボードのみの操作、フォーカス表示
- Python未導入の別PCでの起動（このPCはvenvとBlenderがある）
- 新宿400mの再生成（池田の2通りで回帰確認まで）
- GUI上でのキャンセル・失敗からの再試行の実操作
