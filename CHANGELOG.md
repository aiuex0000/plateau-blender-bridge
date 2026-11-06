# v0.11.3 — Sol レビューの指摘を直す（2026-11-06）

- 終了判定：最初の連絡の後に、起動した msedge.exe が本体へ引き渡して抜けると、窓が残ったままサーバーを止めていた。最初の連絡の後はプロセスの生死を見ず、`_bye` と心拍だけで判断する。
- `app_paths` の書込み確認を `tempfile.mkstemp` の一意なファイルにした（固定名 `.write-test` は既存のファイルを上書きして消し、同時起動で取り合っていた）。
- `nusamai.exe` を同梱するのにライセンス原文が無い時は、ビルドを止める（同梱の判定を spec と同じ exe の有無に揃えた）。
- 説明書の出典例：3D 都市モデルの権利は各市区町村にあるので、都市名と年度を入れる差し替え式にし、授業・社内でも出典を書く・都市の配布ページの指定に合わせるよう案内した。都市名と年度は `scene.provenance.json` の `city` / `year`。
- 書き込めない場所に置いた時の動き（ログは一時フォルダ、作った街はドキュメント）を説明書に書いた。`作った街` が同名のファイルや読み取り専用の時は、既定をドキュメントにする。
- README：GitHub Actions 版も nusamai を同梱する、に直した。

# v0.11.2 — 保存先の既定をアプリのフォルダに（2026-11-06）

- 作った街の保存先の既定を `Documents\PLATEAU\scene.blend` から、exe の隣の `作った街\scene.blend` へ変えた（新画面・予備の Tk 画面とも）。フォルダが書き込めない時だけドキュメントにする。3問目の並びも「アプリのフォルダ」を先頭にした。
- CLI で `--output` を省いた時の結果 JSON も `data\cli_result.json` へ。
- 説明書：保存先の説明と画像を差し替え、片付けの節に「消す前に作った街を移す」を足した。

# v0.11.1 — ポータブル化：フォルダの外へ書かない（2026-11-06）

