# GUI v2 仕様：一問ずつのウィザード（B案）

> 2026-11-05：描画を pywebview から「ローカルHTTP＋Edgeアプリ窓」へ載せ替えた（ZIP の Mark of the Web で .NET の DLL 読込が止まり、Tk へ落ちたため）。下の pywebview に関する記述は当時の記録。今の形は `docs/gui_edge_app_2026-11-05.md`。

2026-10-30。見た目の土台は `docs/gui_v2_directions/directions.html` の B案（開発者決定）。画面の文言と置く要素は今の簡単画面（`plateau_blend/auto_gui.py`）と同じにし、エンジン（`auto.py` など）には触らない。

## 決めたこと

- 描画は pywebview（Windows では WebView2）。HTML/CSS/JS は手書きにし、ビルドも外部CDNも使わない（オフラインで動くこと）。
- 既定の起動は新しい画面。`--legacy` で今の Tk 画面（`auto_gui.py`）を起動する。`--manual` と `--cli` は今のまま。
- pywebview が読み込めない時、または WebView2 が無くて窓を作れない時は、エラーを出さずに Tk 画面で起動する。
- `auto_gui.py` は消さず、中身も変えない。`PLACE_PRESETS`・`STAGES`・`stage_for`・`free_output_path`・`user_error`・`completion_message`・`CANCELLED_MESSAGE`・`BLENDER_DOWNLOAD_URL` はそこから import して使う。

## ファイル構成

| ファイル | 役割 |
| --- | --- |
| `plateau_blend/web_gui.py` | `BridgeApi`（画面から呼ばれる Python 側）と `main()`。Tk を import しない |
| `plateau_blend/web/index.html` | 画面の骨組み |
| `plateau_blend/web/app.css` | 見た目。B案の色（`--p:#D9532B` など）、フォントは `"BIZ UDPGothic","Yu Gothic UI",sans-serif` |
| `plateau_blend/web/app.js` | 画面の切り替えと `window.pywebview.api` の呼び出し |
| `plateau_launcher.py` | 起動の分岐に `--legacy` と、Tk への切り替えを足す |
| `packaging/plateau_bridge.spec` | `plateau_blend.web_gui` と `webview` を hiddenimports に、`plateau_blend/web` を datas に足す |
| `pyproject.toml` | 依存に `pywebview` を足す |
| `tests/test_web_gui.py` | `BridgeApi` のテスト（窓は作らない） |

## 画面の流れ

窓の既定サイズは今の `initial_geometry` と同じ考え方（既定 900x780、小さい画面では縮める）。最小 730x580。

### 入力：3つの質問

上に手順バー（1 場所 → 2 広さ → 3 保存先）。押すと戻れる。下に「← 戻る」「詳細設定」「次へ →」。3問目では「次へ」が「3Dの街を作る」になる。

**1. どこの街を作りますか？**
- 有名な場所のカード（`PLACE_PRESETS` の10件。名前を大きく）。押すと選ばれ、広さもその場所の既定値になる。
- 「または」の下に「Googleマップで好きな場所を探す」の横長カード。押すと同じ画面の中に座標の入力が開く：
  - 「Googleマップを開く」ボタン（既定のブラウザで開く）と、手順の一行（地図を右クリック → 数字をクリックでコピー → 下に貼り付け）
  - 指定のしかた：中心点 / 矩形 / メッシュコード（今と同じ3つ。見出しと例の文言も今と同じ）
  - 入力欄と「この場所を確認」ボタン。結果は欄の下に、成功は緑のチェック、失敗は赤のバツと文言で出す（色だけで区別しない）
- 有名な場所を選んだ時も「この場所を確認」は使える。
- 場所が空のまま「次へ」を押したら、先へ進まず欄の下に「場所を選ぶか、座標を入れてください」と出す。

**2. どのくらいの広さにしますか？**
- 中心点の時だけ聞く。3枚のカード（小さめ 200m・すぐできる / 標準 400m・おすすめ / 広め 800m・時間がかかる）。
- 中心点で入力欄に一辺が書いてある時（`35.68, 139.76, 300` など）は、4枚目の「入力した広さ（300m）」を出して選んだ状態にする。
- 矩形・メッシュコードの時は、この手順を「広さは入力した範囲で決まります」の一文にして、そのまま次へ進める。

