# Windows配布版の配置と起動（2026-10-29）

開発の正本は `C:\Users\You\Workspace\PLATEAU-Blender-Bridge`。Workbench repo内の `projects/plateau-blender-bridge` は写しです。正本直下と確認した上位階層にAGENTS.mdはなく、Git remoteもありません。

利用者向け配布先は `C:\Users\You\Workspace\Workbench\Projects\PLATEAU-Blender-Bridge`。

- `app/PLATEAU-Blender-Bridge.exe`: アプリ本体。周囲のファイルを含めて維持します。
- `PLATEAU-Blender-Bridge.zip`: 元のdistからコピーした配布ZIP。
- `PLATEAU-Blender-Bridge.lnk`: この配布フォルダ内の起動ショートカット（デスクトップには設置していません）。
- `start.cmd`: 配布EXEを起動する補助入口。
- `verification/`: 配置後のself-check JSON、生成ログ、出力先の記録。

正本の `start_gui.bat` は配置済みEXE専用、`start_manual_gui.bat` は同じEXEの `--manual` 専用です。ソース開発は正本内の `start_dev_gui.bat` を使います。開発用venvは `C:\apps\PLATEAU-Blender-Bridge\.venv` を優先し、無ければ正本の `.venv` を使います。C:\appsは既存の開発環境として残し、今回移動・削除・変更していません。開発用nusamaiも残しています。

保存先の既定は `Documents\PLATEAU\scene.blend`。同名があればGUIは別名に採番します。ソース変更は再ビルドするまで配布EXEへ反映されません。使用中のdistへ直接cleanビルドせず、引継ぎにあるdist-next方式を使ってください。

## 今回の検証

- distと配置後の1,085ファイル・ZIPはSHA-256一致。
- 配置EXEのself-check: exit 0、passed true、nusamai・投影データ・Blenderスクリプトあり。
- 配置EXEのCLIで池田 r:135.429,34.819,135.431,34.821 / max_lod: exit 0。既存出力を避け、C:\Users\You\Documents\PLATEAU\scene_4.blend を保存。独立Blender再読込で建物1・地形1・地形UV不良0・画像1枚packを確認。
- 正本の単体テスト: 115件成功、skipなし（アプリコード変更なし）。
- TerraのComputer Useで配置EXEの初期画面・詳細展開・スクロール後の診断とログ操作欄を確認。内部UIのアクセシビリティ情報が弱く、Tab到達性は確認できず。
- 未確認: 125%/150%実表示、1366x768相当、キーボードだけの生成、GUIのキャンセル・再試行・ログ保存/コピー、P2の新宿/池田textured_max_lod回帰、PythonをPATHから外した条件。

公開・共有はしていません。start.cmd経由のself-checkもpassed trueを確認。ショートカットは配布EXEへの参照先と実体を確認し、クリック起動自体は未検証です。