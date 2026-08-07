# 設定ガイド

## 読み込み規則

CLIの標準設定は `assets/runtime_config.json` です。各コマンドは `shared` を読み、その後に同名セクションを上書きします。

GUIは `.gui/runtime_config.json` をローカル設定として使います。`setup.bat` はこのファイルが存在しない場合だけ作成し、CUDAを利用できないPCではCPUと `libx264` を選びます。NVIDIA GPUではPyTorch 2.8.0 CUDA 12.8版を導入し、既存設定がCUDAなのにPyTorchがCPU版の場合はGUIが文字起こしを開始せず修復を案内します。GUIの `設定を保存`、`文字起こしを開始`、`字幕を焼き付ける` で更新され、Git管理されません。素材パスと編集字幕はここへ保存せず、出力先の `*.subtitle-project.json` へ保存します。

~~~text
shared
  ↓ コマンド別セクションで上書き
craig_pipeline / batch / pipeline
  ↓ CLIで上書き
実行値
~~~

CLIの優先順位は `CLI > コマンド別設定 > shared > コード既定値` です。GUI設定は `.gui/runtime_config.json` 内の `shared` と `craig_pipeline` を使用します。

## 編集プロジェクトと設定の責務

`*.subtitle-project.json` はruntime configではなく、動画1本に対する編集データです。`schema_version=1` を持ち、字幕ID、本文、元動画基準の開始・終了時刻、話者、音量から算出したサイズ倍率、手動上書きフラグ、表示用波形、同期結果を保存します。

| 保存先 | 保存するもの | 再生成時の扱い |
|---|---|---|
| `transcripts/*.json` | WhisperXのraw認識結果 | 再文字起こししない限り不変 |
| `*.craig.merged.json` | 自動整形直後の字幕 | プロジェクト作成時の入力記録 |
| `*.subtitle-project.json` | ユーザー編集の正本 | ASS・動画の唯一の編集入力 |
| `*.edited.ass` | ASS生成物 | `ASSを更新` / renderで上書き可能 |
| `*.edited.subtitled.mp4` | 完成動画 | renderで上書き可能 |

プロジェクトの `subtitle_settings.font_size` は全字幕の基準値、各segmentの `subtitle_font_scale` は字幕単位の倍率です。文字起こし時は話者内の相対音量から自動設定され、エディターで変更すると `manual_font_scale=true` になります。話者単位の文字サイズ設定は使用しません。各segmentの `subtitle_font_family` には字幕単位で選択したフォント名を保存し、未指定なら既定フォントを使用します。

`audio_mix.channels` は動画内トラックと個別音声のチャンネルを保存します。各チャンネルは `enabled`、`volume_percent`（0〜200）、`muted`、`solo` を持ちます。`audio_mix.customized=false` の既存・既定プロジェクトは従来の `render_settings.output_audio_track` をそのまま使用し、ミキサーを操作すると `customized=true` になってFFmpegのaudio filter graphでAAC合成します。個別音声の時刻には `transcription.offset_seconds` を使用します。

編集保存は一時ファイルへ書いてから置換するため、保存途中の終了で正本を部分書き込みしません。GUIは変更から700ms後にバックグラウンド保存を開始します。保存中の追加編集は世代番号でまとめ、古い保存完了で新しい編集を保存済みにしない「latest version wins」の動作です。手動保存、ASS更新、動画書き出し、GUI終了時は保留中の保存を待ってから最新状態を同期保存します。Undo/Redo履歴は実行中のGUIセッション内で最大100操作保持します。

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
| `subtitle_font_size` | `50` | 全話者共通の字幕基準文字サイズ。GUIでは50pxを100%として10〜900%で指定 |
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

`h264_nvenc` はNVIDIA NVENC対応FFmpegが必要です。CUDAでWhisperXが動くかどうかとは別の能力なので、GUIは書き出し直前にNVENCで1フレームを試験エンコードします。成功時は `h264_nvenc`、失敗時は `libx264` を自動選択し、codecの選択欄は表示しません。CLIで明示した場合は指定値をそのまま使用します。

NVENCは `VBR + CQ 18 + High profile + spatial/temporal AQ` を標準使用します。ビットレートを低く固定しないため、動きの多いゲーム画面では必要な分だけビットレートが上がり、輪郭のジャギーやブロックノイズを抑えます。

~~~powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
ffmpeg -hide_banner -encoders | Select-String nvenc
~~~

## 話者色

GUIの素材画面または字幕編集画面で話者の色を押すと、カラーピッカーから字幕色を変更できます。選択色は話者名と音声ファイル名に紐づけて `assets/speaker_colors.json` へ保存され、次回起動時も維持されます。個人用の `speaker_colors.json` はGit管理対象外です。手動編集する場合は `assets/speaker_colors.example.json` をコピーし、`files` と `speakers` の2種類で指定できます。

~~~powershell
Copy-Item .\assets\speaker_colors.example.json .\assets\speaker_colors.json
~~~
- `files`: `1-speaker-a.flac` のような音声ファイル名で指定
- `speakers`: `speaker-a` のような解析後の話者名で指定
- `aliases`: 拡張子違いや短縮名を同じ色へ割り当て

指定色は字幕文字本体へ使われ、可読性を保つため黒い枠線を付けます。色は字幕単位ではなく話者単位で固定されます。

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
