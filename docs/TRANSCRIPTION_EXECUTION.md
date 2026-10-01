# 文字起こしの実行境界

`src.transcription_execution.transcribe_audio_with_cache()` は、単一音声の初回認識とキャッシュ検証を担当します。`src.transcribe.build_whisperx_command()` が生成する共通コマンドは、`src.whisperx_runner` を起動してWhisperXのPython APIへ生成設定を渡します。設定の詳細は [初回文字起こしの認識設定](TRANSCRIPTION_ACCURACY.md) を参照してください。

辞書ヒントの有無によらず、実際の実行設定・音声ファイル情報・WhisperXバージョンからフィンガープリントを作成します。呼び出し元の `cache_fingerprint` は文脈情報として組み込みます。JSONが存在するだけでは再利用しません。旧形式・不一致・欠損メタデータは再認識の対象です。

`skip_existing=False` は強制再実行です。実行前に旧メタデータを無効化し、認識と時刻合わせに成功して出力が存在するときだけ新しいメタデータを書きます。認識・時刻合わせの途中で失敗しても、以前のJSONは置き換えません。
