# Repository Guidelines

## Project Structure & Module Organization
このリポジトリは、ゲーム実況向けの字幕生成パイプラインを育てる前提で整理します。現状は空に近いため、追加するコードは次の構成を基本にしてください。

- `src/` : 本体コード。例: 音声抽出、文字起こし、話者ラベル付け、`ASS` 生成
- `tests/` : `src/` に対応する自動テスト
- `assets/` : サンプル音声、字幕テンプレ、検証用素材
- `docs/` : ワークフロー、字幕ルール、設計メモ

例: `src/subtitles/render_ass.py` を作ったら、`tests/subtitles/test_render_ass.py` を追加します。

## Build, Test, and Development Commands
まだビルドツールは未導入です。Python ベースで進める場合は、次のような最小構成を揃えてください。

- `git status` : 作業ツリーの確認
- `git diff` : 変更内容の確認
- `pytest` : テスト全体の実行
- `python -m src.<module>` : モジュール単位のローカル実行

将来 `ffmpeg` や `WhisperX` を使う場合も、手順は `README` または `docs/` に固定コマンドとして残してください。

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
