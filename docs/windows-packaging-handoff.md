# 実装・検証担当AIへ：v0.10 Windowsパッケージング実機検証

この版から利用者にvenv/pip/unittestを要求しない。実行ファイル配布が本線。

## 開発者のPCで得た失敗ログ
1. 依存関係(Pillow 12.3.0 / pyproj 3.8.0)は正常インストール。
2. `test_appearance` の `.read_text()` がWindows cp932でUTF-8を読むためUnicodeDecodeError。`encoding='utf-8'` で修正。
3. `test_pipeline` のUnix shebang fake BlenderをWindowsで実行し WinError 193。 `.cmd` ランチャーのモックへ修正。

## 検証してほしい最小順序（ソース配布EXEのビルド時）
- `build_windows.bat`（開発用の独立 `.build-venv` を利用）かGitHub Actions Windows workflowを実行。エラーがあればビルドログ提出。
- 展開済みの `PLATEAU-Blender-Bridge.exe --self-check <JSON>` を実行してJSONのpassed=trueを確認（GUIは表示されない）。
- EXEを直接ダブルクリック。環境スキャンでBlenderを検出。
- ネット不要の「Blender自己診断」で保存・再読込・UV・画像埋め込みを確認。
- その後、必要なら小範囲の自動取得を検証。nusamai CLIはBlender GUIとは別物。

## 未確認
PyInstallerの実Windowsビルドは設計担当AIのLinux環境では行っていない。Windows .cmdのクォート挙動、Tk/Pillow/pyproj同梱、bpy専用スクリプト配置はCIと実機で確認。
