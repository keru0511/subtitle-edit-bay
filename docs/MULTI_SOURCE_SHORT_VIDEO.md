# 複数素材の短尺編集

単一素材プロジェクトを壊さず、複数の動画を同じ短尺タイムラインへ配置するためのデータ境界を定義する。

## Source IDと再リンク

各素材は `source_id`、fingerprint、path、メディアメタデータ、missing状態を持つ。既存の単一素材プロジェクトは読み込み時に1件のsourceへ移行し、旧 `video_path` などの項目は互換性のため保持する。移行はメモリ上のコピーに対して行い、元のプロジェクトを暗黙に書き換えない。

素材が見つからない場合はmissingとして明示し、ユーザーが再リンクしたときもsource_idと既存のclip参照を保持する。参照中のsource削除は、clip削除を明示しない限り拒否する。

## 時刻と正規化

clipはsource timeとtimeline timeを別々に持つ。異なるfps、解像度、音声サンプルレートは、共通fps、解像度、48kHzへ正規化する計画を生成してから結合する。字幕、speaker style、transition、BGMはsourceをまたぐtimeline側のデータとして扱う。

## 候補とフィルタ

候補はsource_idを保持したまま統合し、上位候補を同一sourceだけで埋めない。FFmpeg処理は入力ごとの正規化とclipのsource/timeline時刻を別行のfilter scriptへ出力するため、素材数や長さで単一コマンドが過剰に長くならない。生成先は呼び出し側で明示的な上書き確認を行う。