**3. ここに保存します**
- ファイル名を大きく、フォルダを小さく。「変更」でファイル保存の窓を開く（pywebview の `SAVE_DIALOG`、`.blend`）。
- その下に、作る内容のまとめ（場所・広さ・保存先）。今の確認ダイアログ（`askyesno`）の代わりにこの画面を確認とし、「3Dの街を作る」を押したら確認なしで始める。
- 同じ名前のファイルがある時は、まとめの下に「同じ名前のファイルがあるので、○○_2.blend で保存します」と前もって出す（`free_output_path`）。

### 作成中

大きな円（4分の何段目か）と、段階の文言（`STAGES`）。その下に「東京タワー・400m を作っています。数分かかることがあります。」。「中止する」ボタンと「くわしく見る」（ログを開く）。

### 完了

「街ができました！」と保存したファイル名。大きな「Blenderで街を開く」。その下に「保存したフォルダを開く」「別の街を作る」（入力の1問目へ戻る。入力内容は残す）。

### 中止・失敗

- 中止：`CANCELLED_MESSAGE` を出し、「もう一度作る」（3問目へ戻る）と「くわしく見る」。
- 失敗：`user_error` の文言を出す。「この場所には」で始まる時はその文をそのまま、それ以外は「作れませんでした」と文言を添える。「もう一度作る」「くわしく見る」「ログを保存」。

### Blender が見つからない時

起動時の検索で Blender が無ければ、1問目と3問目の上に帯を出す：「Blenderが見つかりません」＋「Blenderを入手する（公式サイト）」ボタン＋「入れたら再スキャン」ボタン。見つかったら帯を消す。

### 詳細設定（別画面）

下の「詳細設定」で開き、「← 戻る」で閉じる。中身は今の詳細設定と同じ：保存先（フルパス）、Blender・nusamai の場所と再スキャン、EPSG・建物LOD・航空写真ズーム・地形のサンプル密度・座標軸、矩形で切り出す、取得対象の確認・環境診断・Blender自己診断・手動OBJモード、ログ欄とログの保存・コピー。見た目は B案の色で、詰め込んでよい（初心者は開かない前提）。

## Python 側（`BridgeApi`）

JS からは `window.pywebview.api.<名前>(...)` で呼ぶ。戻り値は JSON にできる値だけ。長い処理は別スレッドで走らせてすぐ返し、進み具合は JS が `poll()` を 150ms ごとに呼んで受け取る（Python から `evaluate_js` で押し込まない。テストしやすくするため）。

| メソッド | 中身 |
| --- | --- |
| `initial_state()` | 有名な場所・段階の文言・広さの既定値・既定の保存先・版数を返す |
| `scan()` | Blender・nusamai を探す。結果は `scan` イベント |
| `check_place(form)` | 今の `check_place` と同じ（カタログだけ）。結果は `place` イベント |
| `plan(form)` | 3問目のまとめ用。保存先が既にあれば別名を返す。入力の誤りは `{"error": 文言}` |
| `start_generate(form)` | 今の `start_generate` と同じ処理。確認ダイアログは出さない |
| `cancel()` / `open_result()` / `open_folder()` | 中止 / Blender で開く / 保存先フォルダを開く |
| `choose_output(current)` / `choose_file(kind)` | ファイル窓。選ばなければ `None` |
| `open_url(kind)` | `maps` か `blender` だけ。任意の URL は受けない |
| `preview_sources(form)` / `diagnose()` / `smoke_test()` / `open_manual()` | 詳細設定のボタン。今と同じ処理 |
| `save_log(path)` / `log_text()` | ログ。コピーは JS 側で `navigator.clipboard` を使う |
| `poll()` | たまったイベントのリストを返す |

- `form` は辞書：`mode`（center/bbox/mesh）、`area`、`size`（200/400/800/input）、`output`、`blender`、`nusamai`、`epsg`、`lod`、`zoom`、`dem_res`、`axis`、`clip`。辞書から `AutoRequest` を作る関数 `request_from_form(form)` を純粋関数として切り出し、今の `build_request` と同じ規則にする（bbox は `r:`、mesh は `m:`、center は `center_condition`）。
- イベントは `{"kind": ..., "data": ...}`。kind は今の `poll` と同じ（`scan` `place` `log` `result` `smoke_result` `done` `error` `cancelled`）に、`stage`（1〜4。`stage_for` で求める）を足す。
- 走っている間に `start_generate` などが呼ばれたら何もしない（今の `running` と同じ）。

