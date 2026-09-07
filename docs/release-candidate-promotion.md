# リリース候補昇格契約

## 目的と境界

マージ前に `Release readiness` がbuildし、独立runnerでinstall/startまで確認したWindows installerの同じbyte列を正式Releaseへ昇格する。公開側は候補の検索、承認済みマージとの対応確認、checksum確認、タグとReleaseの作成だけを担当し、テスト・依存解決・build・署名・installer加工を行わない。

候補生成側の正本は `release-prepare.yml`、成果物内契約は `release_contract.py` とする。公開側は成果物内の自己申告だけでなくGitHub APIのrun、job、PR、commit、artifact情報を照合する。

## 一意な候補の条件

`scripts/release_candidate.py` は次のすべてを満たす候補だけを選ぶ。

- 実際の公開commitに一意に対応し、`main` へマージされた同一repositoryのPRである
- PRの変更ファイルが `VERSION` の1件だけで、最終head/baseが確定している
- 最終headに対応する最新の完了済み `release-readiness.yml` のpull_request runである
- 最新runと同じattemptに属する分類、Linuxテスト、Windows build、install/start、準備集約、readiness集約の全ジョブが成功している
- versionと候補SHAを含むartifactが1件だけ存在し、artifact ID、GitHub SHA-256 digest、期限を取得でき、未失効である
- 候補SHAが最終base/headを親に持つ仮マージcommitで、候補treeと公開commitのtreeが一致する

候補の `candidate_source_sha` とタグを付ける `release_commit_sha` は別に保存する。候補のmanifestや準備記録を正式マージSHAへ書き換えない。対応関係、run/attempt、artifact ID/digest、PR、両tree、installer checksumは `release-promotion.json` に記録する。

最新の該当runが失敗、キャンセル、skipの場合、古い成功runへフォールバックしない。PR更新、base/head不一致、tree不一致、fork、未知のjob構成、重複artifact、digest欠落、期限切れ、APIエラーも公開不可とする。

## 信頼境界

公開Workflowはマージ済みmain上の `job.workflow_sha` から公開ツールをcheckoutし、候補artifact内のコードを実行しない。VERSION-only PRであることと候補tree・公開treeの一致を確認するため、候補を生成したWorkflowおよびbuild定義も承認済みの公開treeと一致する。PR側へ `contents: write` や公開用資格情報は渡さない。

tree一致だけでは履歴や外部入力を使うbuild一般の同一性を証明できない。本プロジェクトのinstaller buildでcommit SHAは配布binaryの入力ではなく不変のprovenance記録にだけ使う。依存・runner・署名等をbuild入力として固定する契約を候補生成側で追加した場合、公開側にも同じフィールドの照合を追加する。未知スキーマを黙って許容しない。

## 再実行と復旧

同じ公開要求の再実行は同じ最終headに対する最新候補を選び、run IDとartifact IDを固定した後はその候補だけを使う。公開途中のAPI障害ではbuildやtestを起動しない。

- 既存タグが別commitを指す場合は停止し、削除・付け替えしない
- 公開済みReleaseは全assetが候補とbyte単位で同じ場合だけ再利用する。欠落や差異があれば停止する
- draftは既存assetを先にbyte単位で照合し、同一候補から不足分だけを追加して公開する。既存assetは上書きしない
- artifact不在・期限切れ・候補不一致では停止し、別runへ自動フォールバックしない

候補artifactと昇格判断記録の保持は14日とする。失効後は信頼済みの生成経路で明示的に再準備し、必要検証と公開承認をやり直す。

## 計測

変更前はマージ後にLinuxテスト1回、Windows build 1回、install/start 1回を再実行していた。変更後の通常公開処理はこれらを0回とし、候補検索、API照合、artifact download、checksum、タグ、Release APIだけを実行する。

実runでは候補run ID/attempt、マージから公開完了まで、候補検索・download・照合・公開の各step時間、総runner時間、`release-promotion.json` のinstaller checksumを記録する。ネットワークとqueue待ちは別に扱い、固定秒数を成功条件にしない。
