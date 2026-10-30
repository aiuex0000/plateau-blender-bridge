# 引き継ぎ：GUIの見た目の作り直し（2026-10-30）

次のセッションは、PLATEAU → Blender Bridge の GUI の見た目をモダンに作り直す。まずこの文書を読み、次に workspace の `AGENTS.md` に従うこと。

## いまの状態

- 機能はひととおり揃い、配布版で通っている。エンジン、簡単画面、詳細設定、中止、ログの保存とコピー、CLIの結果JSON、Blender未検出時の公式リンクがある。
- 正本：`C:\Users\You\Workspace\PLATEAU-Blender-Bridge`（git master、remoteなし、HEAD `8e8085e`）。repo の写しは `projects/plateau-blender-bridge`。
- テスト：正本で126件OK。写しは126件OK・skip 1（`nusamai.exe` を置かないため）。
- 配布先：`C:\Users\You\Workspace\Workbench\Projects\PLATEAU-Blender-Bridge\app`。中身は `c8e4abd` のビルドで、リンクボタン（`8e8085e`）はまだ入っていない。
- 今の画面：`plateau_blend/auto_gui.py`（Tk / ttk の vista テーマ）。手動OBJモードは `gui.py`。

## マスターの方針（決定済み）

- 想定する利用者は「普段PCを使わない人」。今の ttk の見た目は昔のWindowsソフト風で、初心者がためらいうる。マテリアルデザインのようなモダンな見た目にする。
- 作り直しは **pywebview＋HTML** を推奨する（Windowsでは WebView2 で描画）。Tauri は将来の乗り換え先として残す。Electron は使わない。
- Python で書くこと自体は問題にしない。配布版は Python 同梱の凍結EXE。
- 署名はしない。初回起動の警告（SmartScreen）は、画像付きの案内で乗り切る。

## 進め方の提案（未決。マスターと決める）

1. 画面の設計：リナの仕様と操作画面のプロトタイプ（`docs\gui_ux_v1_source\`）が、そのまま下敷きになる。画面に置く要素は今の簡単画面と同じでよい。
2. 骨組み：`auto_gui.py` の処理（生成の worker・キュー・中止・進行4段階・完了時の開くボタン）を画面から切り離し、pywebview の JS API から呼べる形にする。エンジン（`auto.py` など）には触らない。
3. 今の Tk 画面は、新しい画面が揃うまで残す（`--legacy` などで起動できるようにする）。
4. PyInstaller の spec に pywebview と HTML 資産を足す。凍結EXEで WebView2 が無い環境の扱いを決める（Windows 11 には標準で入っている）。

決めること：見た目の方向（配色・角丸・フォント）、HTML を手書きにするかライブラリを使うか、詳細設定とログ欄をどこまで新しい画面に移すか。

## 落とし穴

- 動いている GUI を開いたまま `dist` へビルドしない。検証用のビルドは `--distpath dist-next --workpath build-next` に出し、自己検査に合格してから昇格する。
- 配布EXEは `console=False`。CLI の結果は `<出力>.result.json` で確かめる。
- repo 内に置いた EXE は低い権限で動き、`Documents` へ書けない。配布物は repo の外に置く。
- worker（OpenCode）は、OpenCode の設定がある Workbench repo の中でしか動かない。写し `projects/plateau-blender-bridge` を指して作業させ、検収してから正本へコピーする。
  - ReadPath と EditPath は、末尾の `/` を付けず、ファイルを1本ずつ指定する。
  - worker はテストを実行する権限で止まるので、テストは main が流す。
  - worker が書いた `.bat` は改行が LF になることがある。CRLF に直す。
- 既存の未コミット差分（`luna/memory/` など）は巻き込まない。

## 検証

```powershell
$env:PYTHONUTF8='1'
& C:\apps\PLATEAU-Blender-Bridge\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

ビルド・自己検査・CLIの手順は `docs\handoff_2026-10-29_next-runtime.md` の 5 節を参照。記録は `docs\gui_p1_check_2026-10-29.md` と `docs\p2_regression_2026-10-30.md` にある。

## 見た目の作業と並行して残っているもの

- AI向け説明書と、配布物へのソース同梱。nusamai・PLATEAUデータの利用条件の確認。
- 「AIとのソフト制作」資料の目次案。
- 別PCでの起動確認、池田の再実行、125%/150% 表示（優先度は低い）。
