# PLATEAU → Blender Bridge v0.10 — Windows配布方式への変更

**利用者はPython・venv・pip不要。** Windows向けビルドでは `PLATEAU-Blender-Bridge.exe` をダブルクリックして起動する。ただし、このZIPは**ソースコード**であり、Windows EXEは含まない。 `.github/workflows/windows-build.yml` をWindows GitHub Actionsで実行すると、配布用の `PLATEAU-Blender-Bridge-v0.10-win-x64.zip` を生成できる（**ビルドのWindows成功はまだ未確認**）。

- 利用者：完成版ZIPを展開 → `PLATEAU-Blender-Bridge.exe` を起動。必要な外部アプリはBlender、CityGML変換時にはnusamai CLI。Pythonのインストールや環境構築は不要。
- 開発者：`dev_setup_windows.bat` でソース開発用のvenvをセットアップ。**テストは自動実行しない**。 `build_windows.bat` でWindows用アプリを構築。
- Windowsでのエラー2件への対処：UTF-8 JSONを明示的に読むテストへ修正。UnixシバンをWindowsの実行ファイルとして起動していた模擬BlenderをWindows `.cmd` ランチャーへ置換。
- 配布形式はPyInstaller `--onedir`（インストーラなし、フォルダ一式を展開）で、Blender本体とnusamaiは同梱しない。`blender_scene.py`・`blender_verify.py` をデータファイルとして同梱し、`pyproj` の投影データも同梱対象。
- **実機確認**：Pythonテスト、PyInstallerでのWindowsビルド、生成したEXEの起動、Blenderの自己診断が必要。ソース段階で「EXE完成」とは扱わない。

## 旧版の説明・アルゴリズム

# PLATEAU → Blender Bridge v0.9 (material / UV / DEM quality gates)

This Windows-first project fetches **building CityGML from PLATEAU**, uses the independent **nusamai CLI** to convert buildings to georeferenced OBJ, fetches **GSI aerial photographs and DEM elevation**, reprojects both to a specified *projected metre EPSG*, and saves a Blender `.blend`. It includes an offline test fixture and the original manual OBJ workflow from v0.1.

**State as of 2026-10-16:** 82 offline Python tests passed on Linux. v0.8 adds Blender scene readback verification, a real-Blender local smoke test, atomic no-overwrite publication, latest-per-mesh year selection, and whole-file CityGML XML validation.  This version additionally tests CityGML imageURI texture dependencies, safe path handling, resumable image downloads, MTL image resolution, hash-validated generation cache, and EPSG inference from catalog mesh codes. The entire automatic acquisition path passed with a mocked PLATEAU API, local fake GSI tiles, a fake converter, and a fake Blender runner. **No genuine PLATEAU/GSI downloads or Blender executable ran in this environment. Real `.blend` correctness is not yet established. The Blender reopen verifier is implemented but has only been tested via mock processes.**


## v0.9 — テクスチャと標高の誤完成を防ぐ

- **再読込検証を強化**: 単に `.blend` 内に画像が存在するだけでは合格しない。地形の全メッシュで *packed画像テクスチャがPrincipled BSDFのBase Colorに接続*されているか、UVにNaN/Infがないか、U/Vの広がりがゼロでないかを検査。地形の一部で航空写真が欠落・未接続なら `.blend` は公開しない。Blender本体での動作は引き続き未検証。
- **建物のMTLチェック**: `usemtl` で実際に使っているマテリアルに `map_Kd` が存在した場合、そのOBJに由来するBlenderメッシュのどれかにpacked画像が接続されていることを再読込時に確認する。未使用MTLのテクスチャは誤検出しない。この確認は「少なくとも1つ画像接続がある」という最低限のゲートであり、全フェイスの見た目や、複数マテリアルすべての貼り分け保証ではない。
- **DEM欠損の黙認を解消**: 以前は有効値が98%以上あれば、欠損サンプルを出力OBJの頂点 `z=0` として残す余地があった。現在はデフォルトで**100%のDEMサンプル有効性を要求**し、1点でも欠損があればOBJ書き込み前に明示エラーで停止。水域など欠損のある場所では、補間等がない限り生成できない可能性がある。勝手に地面を補間しない保守的な仕様。
- 旧v0.8のテスト74件と追加8件を合わせて**82件成功**。Python実行環境でのオフラインテストであり、Blender 5.2.2、Windowsのnusamai、実際のAPI/写真タイルのエンドツーエンド実行はまだ未実施。

