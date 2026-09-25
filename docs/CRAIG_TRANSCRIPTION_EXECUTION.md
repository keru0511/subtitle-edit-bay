# Craig音声の文字起こし実行

`src/craig_transcription_execution.py` は、話者別音声と共通の認識処理をつなぎます。`CraigTranscriptionHint` のプロンプト、用語、文脈フィンガープリントを `transcribe_audio_with_cache()` に渡します。ヒントがない場合も実行設定と入力音声のキャッシュ検証は有効です。

`transcribe_craig_audio_batch_with_cache()` は入力順に処理し、音声から出力JSONへの対応と、キャッシュ利用の有無を返します。ヒントは元パス、絶対パス、ファイル名の順で解決し、見つからない場合は共通ヒントを使います。

初回生成の調整内容と検証範囲は [初回文字起こしの認識設定](TRANSCRIPTION_ACCURACY.md) を参照してください。