- ログ（`gui-fallback-*.log`・`crash-*.log`）と Edge の専用プロファイルを、`%LOCALAPPDATA%\PLATEAU-Blender-Bridge` から exe の隣の `data\` へ移した（`plateau_blend/app_paths.py`）。フォルダごと消せば片付く。フォルダが書き込めない時だけ一時フォルダを使う。
- 説明書から「ZIP を展開する」の手順を外した（読んでいる時点で展開済みのため）。置き場所の注意と、前の版が `%LOCALAPPDATA%` に残した物の消し方を書いた。

---

# v0.11 — 一問ずつの新しい画面、Edge アプリ窓、説明書とライセンスの同梱（2026-11-05）

- 画面を「場所 → 広さ → 保存先」の一問ずつの形に作り直した（GUI v2、2026-11-02）。
- 画面の土台を pywebview（WebView2）から、ローカル HTTP サーバー＋ Edge のアプリ窓へ載せ替えた。ネットから落とした ZIP の Mark of the Web で .NET の DLL 読込が止まり、旧画面へ黙って落ちていた問題の修正。pywebview・pythonnet の同梱をやめ、ZIP は 42.9MB → 37.5MB。
- アプリ用 Edge を同期・拡張なしで開く（新しいプロファイルが利用者の拡張を全部入れ、案内タブを開いていた）。
- 予備の Tk 画面へ切り替える時は、理由を `%LOCALAPPDATA%\PLATEAU-Blender-Bridge\logs\gui-fallback-*.log` に残す。
- 配布物の exe の隣に `はじめにお読みください.txt`、画像つきの `使い方ガイド.html`、ビルド環境のライセンス原文を集めた `THIRD_PARTY_LICENSES.txt` を必ず置く（`packaging/dist_extras.py`）。
- `start_dev_gui.bat` が旧画面を直接開いていたのを、凍結 exe と同じ入口（`plateau_launcher.py`）に直した。
- README を開発者向けに書き直し、旧版は `docs/history/README_v0.10.md` へ移した。
- 記録：`docs/gui_v2_check_2026-11-02.md`、`docs/gui_edge_app_2026-11-05.md`。

v0.10 の変更は `CHANGELOG-v0.10.md`。

---

# v0.9 — 保存後の外観検証 / DEM欠損を0mにしない（2026-10-16）

- Blender再読込の `blender_verify.py` に、地形全メッシュでのpacked画像→Base Color接続、UV有限性・非縮退チェックを追加。
- OBJで使用中の `map_Kd` マテリアルだけを抽出し、各画像付きOBJの読み込み後に画像接続が残るかを確認する基盤を追加。OBJ由来識別子をBlenderオブジェクトのcustom propertyとして保存。
- 98%以上のDEMサンプル有効率なら生成してしまい、欠損頂点を0m化しうる問題を解消。DEMはサンプル100%必須、エラー時は出力しない。
- 新規8テストを含め計82テスト成功。v0.8の74テストは維持。
- 未検証: 実Blender 5.2.2のbpy動作、実地理院タイル、PLATEAU API、Windows nusamai.exe。実際の見た目の正しさや素材の出典条件の自動判定は未実装。

---

# v0.8 — 独立Blender検証・自己診断・CityGML取得範囲の改善 (2026-10-16)

- Blenderが作った一時 `.blend` を**二度目の独立プロセスで開き直す**処理を実装。建物・地形の再読込座標範囲、必須メッシュ、地形の有効UV、埋め込まれた外部テクスチャ、出典メタデータ、EPSG/原点を検査。失敗時は正式な完成 `.blend` を発行しない。
- ネット接続・GIS Converter CLI不要の **Blender自己診断**を追加。GUIボタンと `auto_cli --smoke-test` で合成OBJ/PNG/PGWから本番パイプラインと同じ保存/再読込検査を実行できる。実機での失敗ログは `<output>.work/last_verify.log`。
- `os.replace` の競合上書きを防止。既定モードはハードリンクによる排他的ファイル確定とし、非対応ファイルシステムでは一時成果物を残して安全側で停止。
- PLATEAUの市単位最新版選択を**市・メッシュ単位の最新年選択**に変更。新年度の欠けたメッシュを旧年度データで補完し、異なる年度の混在はログで警告（地物のシームレスな整合は保証しない）。
- CityGML XML検査をルート要素のみから、DTDを拒否する**Expatストリーム全体構文チェック**に変更。途中で切れたXMLや末尾の破損を早期検出。
- テスト74件成功（v0.7は62件）。保存再読込プロセスは本体未インストールのためモック、Blender実機未検証。Python構文チェックとZIP再展開テストを実施する。

---

# Changelog

## v0.7 — Cache integrity and input-format hardening (2026-10-16)

- Normalized building and terrain OBJ caches now verify SHA-256 digests of **every staged output** (including staged MTL and textures) before reuse. A stale/altered/truncated cache is rebuilt, not silently accepted. Successful staging uses `.part` and only publishes a ready marker after hashing.
- Projected-clipped building OBJ caches now verify geometry + material asset SHA-256, recover from malformed metadata, and record empty clips separately. Old cached-clip markers intentionally regenerate once.
- `normalize_obj` now checks the cancellation token while streaming large OBJ files; interrupted work is not marked completed.
- Downloaded `.gml` now checks the parsed XML root name `CityModel` instead of merely checking for a leading `<` (which previously accepted HTML error pages). Corrupted JSON cache metadata and matching-hash non-GML cached files are recovered from network instead of trusted. This is a quick format gate, **not** full CityGML XSD validation.
- `.blend` building aborts if the requested image packing fails instead of publishing a portable-looking but externally dependent file.
- 62 offline unit/integration tests pass; includes 10 new tests for damaged normalized/clipped OBJ, texture asset tampering, cancellation during normalization, HTML masquerading as CityGML, and corrupted GML cache metadata.
- **No real PLATEAU/GSI download, Windows nusamai.exe or Blender 5.2.2 run yet**; end-to-end remains simulated.

# Changelog

## v0.6 — Offline scalability and verified resumption (2026-10-16)

- Store imported OBJ vertex/UV/normal tables in disk-backed float64 random-access records during polygon cropping. Avoid tuple-list O(number of input vertices) Python heap retention.
- Check cancellation during the large OBJ scan and polygon clipping loops.
- Replace per-pixel coordinate reprojection of aerial imagery with a vectorized per-scanline pyproj transform to EPSG:3857. Retain PGW georeferencing in the selected Japanese projected EPSG.
- Re-download damaged GSI tile-cache entries; reject invalid new image dimensions.
- Verify cached Converter OBJ / MTL / dependent textures using SHA-256; regenerate if content has changed.
- Bound child-process log queue memory; keep streamed durable logs.
- Add offline regression tests for pixel equivalence, high vertex count without Python heap growth, cancellation mid-file, noisy child processes, damaged tiles and invalid converter cache.
- 52/52 offline tests pass. **No native Windows, live PLATEAU download or real Blender binary test has been performed.**

## v0.5 — Cancellation, planning, diagnostics

- GUI cancellation and cancellation-aware child process launching.
- Photo/DEM tile forecasts and safety budgets.
- Environment diagnosis of Blender, nusamai, dependencies and free disk space.
- API error handling and allowed redirect checking.

Earlier v0.1–v0.4 packages remain separate for reproducible comparisons.
