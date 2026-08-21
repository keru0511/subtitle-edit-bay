# YouTube投稿用パッケージ

投稿前の確認を短縮するため、タイトル、説明、固定コメント、キーワード、章、サムネイル候補をローカルのフォルダへまとめて出力する。YouTube API、アカウント認証、アップロードは行わない。

## 入力と出力

`youtube_text` がプロジェクトに存在する場合は、そのタイトル、説明、固定コメント、キーワードを再利用する。未設定の場合はプロジェクトの基本項目を使う。出力には以下を含める。

- `package.json`: schema、revision、platform rules、章、候補、設定fingerprint
- `title.txt`、`description.txt`、`pinned-comment.txt`、`keywords.txt`
- `chapters.txt`: YouTubeへ手動貼り付けできる `HH:MM:SS タイトル` 形式
- `manifest.json`: 出力ファイル、revision、アップロード未実施の記録

## 章とサムネイル

章は開始時刻、タイトル、IDを持ち、同一時刻・重複ID・動画長を超える時刻を拒否する。追加、名称変更、時刻変更、削除は出力前に検証する。

サムネイル候補はハイライトスコア、明るさ、重複距離でローカル順位付けする。FFmpegの1フレーム抽出とcontact sheetのコマンドを表示し、ユーザーが確認してから実行する。

## stale判定と安全性

プロジェクトのrevisionまたは設定fingerprintが変わった場合、既存パッケージはstaleと判定する。既存の出力フォルダは明示的にoverwriteを指定しない限り変更せず、一時フォルダへ生成してから配置する。

