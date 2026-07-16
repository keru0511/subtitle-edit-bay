# 設定ガイド

## 読み込み規則

CLIの標準設定は `assets/runtime_config.json` です。各コマンドは `shared` を読み、その後に同名セクションを上書きします。

GUIは `.gui/runtime_config.json` をローカル設定として使います。`setup.bat` はこのファイルが存在しない場合だけ作成し、CUDAを利用できないPCではCPUと `libx264` を選びます。GUIの `SAVE PRESET` または `START RENDER` で更新され、Git管理されません。

~~~text
shared
  ↓ コマンド別セクションで上書き
craig_pipeline / batch / pipeline
  ↓ CLIで上書き
実行値
~~~

CLIの優先順位は `CLI > コマンド別設定 > shared > コード既定値` です。GUI設定は `.gui/runtime_config.json` 内の `shared` と `craig_pipeline` を使用します。

## shared

| キー | 現在値 | 意味 |
|---|---:|---|
| `model` | `large-v3` | WhisperXモデル |
| `device` | `cuda` | `cuda` または `cpu` |
| `compute_type` | `float16` | CUDAは通常`float16`、CPUは通常`int8` |
| `language` | `ja` | 認識言語 |
| `vad_onset` | `0.35` | 発話開始のVAD閾値 |
| `vad_offset` | `0.2` | 発話終了のVAD閾値 |
| `width`, `height` | `1920`, `1080` | ASSの基準解像度 |
| `nvenc_cq` | `18` | NVENC固定品質。小さいほど高画質・大容量 |
| `x264_crf` | `18` | libx264固定品質。小さいほど高画質・大容量 |
| `subtitle_font_size` | `50` | 全話者共通の字幕基準文字サイズ |
| `subtitle_max_gap_seconds` | `0.1` | 単語間の無音をページ境界候補にする秒数 |
| `subtitle_end_padding_seconds` | `0.08` | 最後の単語後に残す字幕余白 |
| `subtitle_min_duration_seconds` | `0.35` | 極端に短い字幕の表示下限 |

字幕分割は無音だけで機械的に切りません。WhisperX時刻の不自然な長さを補正し、BudouXの文節境界を優先し、必要な場合だけJanomeの単語境界を補助に使います。

### 字幕が細かすぎる

`subtitle_max_gap_seconds` を大きくすると、短い無音では分割しにくくなります。例: `0.1` から `0.2`。

### 字幕が無音中に残る

`subtitle_end_padding_seconds` を小さくします。現在値 `0.08` は最後の単語から80msの余白です。

### 字幕が一瞬で消える

`subtitle_min_duration_seconds` を大きくします。ただし次字幕の開始時刻や元セグメント終端を超えられない場合があります。

## craig_pipeline

| キー | 現在値 | 意味 |
|---|---:|---|
| `input_root` | `video_import` | 対象名を探すルート |
| `export_root` | `video_export` | 対象名ごとの出力ルート |
| `reference_track` | `null` | `null`なら動画音声トラックを自動照合 |
| `reference_audio` | `null` | `null`なら最初の`1-*`音声 |
| `alignment_sample_rate` | `120` | 同期比較用の低レートサンプル数/秒 |
| `alignment_offset_adjustment` | `0.0` | 自動検出した同期オフセットへ加える補正秒数 |
| `video_codec` | `h264_nvenc` | 字幕焼き込みの動画codec |
| `audio_codec` | `copy` | フィルタなしの場合の音声codec |
| `output_audio_track` | `0:a:0` | 完成動画へ入れる動画側の音声トラック |
| `nvenc_preset` | `p5` | NVENCの速度・品質プリセット |
| `nvenc_cq` | `18` | NVENC固定品質。通常は18、容量優先なら19〜21 |
| `x264_crf` | `18` | libx264使用時の固定品質 |
| `audio_normalize` | `true` | 最終音声をFFmpeg `loudnorm` で正規化 |
| `audio_target_lufs` | `-16.0` | 目標Integrated Loudness。値を上げると音量も上がる |
| `audio_loudness_range` | `11.0` | 目標Loudness Range |
| `audio_true_peak_db` | `-1.5` | 目標True Peak（dBTP） |
| `cut_no_speech` | `false` | 全Craig話者が無音の区間をカット |
| `no_speech_min_seconds` | `1.2` | カット対象にする最短の無発話秒数 |
| `speech_padding_seconds` | `0.25` | 発話の前後に残す秒数 |
| `speech_threshold_db` | `-40dB` | 各Craig音声を無音とみなす閾値 |
| `speech_min_clip_seconds` | `0.25` | 残す動画断片の最短秒数 |
| `subtitle_volume_scale_percent` | `20.0` | 話者内の相対音量による文字サイズ変化幅。20なら約80〜120% |
| `postprocess_workers` | `4` | GPU文字起こし中に使うCPU後処理ワーカー |
| `skip_existing_transcripts` | `true` | 既存JSONを再利用 |

