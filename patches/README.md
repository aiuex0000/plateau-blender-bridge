# 同梱nusamaiへのパッチ

`build_nusamai.bat` が MIERUNE/plateau-gis-converter v0.1.19 をcloneした直後に当てる。MITライセンスの範囲での改変。

## nusamai-v0.1.19-textured-lod-fallback.patch

- 症状：`-t use_lod=textured_max_lod` で、テクスチャを持たない建物が**面ゼロ**で出力される。池田市 52351384 の矩形周辺372棟のうち327棟が空になった（`max_lod` では0棟）
- 原因：`FilterLodTransform` の `TexturedHighest` が、テクスチャのあるLODを探すループ内で `edit_tree(&mut entity.root, lod)` を呼び、元の木から他のLODを削ってしまう。テクスチャが見つからない建物は最後に最高LODへ戻そうとするが、その時にはLOD0まで削られた木しか残っていない
- 修正：探索は `entity.root.clone()` 上で行い、最終決定したLODだけを元の木へ適用する
- 修正後：同じ372棟で空0棟、面数17,534（`max_lod` と同じ）
- 上流への報告：しない（2026-10-21、開発者判断）。2026-10-21時点の上流mainも同じコードのまま

## nusamai-v0.1.19-drop-unused-sinks.patch

- 目的：Bridgeが使わない出力形式を外し、再配布条件を確認できない依存 `kv-extsort`（git固定commit `f23b1e51`。Cargoのlicense宣言、LICENSE・NOTICE、ソース内の許諾表示のいずれも無い）を配布物から除く。作者への確認は行わない（2026-11-06、開発者判断）
- `kv-extsort` を直接使っていたのは3D Tiles sinkの並べ替え段と、MVT・MLT・PMTilesが共有する `sink/vector_tile` の並べ替え段だけ。この4 sinkを `BUILTIN_SINKS` から外し、モジュールごと削除した
- glTF sinkが使う `sink/cesiumtiles/metadata` と `utils` だけは残す（外部crateに依存しない）
- 依存から `kv-extsort`・`cesiumtiles`・`mlt-core`・`pmtiles` を外した。上流のTauri GUI（`app/src-tauri`）はworkspaceから外した（BridgeはCLIだけを同梱する）。`Cargo.lock` は版を上げずに不要分だけ刈った物をパッチに含めるので、`build_nusamai.bat` は `--locked` でビルドする
- 上流テスト `tests/sink.rs` から外したsinkの4件を除いた
- OBJ sink・変換処理には手を入れていない

## 参考：パッチ対象外の気づき

- v0.1.19のOBJ出力で `textured_max_lod` を使うと、`--output` が相対パスの時に `sink/obj/mod.rs:581` の `Url::from_file_path(...).unwrap()` でpanicする。Bridgeは常に絶対パスを渡すので影響なし
