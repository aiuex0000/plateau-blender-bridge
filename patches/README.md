# 同梱nusamaiへのパッチ

`build_nusamai.bat` が MIERUNE/plateau-gis-converter v0.1.19 をcloneした直後に当てる。MITライセンスの範囲での改変。

## nusamai-v0.1.19-textured-lod-fallback.patch

- 症状：`-t use_lod=textured_max_lod` で、テクスチャを持たない建物が**面ゼロ**で出力される。池田市 52351384 の矩形周辺372棟のうち327棟が空になった（`max_lod` では0棟）
- 原因：`FilterLodTransform` の `TexturedHighest` が、テクスチャのあるLODを探すループ内で `edit_tree(&mut entity.root, lod)` を呼び、元の木から他のLODを削ってしまう。テクスチャが見つからない建物は最後に最高LODへ戻そうとするが、その時にはLOD0まで削られた木しか残っていない
- 修正：探索は `entity.root.clone()` 上で行い、最終決定したLODだけを元の木へ適用する
- 修正後：同じ372棟で空0棟、面数17,534（`max_lod` と同じ）
- 上流への報告：未実施（外部への書き込みのためマスター確認待ち）