## v2.1 の直し（2026-10-30、開発者が触った意見と Sol のレビューから）

この節は上の「画面の流れ」より優先する。

### 画面の並び（スクロールさせない）

- 入力の3問・作成中・完了・中止・失敗は、730x580 でも 900x780 でも縦スクロールを出さない。高さが足りない時は余白・文字を詰める（`@media (max-height: ...)`）。詳細設定の画面だけはスクロールしてよい。長いパスは1行で省略し（`text-overflow: ellipsis`）、`title` で全文を見せる。
- **1問目**の並び：手順バー → Blender未検出の帯（あれば。1行） → 質問「どこの街を作りますか？」 → **座標の入力欄と「確認」ボタン（いつも見えている。主役）** → 確認結果の1行 → 「Googleマップを開く」ボタン＋コピー手順の1行 → 「有名な場所から選ぶ」の小さなドロップダウン → 「矩形・メッシュコードで指定する」の折りたたみ（開くと中心点/矩形/メッシュの切替） → 戻る・詳細設定・次へ。有名な場所の大きなカードと「Googleマップで好きな場所を探す」の横長カードはやめる。
- **3問目**の並び：手順バー → 帯 → 質問「ここに保存します」 → 保存場所の切替「ドキュメント / アプリのフォルダ / その他…」 → ファイル名＋「変更」 → 場所・広さの短いまとめ → 別名保存の注意・入力エラー → 戻る・詳細設定・3Dの街を作る。

### 機能の直し

1. **Googleマップを開く**：ボタンは必ず既定のブラウザで開く。入力欄に正しい中心点があれば、そこを中心に開く（`https://www.google.com/maps/@{lat},{lon},16z`）。無ければ今の `MAPS_URL`。`open_url(kind, lat=None, lon=None)` は `maps` と `blender` だけを受け、lat/lon は数値で日本の範囲（`auto.py` の `_is_lat`/`_is_lon` と同じ）の時だけ使う。
2. **場所の自動確認**（対応地域の問題への今回の手当て）：入力欄の値が形式として正しくなったら、700ms 待ってから自動で `check_place` を呼ぶ。有名な場所を選んだ時もすぐ確認する。「確認」ボタンも残す。
   - `check_place(form, request_id)` は結果の `place` イベントに `request_id` を入れて返す。JS は最新の `request_id` 以外の結果を捨てる（Aを確認中にBへ変えた時、Aの結果をBに出さない）。ボタンの再有効化は結果が届いた時。
   - 結果は3種類に分ける：データあり（緑のチェック）／この場所にはデータが無い（`NoPlateauData`。赤のバツ＋「有名な場所から選ぶか、別の場所を試してください」）／確かめられなかった（通信などそれ以外の例外。灰色の「！」＋「通信を確かめて、もう一度押してください」）。通信の失敗を「データが無い」と言わない。
3. **入力の検査を1問目で**：新しい `inspect_area(form)` が、形式だけを通信なしで調べて `{ok, mode, explicit_size_m, error, note}` を返す。1問目の「次へ」はこれが ok の時だけ進み、だめなら欄の下に `error` を出す。
4. **広さの表示と実際を揃える**：
   - `explicit_size_m` は、中心点の入力に3つ目の数字として一辺が書いてある時だけ数値、それ以外は `None`。判定は `center_condition` と同じ規則で行う（NFKC、先頭 `r:` の除去、Googleマップの URL `@lat,lon,` を含む時と度分秒の時は**広さなし**、それ以外は数字が3つの時だけ3つ目）。`17z` を広さと読まない。
   - 2問目：`explicit_size_m` があれば4枚目「入力した広さ（300m）」を選んだ状態で出す。ここで 200/400/800 のカードを選んだら、そのカードの広さで作る。`request_from_form` は中心点で `size` が `'input'` 以外かつ `explicit_size_m` がある時、入力の最初の2つの数字だけで `center_condition` を呼ぶ。3問目のまとめは Python が返した実際の広さを出す。
