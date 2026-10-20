# Repository Guidelines

## Project Structure & Module Organization
このリポジトリは、ゲーム実況向けの字幕生成パイプラインを育てる前提で整理します。現状は空に近いため、追加するコードは次の構成を基本にしてください。

- `src/` : 本体コード。例: 音声抽出、文字起こし、話者ラベル付け、`ASS` 生成
- `tests/` : `src/` に対応する自動テスト
- `assets/` : サンプル音声、字幕テンプレ、検証用素材
- `docs/` : ワークフロー、字幕ルール、設計メモ

例: `src/subtitles/render_ass.py` を作ったら、`tests/subtitles/test_render_ass.py` を追加します。

## Build, Test, and Development Commands

### Linux CI・クラウド開発

LinuxのPython・GUI・テストは必ず `bash scripts/linux_dev.sh <command>` で実行します。
セットアップは `bash runtime/linux-dev/cloud-install.sh`、通常CIと同じ検証は
`bash scripts/linux_dev.sh quality` と `bash scripts/linux_dev.sh test` です。
ホストの `.venv` や旧 `.cloud-setup/env.sh`、Windows用lockから独自にLinux環境を作らないでください。
依存を変更する場合は共有lockを同じPRで更新します。手順は
[`runtime/linux-dev/README.md`](runtime/linux-dev/README.md) にあります。
Python 3.10を使用します。作業時は次のコマンドを基本にしてください。

- `git status` : 作業ツリーの確認
- `git diff` : 変更内容の確認
- `bash scripts/linux_dev.sh test` : Linuxのテスト全体とGUI起動確認
- `bash scripts/linux_dev.sh python -m src.<module>` : 共通環境でのモジュール実行

FFmpegやWhisperXを含む依存更新も、手順を `README` または `docs/` に固定コマンドとして残してください。

## Coding Style & Naming Conventions
Python を使う場合は 4 スペースインデント、タブは禁止です。命名は次に統一します。

- ファイル名・モジュール名: `snake_case`
- クラス名: `PascalCase`
- 関数名・変数名: `snake_case`
- 定数: `UPPER_SNAKE_CASE`

整形ツール未導入なら、将来的に `black` と `ruff` の採用を推奨します。字幕スタイル名や JSON キーも、省略しすぎず意味が分かる名前を使ってください。

## Testing Guidelines
テストは `tests/` 配下に置き、`test_*.py` 形式で命名します。字幕系では特に次を優先して検証します。

- タイムスタンプ変換
- 改行ルール
- 話者ごとの色・スタイル割り当て
- `ASS` 出力の整合性

新機能を追加する場合は、実装と同じ変更でテストも追加してください。

## Commit & Pull Request Guidelines
まだコミット履歴がないため、最初から短く明確な命令形で統一してください。以降、コミットメッセージと Pull Request タイトルは日本語で記述してください。

- `ASS 字幕レンダラーを追加`
- `話者ごとの色割り当てを実装`
- `タイムスタンプ整形のテストを追加`

Pull Request には、変更内容、目的、確認手順、見た目に影響する場合は字幕サンプルやスクリーンショットを含めてください。

## コミュニケーション言語
本リポジトリでは、以下を日本語で記載します。

- Pull Request のタイトルと説明文
- Pull Request / Issue へのコメント、レビューコメント

## Security & Configuration Tips
API キー、認証情報、生成済みの大容量動画はコミットしないでください。ローカル設定は `.env` などの ignore 対象に置き、必要な環境変数や外部ツール依存は `docs/` か `README` に明記します。
