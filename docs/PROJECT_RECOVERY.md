# プロジェクトスナップショットと復旧

編集データを一括変更、Codex提案適用、移行、復元の前にスナップショットを作成する。スナップショットはプロジェクトJSONだけを持ち、動画・音声・画像などの素材本体、素材パス、APIキー、token、password、secretを保存しない。

## 保存形式

各ファイルは `schema_version`、snapshot id、revision、reason、created_at、project、checksumを持つ。checksumはサニタイズ後のJSONをcanonical encodingしてSHA-256で計算する。読み込み時にchecksumが一致しないファイルは破損としてスキップし、復元対象にしない。

## 復元と差分

一覧、checksum検証、JSON差分、復元を提供する。復元先が既存の場合は明示的なoverwriteが必要で、復元前には現在のプロジェクトを `pre-restore` として保存する。元のスナップショットは変更しない。

## 保持とクラッシュ復旧

保持数、保持日数、容量を設定できる。pinnedスナップショットは自動削除しない。クラッシュ復旧journalは、現在のrevisionより新しいかつchecksumが有効な状態だけを候補として返し、復元後にjournalを消す。古い状態や不明なrevisionは自動適用しない。

