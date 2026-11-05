# 新画面を pywebview から「ローカルHTTP＋Edgeアプリ窓」へ載せ替え（2026-11-05）

## なぜ

松井さんの PC で、配布版を開くと旧版（Tk）の画面が出た。手元で再現した結果、原因は次のとおり。

- ネットから落とした ZIP をエクスプローラーで展開すると、全ファイルに Mark of the Web（`Zone.Identifier`、ZoneId=3）が付く。
- pywebview は pythonnet（.NET）経由で WebView2 の DLL を読む。印が付いていると .NET の DLL 読込が止まり、`web_gui.main()` が False を返す。
- 起動口はどんな失敗でも理由を残さず Tk へ切り替えていた。

| 条件（配布版 `app` の写し） | 子プロセス | 画面 |
|---|---|---|
| 全1,112ファイルに印 | なし | Tk（旧画面） |
| 同じ写しで印だけ外す | `msedge.exe`（WebView2） | 新画面 |

## 形

- `plateau_blend/web_server.py`：標準ライブラリの `ThreadingHTTPServer` を `127.0.0.1:0` で立てる。起動ごとの token（`X-Bridge-Token`）と `Host` を検査し、`POST /api/<名前>` で `BridgeApi` のクラスに定義された公開メソッドだけを呼ぶ。静的配信は `web` 直下のファイルだけ。
- `web_gui.main()`：`msedge.exe --app=<URL> --user-data-dir=%LOCALAPPDATA%\PLATEAU-Blender-Bridge\edge-profile` で開く。Edge が無ければ既定のブラウザ。
- `app.js`：`state.api` を `fetch('/api/<名前>')` の Proxy に替えただけで、画面の動きは同じ。
- ファイル選択：Tk の `filedialog` をメインスレッドで出す（HTTP のスレッドは依頼をキューへ入れて待つ）。
- 同梱から pywebview・pythonnet が抜けた。.NET も WebView2 の DLL も読まないので、印の有無に左右されない。
- 旧画面へ切り替える時は `%LOCALAPPDATA%\PLATEAU-Blender-Bridge\logs\gui-fallback-*.log` に理由と traceback を書く。

## 終わり方（実測で決めた）

- 起動した `msedge.exe` は、**初回でも**本体を起動し直してすぐ終わる。プロセスの終了は、閉じた合図に使えない。
- 主な合図は、画面の `pagehide` から `keepalive` の fetch で送る `/api/_bye`。届いた後3秒連絡が戻らなければ終了する。戻れば再読込とみなす。
- 保険として、心拍（150ms ごとの `poll`）が180秒途切れたら終了する。最小化中の Edge はタイマーを1分1回まで間引くので、これより短くしない。
- 2回目の起動では、同じプロファイルの Edge へ引き渡されて新しい窓が開く（旧画面には落ちない）。

## 確かめたこと

- テスト 184件成功（前回159件。サーバー・ファイル選択・終了判定・さよならを追加）。
- ソースから：窓が開く／2つ目の起動も新画面／WM_CLOSE で窓2つを閉じて4秒で両方終了、専用プロファイルの Edge 0個。
- 凍結版 `dist-next`：self-check 合格（`web_files_found: true`）。全1,088ファイルに印を付けた写しを `UseShellExecute=false` で起動 → 新画面の窓（題名は `initial_state()` 由来）、fallback ログ0件 → WM_CLOSE で4秒で終了、Edge 0個。
- 印つきの exe をエクスプローラー経由で開くと「開いているファイル - セキュリティの警告」が出る。これは Windows の確認で、利用者が「実行」を押せば進む（DLL 読込の問題とは別）。

## まだのこと

- 新しい形の画面で、ボタンを実際に押す確認（保存先の「変更」＝Tk のファイル選択、作成→完了、ログ保存、詳細設定）。
- `dist` への昇格、ZIP と配布先 `app` の入れ替え。
- 松井さんの PC での確認。
- `docs/gui_v2_spec.md` の pywebview の節は当時の記録として残し、冒頭に注記を足した。