5. **作成の二重起動**：`start_generate`・`preview_sources`・`smoke_test` は、「動いているか」の判定と `_running=True` を同じ `_lock` の中で行う（今は判定が鍵の外にある。`web_gui.py:288`）。JS は最初の `await` の前にボタンを無効にし、Python が断った時は今の作業の状態を消さない。
6. **完了の取りこぼし**：JS の `poll` は `setInterval` をやめ、前の `await api.poll()` が返ってから次を `setTimeout` で予約する（重ならない）。Python の `poll()` はキューの取り出しを `_lock` の中で行う。各作業に `job_id` を振り、`stage`・`result`・`done`・`error`・`cancelled` に入れる。`done` に保存先のパスも入れて、`result` との順番に頼らず完了画面へ移れるようにする。今の `job_id` 以外のイベントは画面の状態を変えない（ログには出す）。
7. **詳細設定のボタン**：取得対象の確認・自己診断も JS の作業状態に乗せる。走っている間は詳細設定の実行ボタンを無効にする。Python が断ったら（`False`/`started: False`）何も変えない。
8. **Blender未検出の帯**：帯は「使う Blender のパスが実在するファイルか」で決める。欄に文字があるだけでは消さない。新しい `blender_status(path)` が `{ok, path}` を返す。再スキャンは、欄が空か実在しない時に、見つかった最初の Blender で置き換える。Python 側の検出結果も、毎回のスキャンで更新する（初回の値に固定しない）。
9. **保存場所の切替**：
   - ドキュメント：今の既定（`_default_output()`）。
   - アプリのフォルダ：凍結EXEでは `Path(sys.executable).parent/'作った街'`、開発時は `Path(__file__).resolve().parent.parent/'作った街'`。ファイル名は今の名前を引き継ぐ。選んだ時にフォルダを作れるか・書けるかを確かめ、だめなら「このフォルダには保存できません。ドキュメントを使ってください」と出してドキュメントへ戻す。
   - その他…：今の「変更」と同じ保存の窓。
   - `output_choices()` が2つの候補のパスを返し、`use_output_folder(kind, current)` が新しいパスか `{error}` を返す。

### テストに足すもの

`inspect_area` の4つの形（`緯度,経度`／`緯度,経度,300`／Googleマップの URL の `17z`／度分秒）、カードで広さを上書きした時の `request_from_form`、2本のスレッドから同時に `start_generate` を呼んで1件だけ始まること、`job_id` と `done` の保存先、`check_place` の `request_id` と3種類の結果（通信の例外を「データなし」にしないこと）、`open_url` の座標つきと日本の範囲外、`blender_status` の実在しないパス、アプリのフォルダへの切替（書けない時の `error` を含む）。

## 確認済みの落とし穴（pywebview 6.2.1）

- `import webview` は `main()` の中で行う。モジュールの先頭で import すると、テストと Tk への切り替えが pywebview の有無に左右される。
- js_api に渡したオブジェクトは、`_` で始まらない属性を全部たどって JS へ見せる。窓・キュー・スレッド・CancelToken などは必ず `_window` のように `_` 付きで持つ。
- 保存の窓は `window.create_file_dialog(webview.FileDialog.SAVE, ...)`。`webview.SAVE_DIALOG` は非推奨。開く窓は `webview.FileDialog.OPEN`。
- 窓の作成は `webview.create_window(title, url=<index.htmlの絶対パス>, js_api=api, width=..., height=..., min_size=(730,580))`、続けて `webview.start()`（メインスレッドで）。`start()` が例外を出したら Tk 画面で起動し直す。
- 凍結EXEでは `index.html` の場所を `sys._MEIPASS` 基準で求める（開発時は `Path(__file__).parent/'web'`）。
- JS の API は `pywebviewready` イベントの後でないと使えない。

## 完了の条件

- 今のテスト126件が通ったまま、`tests/test_web_gui.py` が通る。テストは窓を作らず、`preview`・`generate` などは差し替える：`request_from_form` の3つの mode、走っている間の二重起動の無視、段階イベントが戻らないこと、中止で `cancelled` が来ること、`open_url` が知らない種類を断ること、`plan` の別名。
- `python -m plateau_blend.web_gui` で窓が開き、3問・作成中・完了・詳細設定を行き来できる（mainが目で確かめる）。
- 凍結EXEのビルドと自己検査は、この段階では行わない（次の段階）。
