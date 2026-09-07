# リリースガイド

通常リリースで人が行う操作は、`VERSION` だけを変更するPull Requestの作成、内容確認、マージです。公開タグには厳密な `vX.Y.Z` を使います。公開済みのタグを削除・付け替えしないでください。

## マージ前のRelease readiness

`Release readiness` Workflowは、`main` 向けのすべてのPull RequestとMerge queueの候補で動きます。`paths` フィルターは使いません。

- `VERSION` の実際の値が変わる場合はリリースPRです。変更ファイルが `VERSION` だけであること、値が厳密な `vX.Y.Z` で増加していること、同じタグやGitHub Releaseがないことを検証します。
- リリースWorkflow、インストーラー、検証スクリプトを変更するPRは、`VERSION` を変えなくても基盤変更として準備処理を実行します。公開要求にはしません。
- それ以外は通常PRであることを明示して成功します。通常のCIは別途必要です。
- 不正なVERSION、判定失敗、テスト・ビルド・成果物検証・インストール・起動確認の失敗、キャンセル、予期しないskipは失敗として集約されます。

リリースPRと基盤変更PRでは、PRブランチ単独ではなくGitHubが作成した `main` との仮マージSHAを `source_sha` として、次を実行します。

1. VERSIONと入力バージョンの一致を検証する
2. FFmpeg／ffprobe、Qt offscreen環境を明示して、分類済みテスト群を別プロセスで実行する
3. 正式バージョンを埋め込んだWindowsインストーラーを作る
4. SHA-256、manifest、対象SHA、準備記録を検証する
5. そのインストーラーをサイレントインストールし、配置内容、VERSION、GUI起動を確認する

PRの準備処理は `contents: read` だけで動き、タグやReleaseを作りません。`pull_request_target` や公開用資格情報も使いません。

### v0.4.8で検出したGUIテスト失敗

失敗したRelease runは911件を1つのPythonプロセスで一括実行し、通常CIは分類済みグループを別プロセスで実行していました。`start.call_args` が `None` になった2件はReleaseで同じ順序のとき再現し、通常CI方式では成功したため、アプリ処理の削除やテストskipではなく、実行単位を通常CIと共通の `run_ci_tests.py` に統一しました。さらにRelease環境にもFFmpegとffprobeを明示的に導入し、Qtのoffscreen／software環境変数を通常CIと揃えています。

## マージ後の公開

リリースPRをマージすると `release-request.yml` が、そのpushイベントの実際のマージSHAとVERSIONを固定します。待機中に `main` が進んでも対象を最新HEADへ差し替えません。

公開処理は `release-prepare.yml` をもう一度呼び、テスト、正式版インストーラー構築、成果物検証、インストール・起動確認がすべて成功した後にだけ、次を行います。

1. 対象SHAへ注釈付きタグを作る。既存タグなら同じSHAを指す場合だけ再利用する
2. 準備済みartifactを再検証する
3. GitHub Releaseを作り、インストーラー、SHA-256、manifest、`release-preparation.json` を添付する

既存Releaseの再実行では公開済みassetをダウンロードしてSHAと対象コミットを照合します。同一なら何も上書きせず成功し、異なる場合は停止します。`--clobber` は使いません。

障害復旧の手動実行では、`Release from merged version` に次の2値を明示します。

- `source_sha`: 既に `main` に含まれる、VERSIONだけを変更したコミットの完全な40桁SHA
- `release_version`: そのコミットのVERSIONと同じ `vX.Y.Z`

この経路も共通準備処理を迂回しません。直接タグpushを公開入口にするWorkflowはありません。

## 必須チェック設定

Workflow追加だけではマージを制限できません。`main` のRulesetまたはBranch protectionで、既存の必須CIに加えてジョブ `Release readiness` を必須にし、次を設定してください。

- Pull Requestを必須にする
- 必須チェック成功を必須にする
- マージ前にブランチを最新化する（strict / require branches to be up to date）
- 管理者を含む通常操作に適用し、意図しないbypass actorを登録しない
- Merge queueを使う場合は `merge_group` のチェックも必須にする

2026-09-07時点の確認では、Repository Rulesets APIの応答は空でした。Branch protectionはGitHub AppにAdministration権限がなく確認・変更できなかったため、上記設定はこのPRからは未適用です。設定後、通常PRとVERSION-only PRの両方で `Release readiness` がrequiredとして表示され、古い成功結果だけではマージできないことを確認してください。

## リリース後の確認

1. `Release from merged version` と、その中の共通準備・公開ジョブが成功していることを確認する
2. 対象タグがマージSHAを指すことを確認する
3. Assetsに `SubtitleEditBay-Setup.exe`、同名の `.sha256` と `.manifest.json`、`release-preparation.json` があることを確認する
4. [直接ダウンロードURL](https://github.com/keru0511/subtitle-edit-bay/releases/latest/download/SubtitleEditBay-Setup.exe)から取得できることを確認する

公開時のGitHub通信障害などは再実行で復旧します。ソース修正が必要なら既存タグは動かさず、パッチ番号を上げた新しいVERSION-only PRを作成します。
