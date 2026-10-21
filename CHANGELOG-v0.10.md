# v0.10 — Windows UX / 配布方式

- 利用者にPython環境構築・テスト実行を要求しない、PyInstaller onedir配布構成を追加。
- Windows GitHub Actionsのビルド・静的実行検査と配布用ZIP生成を追加。
- 手動OBJモードを凍結されたexeから `--manual` で再起動可能に修正。
- Windows cp932でのUnicodeDecodeErrorを修正。
- Unix shebangなモックを直接起動するWindows失敗を修正。
- 実験用セットアップを `dev_setup_windows.bat` に改称し、**テスト自動実行を廃止**。
- Python本体・Blender/nusamaiを消したわけではない。内部PythonランタイムはEXEに同梱、Blender/nusamaiは別途検出・指定。
- Windows用EXEはこの環境では生成しておらず、CIのWindows実行待ち。