**検証レポート項目の追加**: `terrain_textured_mesh_count`, `terrain_bad_uv_mesh_count`, `building_textured_sources`。v0.8の模擬レポートはv0.9検証の合格形式にそのまま利用できない。実Blenderが実行されると新しいレポートが自動出力される。

## v0.8: 生成物の自動検証とローカル自己診断（重要）

従来は Blender の終了コードと `.blend` ヘッダーのみで成功を判定していました。v0.8 では、**保存直後の一時 `.blend` を独立した Blender プロセスで開き直し**、必要なコレクション、実際のメッシュの座標範囲、地形UV、画像のpack、CRS/原点を検証します。検証に失敗すると正式な出力パスには公開しません。理由は `<scene>.work/last_verify.log` と `verify_*.json` で調査できます。独立再読込は Blender の起動が一回増えるため、処理時間・メモリピークには影響します。

**Blender自己診断（ネットワークなし・nusamai不要）**：GUI の「Blender自己診断」をクリックするか、CLI で次を実行します。

```powershell
.venv\Scripts\python.exe -m plateau_blend.auto_cli --smoke-test
# Portable版のBlenderを明示する場合
.venv\Scripts\python.exe -m plateau_blend.auto_cli --smoke-test --blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --smoke-dir "C:\temp\plateau-diagnostics"
```

合成データのOBJ（建物と地形）・画像・PGWを作り、**本番と同じ `pipeline.run` を使って** `.blend` を生成・再読込します。成功時は `blender_smoke_日時/` 内に `smoke.blend` と、動作再現用の全入力、ログ、provenanceが残ります。元の都市モデルに触れず、外部公開・データ取得も行いません。`[SMOKE OK]` を確認しても、CityGMLのCLI互換性や地形の高度基準が検証されたことにはなりません。

**出力の上書き保護**：通常モードでは `os.link` により同名出力の割り込み生成があっても上書きしません。保存先のファイルシステムがハードリンクに対応していない場合は、安全のため公開処理を停止し、一時 `.blend` を残します。NTFS等のハードリンク対応ボリュームを推奨します。明示的に `overwrite_output=True` とした内部・手動モードのみ従来どおり置換できます。

**カタログ年次処理**：市の最新年度が全メッシュを含むとは限りません。各 `(自治体コード, メッシュ)` で最も新しい調査年のGMLを選び、同じ自治体で旧年度を補完したときはログに警告します。**異なる収録年度を混在させた建築物の継ぎ目・位置は、実データで追加確認が必要**です。GMLの完全XML構文もストリーム検査しますが、CityGMLのXSD適合性までは保証しません。

## Previous source-only setup (superseded by v0.10)