Craig音声は話者分離済みなので、通常はHugging FaceトークンもWhisperX diarizationも不要です。

字幕サイズは話者名では変えません。`subtitle_font_size` を全話者の基準とし、各話者自身の発話音量の中央値との差から `subtitle_volume_scale_percent` の範囲で拡大・縮小します。`0` にすると音量連動を無効化し、すべて基準サイズになります。文字サイズを変更した場合は、画面からはみ出しにくいよう1行の文字数も合わせて調整されます。

`audio_normalize=true` または `cut_no_speech=true` では音声フィルタが必要なため、最終音声は `audio_codec=copy` ではなくAACになります。`cut_no_speech` はゲーム音ではなく全Craig話者音声の和集合を使うため、ゲーム音が鳴り続けていても会話がない区間を判定できます。判定結果は `*.craig.no_speech.json` で確認できます。

## batch

| キー | 意味 |
|---|---|
| `input_dir`, `output_dir` | 一括入出力ディレクトリ |
| `audio_track` | 処理対象の動画音声トラック配列 |
| `diarize_track` | WhisperX話者分離を行うトラック配列 |
| `op_file`, `ed_file` | 任意のOP/ED動画 |
| `video_codec`, `audio_codec` | FFmpeg codec |
| `output_audio_track` | 完成動画へ入れる本編音声。標準は `0:a:0` |
| `audio_normalize` | OP/本編/EDの音量正規化 |

OP/本編/EDは本編と同じフレームレート・タイムベース・48kHzステレオへ正規化してから連結します。音声のないOP/EDには無音トラックを補います。

`diarize_track` を設定する場合のみHugging Faceトークンが必要です。トークンは設定ファイルやCLI引数へ保存せず、`HF_TOKEN` 環境変数から渡してください。

## GPUとcodec

| 用途 | GPU設定 | CPU設定 |
|---|---|---|
| WhisperX | `device=cuda`, `compute_type=float16` | `device=cpu`, `compute_type=int8` |
| FFmpeg焼き込み | `video_codec=h264_nvenc` | `video_codec=libx264` |

`h264_nvenc` はNVIDIA NVENC対応FFmpegが必要です。CUDAでWhisperXが動いても、FFmpegにNVENCがなければ動画エンコードは失敗します。

NVENCは `VBR + CQ 18 + High profile + spatial/temporal AQ` を標準使用します。ビットレートを低く固定しないため、動きの多いゲーム画面では必要な分だけビットレートが上がり、輪郭のジャギーやブロックノイズを抑えます。

~~~powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
ffmpeg -hide_banner -encoders | Select-String nvenc
~~~

## 話者色

`assets/speaker_colors.example.json` を `assets/speaker_colors.json` へコピーして使用します。個人用の `speaker_colors.json` はGit管理対象外です。設定は `files` と `speakers` の2種類を持ちます。

~~~powershell
Copy-Item .\assets\speaker_colors.example.json .\assets\speaker_colors.json
~~~
- `files`: `1-speaker-a.flac` のような音声ファイル名で指定
- `speakers`: `speaker-a` のような解析後の話者名で指定
- `aliases`: 拡張子違いや短縮名を同じ色へ割り当て

字幕文字は常に白で、指定色はASSの枠線に使われます。

~~~json
{
  "files": {
    "1-speaker-a.aac": {
      "color": "#FFD966",
      "aliases": ["1-speaker-a.flac"]
    }
  },
  "speakers": {
    "speaker-a": {
      "color": "#FFD966",
      "aliases": ["speaker-a"]
    }
  }
}
~~~

一時的にCLIで上書きする場合:

~~~powershell
.\.venv\Scripts\python.exe -m src.craig_pipeline game_session_01 --track-color "craig:speaker-a=#FFD966" --run
~~~

色変更だけなら既存transcript JSONを再利用できます。
