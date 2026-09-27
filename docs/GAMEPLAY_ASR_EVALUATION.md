# ゲーム実況音声の文字起こし評価

実際の実況で初回認識を調整する際は、音声を聞いて確定した発言と発話区間を基準にします。字幕から推測した文章やWhisperXの出力を正解文に流用しません。特に無音・ゲーム音だけの箇所に生じる語の挿入、実際の発言の脱落、発話区間からの時刻逸脱を別々に確認します。

## 評価素材

ゲーム音・BGM、静かな発話、叫び、自然な反復、短い相づちを含む複数の場面を選びます。80秒程度の一場面だけでは判定せず、各条件から複数の場面を採り、誤生成が起きた区間と起きなかった区間の両方を含めます。各場面は短く切り出し、元音声と元動画の位置を記録します。同時発話は現在の文字列比較で順序を一意に決められないため、自動合否に入れる前に別の音声トラックで評価できるか確認します。

個人の録画や再配布条件を確認していない素材はリポジトリに追加しません。以下のマニフェストと出力は `out/` などのGit管理外に置きます。レポートには音声のパスと正解文が含まれるため、公開前に内容を確認してください。

```json
{
  "schema_version": 1,
  "sample_rate": 16000,
  "duration": 40,
  "clips": [
    {
      "id": "gameplay-scene-1",
      "audio": "/absolute/path/to/original.mp4",
      "sha256": "元ファイルのSHA-256",
      "source_start_seconds": 120,
      "audio_stream": "0:a:1",
      "start": 0,
      "duration": 40
    }
  ],
  "speech_windows": [
    {"start": 2.1, "end": 4.5, "text": "音声を聞いて確定した発言"},
    {"start": 10.0, "end": 11.8, "text": "本当に繰り返した語も残す"}
  ],
  "limits": {
    "max_cer": 0.25,
    "max_cer_regression": 0,
    "timing_tolerance_seconds": 0.4,
    "max_outside_speech_characters": 0,
    "max_untimed_characters": 0,
    "max_timing_window_errors": 0
  }
}
```

`sha256` は元ファイル全体に対する値です。`source_start_seconds` は元動画内での切り出し開始位置、`start` は評価用音声内での配置位置です。`speech_windows` の時刻は評価用音声の先頭を0秒とし、選択した音声トラックで聞こえる発話を漏れなく時刻順に記載します。その外側は無音またはゲーム音のみの負例になります。発話の相づちや実際の反復を正解文から取り除かないでください。評価の上限値は設定を比較する前に決め、結果に合わせて変更しません。`audio_stream` と `source_start_seconds` を省略すると、既存の短い音声ファイルをそのまま使用します。

## 実行

既存の認識JSONがある場合、モデルを起動せずに採点できます。この経路は元音声の存在・ハッシュや認識時のモデル設定を検証しないため、比較結果の根拠には使わず、誤り箇所の探索に使います。

```sh
python scripts/benchmark_transcription.py \
  --manifest out/gameplay/manifest.json \
  --score-json out/gameplay/transcript.json \
  --output out/gameplay/scored
```

初回認識を比較する場合は、比較対象と変更後のcheckoutを指定します。既定は既存CIと同じ `large-v3 / CPU int8` です。実運用のCUDA環境では `--device cuda --compute-type float16` を追加します。両版は同じ音声・モデル・実行条件で比較し、一度に変更する認識設定は一種類に絞ります。

```sh
python scripts/benchmark_transcription.py \
  --manifest out/gameplay/manifest.json \
  --baseline-root /absolute/path/to/baseline \
  --candidate-root /absolute/path/to/candidate \
  --device cuda --compute-type float16 \
  --output out/gameplay/comparison
```

候補はVAD閾値、音声区切りの長さ、探索幅と反復設定の順に、一種類ずつ比較します。評価結果はCERだけで決めず、`insertions`、`deletions`、`outside_speech_characters`、`timing_window_errors` と実際の音声を照らし合わせます。反復ペナルティだけで発言していない語を消せても、自然な繰り返しが脱落するなら採用しません。朗読4件の短い実認識CIは高速な回帰検査として維持し、実況素材を使った長い探索を通常のPRごとに追加しません。

社畜部の実況動画で実モデルを動かした初回の試行と、既定設定を変更しなかった理由は [実況音声による初回認識の試行](GAMEPLAY_ASR_TRIAL_2026-09-26.md) に記録します。
