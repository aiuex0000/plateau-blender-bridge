# 引継ぎ：PLATEAU Blender Bridge を Blender アドオン（純Python）へ作り直す

作成: 2026-11-11 / 宛先: 新規チャット（Fable）

## なぜ作り直すか

- 利用先（リスタル・松井さん）の会社PCは Windows 11 の **スマートアプリコントロール（SAC）** が有効。署名のない exe（`PLATEAU-Blender-Bridge.exe`、同梱の nusamai）が止められる。Defender の除外や開発者モードでは通らない。SAC にはアプリ単位の例外がなく、切ると戻せないので、会社PCでは切らない方針。
- SAC は署名のない `.pyd`（Pythonのネイティブ拡張）も止める（Anki で同じ報告あり）。
- コード署名は採らない（マスター判断）。デモサーバでのWeb化は、サーバがWordPress程度の共用環境で、Dockerも重い処理も不可のため断念。専用VPSも費用の面で不可。
- このツールは「利用者さんが Blender を覚えるマニュアルの中で使う道具の一つ」。費用をかけず、Blender の中で完結させる。
- 署名済みの Blender 本体は動くので、**Blender の中で動く純Python のアドオン**にすれば SAC を通る見込み。

## 作るもの

Blender のサイドバーから地域・範囲を選び、PLATEAU の建物と国土地理院の地形・航空写真を取り込むアドオン。

### 絶対条件

- **純Python のみ。** `.pyd`・`.dll`・exe を同梱・呼出ししない。Blender に同梱の `bpy` / `bmesh` / `mathutils` / 標準ライブラリと、Blender 同梱の numpy までにする。numpy を使うなら、Blender 同梱品で SAC が通るかを段0で確認する。
- 次の三つを置き換える：
  - nusamai（CityGML → OBJ）→ CityGML を `xml.etree`（iterparse）で読み、`bmesh` で面を直接作る。多角形はそのまま n-gon で入れてよい。
  - Pillow → `bpy.data.images` と pixels で代用する。
  - pyproj → 平面直角座標系（JGD2011、EPSG:6669〜6687）の変換式を純Pythonで書く。
- 外部への通信は `urllib`（Blender 同梱の ssl）で行う。

## 使える資産

- 既存の本体（このrepo）：`plateau_blend/` の `catalog.py`（PLATEAU のデータ探し）、`gsi.py`（地形・航空写真のタイル）、`clip.py`、`subset.py`、`appearance.py`、`crs.py`、`blender_scene.py`。Pillow と pyproj を外せば流用できる部分が多い。
- 既存のアドオン：**nneri-hin/Plateau-Blender-Importer**（GitHub）。PLATEAU の CityGML を Blender へ読み込む。テクスチャ・道路・地形に対応し、メッシュコードや緯度経度で中心を指定できる、と紹介されている。最終更新は約3年前。
  - **まずこれを読む。** ライセンスが取り込みを許すなら、ベースとして取り込んでよい（マスター了承済み）。取り込む時は LICENSE と出典を残す。許さない、または不明なら、設計の参考にだけする。
  - ネイティブ依存（`.pyd` を要する部品）の有無と、今の Blender（4.2以降の拡張機能の形式）で動くかを確認する。

## 段取り

| 段 | 中身 | 完了の目安 |
|---|---|---|
| 0 | 最小の試作アドオン：ボタン1つで地形タイルを1枚 `urllib` で取り、板メッシュにテクスチャを貼る。zip でインストールできる形 | マスターの環境で動く。松井さんへ渡す試作と短いお願い文の案ができている（**送信はマスターが行う**） |
| 1 | 地形と航空写真（`gsi.py` 相当） | 指定範囲の地形と写真が Blender に出る |
| 2 | 建物の形（LOD1 / LOD2） | 既存の版と同じ範囲で、形と位置が一致する |
| 3 | 建物のテクスチャ（ParameterizedTexture） | 既存の版と見た目が揃う |
| 4 | サイドバーの操作パネル（地域・範囲・設定の確認） | 今の Web 画面の質問の流れが Blender の中で通る |

- **段0は制作の一番最初に行う。** 試作を松井さんに試してもらうことはマスター了承済み。試作 zip と、入れ方・確かめてほしいこと（入るか、ボタンで板が出るか、松井さんの Blender の版）を書いた短いお願い文を作り、マスターに渡す。段1以降は、松井さんの結果を待たずに進めてよい。
- 段0が SAC で通らなかったら、そこで止めてマスターへ報告する。
- 大きいデータでの速さ（純Python の XML 解析）は段2で測って報告する。範囲の上限を設けるかは、その時にマスターと決める。

## 守ること

- 作業場所はこのrepo（`C:\Users\You\Workspace\PLATEAU-Blender-Bridge`）。アドオンは新しいフォルダ（例：`blender_addon/`）に置き、既存の exe 版は壊さない。
- 正本は `core.autocrlf=false` で LF。CRLF を混ぜない（`git ls-files --eol` で確認）。
- 松井さん（Slack）への連絡、GitHub への push・リリースは、マスターの確認を取ってから行う。
- 未追跡の `docs/handoff_2026-11-10_claude.md` と `docs/handoff_2026-11-10_v0.11.7_worktree.patch` はマスターのものなので、触らない。