1. Install Python 3.10+ with the `py` launcher, and install Blender 5.x (5.2.2 LTS is the validation target).
2. Install or build the `nusamai.exe` **command-line binary** from [MIERUNE/plateau-gis-converter](https://github.com/MIERUNE/plateau-gis-converter). **The regular `PLATEAU GIS Converter` GUI executable is not a substitute.** The currently documented upstream CLI includes `--epsg` for projection. The official repo's README documents CLI binary downloads for macOS/Linux; a Windows CLI binary may need a Rust release build. Do not assume an installed GUI exposes the CLI. The automatic GUI will attempt to locate the CLI on PATH, `NUSAMAI_EXE`, and a few conventional folders; otherwise browse to the binary.
3. Unzip into a writable directory; run `setup_windows.bat`. It creates a local `.venv` and installs Pillow + pyproj; it **does not modify the machine-wide Python environment**.
4. Run `start_gui.bat`. Blender detection starts immediately in the background; if your portable Blender is not detected, browse to its `blender.exe`.
5. For a first run select **緯度経度の矩形**, enter a **small** range `west,south,east,north` (e.g., a bounding box roughly 200 m across), leave EPSG as `AUTO` to infer one of the 19 JGD2011 projected zones from the rectangle (or specify a zone manually), choose `min_lod`, zoom 14–16, and give a **new** `.blend` output path. Use **取得対象の確認** first. Then **自動取得して .blend を生成**.
6. Once saved, click **完成ファイルをBlenderで開く**.

For previously exported OBJ/DEM/PNG/PGW, use `start_manual_gui.bat` (the preserved v0.1 workflow). `start_gui.bat` launches the new automatic GUI.

## Area choices, clipping and caveats

- **Bounding box:** `r:west,south,east,north` (WGS84 longitude,latitude degrees). Use a small area to limit downloads. This is the most verifiable method.
- **Mesh:** `m:<meshcode>` where mesh code has 6/8/9/10 digits. EPSG `AUTO` can infer from a single mesh code or the returned catalog mesh set.
- **Place:** `g:池田市` or another Japanese name; EPSG `AUTO` resolves after retrieving the catalog mesh codes (fails if zones disagree). The API may resolve a much wider area than expected; file limits stop oversized requests.

**Important:** The API returns CityGML at its stored *mesh/file granularity*. From **v0.3**, a geographic rectangle (`r:west,south,east,north`) is transformed into a conservative XY bounding rectangle in the selected projected CRS. Buildings are surface-clipped to that rectangle **after** CityGML → OBJ conversion, preserving UVs, normals, winding and material assignments. GSI terrain and orthophoto now match the selected projected rectangle rather than the entire CityGML tile. This is an XY rectangle approximation of the geographic footprint, **not** an exact geodesic boundary, and it does not cap holes in cut building surfaces or guarantee a watertight result. **`g:` and `m:` searches still include entire returned mesh/file extent**, as those queries do not provide an explicit user rectangle. Use the GUI checkbox or CLI `--no-clip` to retain the original full-mesh behaviour.

## Quality and safety

- EPSG=`AUTO` infers a JGD2011 Japan Plane Rectangular Zone (EPSG 6669–6687) using the geographic rectangle or catalog mesh-code centers, checking pyproj/PROJ area-of-use and zone origins. For `g:`/multiple `m:` searches, it resolves **after** catalog lookup and refuses to auto-select when meshes require different zones. Source mesh center is a projection-choice hint, **not** the exact building location.
- Uses `pyproj.Transformer(..., always_xy=True)` for **longitude,latitude ordering** and an explicit metric projected EPSG for OBJ + imagery + terrain. Photo `.pgw` is generated from pixel **centers**. Image+model projection must agree.
- Blender imports *origin-rebased* OBJ, checking world bbox after import before saving. The original coordinates and data-acquisition metadata are recorded in the `.blend` text and provenance sidecar.
- Query budgets: **24 building GML files**, **250 MiB combined download** (including files with unspecified server sizes), **150 MiB max per GML**, **15 km max selected rectangle/converted bbox**; fail with a clear diagnostic if exceeded.
- GSI zoom 14–18 imagery, with a maximum number of tiles; large high-zoom requests downshift to zoom 14. Output raster edge length ≤2048 px. Black/transparent photo detection is retained.
- Terrain defaults to GSI DEM raster sampled to a generated OBJ, priority DEM5A → DEM5B → DEM5C → DEM10B; invalid heights are represented as holes. If more than 2% of samples are missing, stop rather than invent flat land. **GSI DEM and PLATEAU building datasets are not guaranteed to share vertical origins or measurement dates.**
- Individual GML source downloads are additionally verified by SHA-256 before cache reuse; redirects to unapproved hosts are rejected before following them.
- Outputs, GML downloads and tiles are stored under `<output-name>.work/auto/`; successful downloads, OBJ conversion, DEM and image results are reused on subsequent attempts. Generated DEM/orthophoto/PGW caches now have SHA-256 manifests: altered, truncated or invalid markers trigger regeneration. Source files are not edited; final `.blend` never automatically overwrites an existing one.
- `nusamai` must be capable of outputting projected OBJ with `--epsg`. Its precise output coordinate convention, material links and supported option combinations need a Windows test. If axis is ambiguous, automatic mode **stops**, and the axis combobox offers explicit `xyz`/`yxz` overrides after inspection.
- When `--lod textured_max_lod` is selected, the tool additionally parses `app:imageURI` references from each GML, resolves them within the same PLATEAU CMS dataset, downloads the referenced images (up to 1000 references / 120 MiB per source; rectangle requests first keep only nearby buildings), preserves their original relative directory structure for nusamai, and verifies MTL image references after conversion. Dangerous/out-of-dataset URLs and broken images **stop the process**. Other LOD modes skip this additional texture fetch. This behavior is implemented and mocked, not yet exercised on real CMS datasets. The GSI orthophoto on DEM is independent.
- The app fetches public data over HTTPS only from allowlisted PLATEAU asset and GSI hosts, with tile and file download bounds. There is **no executable auto-download or installation**. Third-party terms must be checked before public redistribution of generated `.blend` or bundled imagery.

## v0.7: stricter cache reuse and image portability

- Normalized OBJ caches (`.work/cache`) now verify every derived output (OBJ, MTL, dependent textures) against SHA-256 before reuse. Damaged files and malformed completion markers cause regeneration. This replaces the v0.6 timestamp-only completion marker.
- Clipped OBJ caches (`.work/auto/clipped`) now have integrity manifests for the geometry and associated material assets; malformed markers and tampered results are rebuilt. Valid empty clips are cached distinctly.
- CityGML downloads parse the first XML root name and require `CityModel`. This rejects HTML error responses and generic XML, without running full CityGML schema validation. Broken or stale JSON metadata is rebuilt safely.
- OBJ normalization checks for user cancellation periodically even during multi-million-line passes.
- If **pack_image** is enabled, Blender-side image-packing failures stop generation instead of silently creating a nonportable `.blend`. This requires actual Blender verification.
- 62 offline tests passed, including damaged cache and cancellation regressions. No live data services or Blender were available in the test runtime.

## v0.6: memory use, orthophoto reprojection, and recovery

- **OBJ clipping is disk-backed**: `v`, `vt`, and `vn` are indexed as compact 64-bit binary temporary files mapped for random reads instead of creating Python tuple lists for every coordinate. Cropping still emits standard `OBJ/MTL` and preserves UVs, normals, material links and winding. It needs **temporary disk space**; large outputs are still expensive, and Windows memory-mapped temporary-file behaviour requires real-machine verification.
- **Cancellation reaches clipping**: a stop request is checked periodically while reading vertices and face statements, rather than only between OBJ files. Interrupted files are not marked as cached successes.
- **Orthophoto reprojection is vectorized by scanline** via `pyproj` to EPSG:3857 followed by tile-index arithmetic, instead of performing a separate Python CRS transform for every pixel. The PGW stays in the **selected local metric CRS**. An offline pixel-by-pixel reference comparison validates output for an example near Ikeda, including multi-tile sampling.
- **Converter OBJ cache verifies SHA-256** of the generated OBJ, material files and referenced textures. Corrupted files trigger a fresh conversion. The old v0.5 converter marker deliberately does not count as verified; its conversion will be re-run once.
- **GSI cached tiles are revalidated** and a truncated/wrong-sized cached tile is downloaded again. Malformed new tiles are rejected rather than written to cache. Cache cleanup is atomic using `.part` temporary files.
- **External process output queue is bounded** (512 lines); heavy log output does not create unbounded in-memory backlog. Child process cancellation and log files remain supported.
- **Tests**: 62 offline unit/integration tests (including a 120,000-vertex low-memory fixture, a 4,000-line chatty subprocess, cancellation while spooling, corrupt GSI tile retry, and converter-cache tampering). These are synthetic checks, **not** proof that CityGML + nusamai + Blender works on a Windows machine.

## v0.5: cancellation, planning and diagnostics

- GUI **■ 中止** button interrupts between workflow phases and during file downloads, tile processing, photo rows and terrain rows. When `nusamai` or Blender is running, it terminates the launched process tree (best effort; OS limitations apply), writes logs and retains successful downloaded assets for a later re-run. It does **not** delete previously generated source files. A pending network read may take up to its timeout to stop; interrupted CPU-intensive OBJ clipping does not immediately stop within a single source OBJ file.
- **取得対象の確認** now prints a projected-range forecast for explicit `r:` rectangles: requested and fallback photo zoom, estimated photo tiles, DEM tile positions and conservative request upper bound, output image dimensions and terrain mesh vertices. These are **not** estimates of CityGML expansion, disk use or Blender peak memory. Actual remote imagery may contain missing tiles.
- `--diagnose` or GUI **環境診断** probes `blender --version`, `nusamai --version`, Python dependency versions, and available space on the selected output volume. It never downloads executables, performs elevation downloads or writes project source files. The GUI probes executables from a background thread.
- PLATEAU API response parsing accepts either a single city JSON record, an array of records or the `cities` envelope and surfaces a JSON `error` response explicitly. GSI redirects to a different host are rejected **before** following them.
- Before GSI generation, a conservative cap checks the tile count (default 160 possible tile requests). If too wide, shrink the selected area rather than relying on a very large hidden download. Multiple texture-sourced GML files share a cumulative 120 MiB texture budget (default), in addition to per-file controls.

## CLI / troubleshooting

```powershell
# Diagnose installed tools before any download:
.venv\Scripts\python.exe -m plateau_blend.auto_cli --diagnose

# Show what GML files will be downloaded without downloading anything:
.venv\Scripts\python.exe -m plateau_blend.auto_cli --condition "r:135.429,34.819,135.431,34.821" --epsg AUTO --output scene.blend --preview

# Auto-acquire and build scene:
.venv\Scripts\python.exe -m plateau_blend.auto_cli --condition "r:135.429,34.819,135.431,34.821" --epsg AUTO --output scene.blend --lod min_lod --zoom 16 --nusamai "C:\\tools\\nusamai.exe"

# Fully offline Python tests:
.venv\Scripts\python.exe -m unittest discover -s tests -v

# v0.1 legacy preflight:
.venv\Scripts\python.exe -m plateau_blend.cli --config examples/config.example.json --preflight
```

Project-specific logs are in `<scene>.work/last_blender.log`, `<scene>.work/auto/converter/last_converter.log`, and the metadata in `<scene>.work/auto/acquisition.json`. The auto path always requests `--epsg` when invoking nusamai, which should prevent accidental degrees vs metres output; if the binary rejects the option, upgrade/build the CLI instead of bypassing the projection check. If `xyz` and `yxz` both look geographically plausible, auto axis validation refuses to guess.

## Directory layout

```text
plateau_blend/
  auto_gui.py        # primary Windows GUI, dependency scan, cancel button and progress log
  auto_cli.py        # CLI front end, including discovery preview
  auto.py            # orchestration and caching
  clip.py            # polygon/UV/normal projected XY crop
  crs.py             # JGD2011 EPSG zone inference (bbox + mesh metadata)
  appearance.py      # CityGML imageURI dependencies and bounded secure acquisition
  artifact_cache.py  # hash-verifiable DEM / orthophoto cache
  catalog.py         # PLATEAU API lookups, per-GML downloads/budgets
  discovery.py       # read-only Blender/nusamai finder
  diagnostics.py     # explicit --version probe + available disk report
  runtime.py         # cancellable child process and log streaming
  resources.py       # rough tile/image/terrain budgets from projected bounds
  gsi.py             # GSI orthophoto and DEM tiles / CRS reprojection
  converter.py       # nusamai command generation, legacy CLI interface
  config.py          # manual workflow schema
  pipeline.py        # safe Blender generation and logging
  blender_scene.py   # bpy code executed inside Blender
  geo.py, obj.py     # coordinates, OBJ inspection, source-safe normalization
  gui.py, cli.py     # retained manual workflow
examples/            # offline fixture and old example config
tests/               # 82 tests (incl. mock Blender readback, synthetic smoke preflight, multi-year catalog fallback)
```

## Rights, attribution and source references

- [PLATEAU API / CityGML file search (trial)](https://docs.plateauview.mlit.go.jp/api/rest/operations/datacatalogcitygmlconditions/); [PLATEAU API trial notice](https://docs.plateauview.mlit.go.jp/api/rest/).
- [GIS Converter CLI usage (current upstream)](https://mierune.github.io/plateau-gis-converter/manual/use_command_line.html).
- [GSI map-tile list and terms](https://maps.gsi.go.jp/help/use.html); [GSI elevation tile specification](https://maps.gsi.go.jp/development/demtile.html); [GSI complete tile list](https://maps.gsi.go.jp/development/ichiran.html).

The GSI seamless orthophoto mosaics may contain imagery from other providers with additional source attribution. The generated `.blend` stores baseline GSI/PLATEAU credits; **that is not a substitute for checking additional attribution and rights for public distribution**.

## What is NOT yet verified or supported

- No live PLATEAU API, GSI tile server, Windows `nusamai` binary or actual Blender executable was run in this environment. All end-to-end automation tests used mocks.
- Cropped OBJ geometry duplicates per-face corner vertices; this prioritizes attribute safety over mesh compactness. Test performance on unusually dense city meshes. No watertight boundary caps.
- Only `imageURI` references for ordinary raster files (`.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`) within the original dataset tree can be auto-fetched. External/absolute references, unrecognized asset layouts, unsupported image formats and unexpected redirects fail safe. Actual CMS asset-directory accessibility and MTL conventions require real-data verification.
- `g:` / `m:` now infer an EPSG via catalog mesh centers; this is a zone selection only. Source geography and final object axes must still pass the projected bbox plausibility check; cross-zone selections stop.
- The building model and GSI terrain may use different survey dates and vertical reference conventions. Automatic vertical datum correction is not implemented.

## v0.4–v0.5 additional failure/recovery notes

- A `textured_max_lod` run first downloads the selected `.gml` into a hashed source cache, then stages a second copy with original `udx/bldg/...` relative path beneath `.work/auto/citygml/`, fetching appearance images alongside. It does not alter original GML content.
- Incomplete image downloads use `.part`; a JSON manifest records completed images. On rerun, matching SHA-256 entries are reused; changed or corrupt images are re-fetched. Unknown absolute image URLs are intentionally rejected instead of silently missing textures.
- Missing `map_Kd` or other MTL texture maps from OBJ conversion fail before Blender starts. Other texture source forms besides CityGML `imageURI` are not yet auto-fetched.
- Photo `.png` + `.pgw` and generated terrain `.obj` have checksummed manifests in `auto/generated`; a mismatched checksum triggers a new generation using the tile cache.
- **Resource ceilings:** default up to 1000 texture images and 120 MiB per downloaded GML; no unsolicited executable download, no destructive cleanup of source data. Raise quality gradually after testing on small real scenes.
