# Clips Kittyとの実装比較（2026-10-20）

本書はSubtitle Edit BayのUI整備（#440）と、ゲーム実況の長尺字幕編集を目的に、
[Clips Kitty](https://github.com/ColinGPT9/clips-studio)の公開実装を確認した記録。
このPRにはClips Kitty由来のコードを一切含めない。

## ライセンス境界

- Subtitle Edit Bay: [MIT](../LICENSE)
- Clips Kitty: [AGPL-3.0](https://github.com/ColinGPT9/clips-studio/blob/main/LICENSE)

AGPLの実装ファイルをそのままMITプロジェクトへコピーしてMITのみで配布してはならない。
コードの流用が必要になった場合は著作権者の別ライセンス許諾か、
配布形態を含めたライセンス検討を先に行う。
今回は公開仕様から必要な挙動を把握し、Subtitle Edit Bayの既存データモデル上に独立実装する。

## 機能の重複と移行判断

| 機能 | Clips Kittyの確認先 | Subtitle Edit Bayの現状 | 判断 |
| --- | --- | --- | --- |
| SRT / WebVTT書き出し | `multilingual/subtitles.py` | `src/subtitle_export.py`でSRT/VTT/CSVに対応 | 移植不要 |
| 字幕の編集・時刻保持 | `video_editor/timeline.py`, `video_editor/captions.py` | `src/video_timeline.py`, `src/subtitle_project.py` | 既存の素材時刻を維持 |
| 無音部分の削除 | `longform/downtime.py` | `src/silence_cut.py`で音声無音カット対応 | 静止画/待機検出は将来の別要件 |
| 音量・ミュート・フェード | `video_editor/audio.py` | `src/audio_mixer.py`, `src/realtime_audio_mixer.py` | 全面移植不要 |
| 自然言語による編集提案 | `analysis/clip_edit.py` | `src/codex_edit_proposal.py`, `src/gui_codex_state.py` | 独立した既存契約を継続 |
| 外部の話者別音声と同期 | 標準動画入力に外部音声指定なし | `src/craig_pipeline.py`などが対応 | Subtitle Edit Bayを正本とする |
| 長尺からの見どころ抽出 | `longform/highlight_select.py` | 独立した見どころ候補機能あり | 現在の優先対象外 |
| 出力用フレーム合成 | `longform/assemble.py` | `src/sequence_render.py`, `src/assemble_video.py` | 既存パスで検証 |
| 再レンダリング時の編集状態維持 | `render_opts`の保持 | `*.subtitle-project.json`が編集の正本 | 既存方式を維持 |

## 今回採用する設計上の観点

- **時刻は正本の素材時刻と完成シーケンスの出力時刻を区別する。**
  Clips Kittyの編集処理にも原区間と完成時刻の区別があるが、
  Subtitle Edit Bayは既に非破壊カットとシーケンスの時間対応を持つ。
- **1本の動画素材にある字幕を別素材へ誤って流用しない。**
  クリップ別の字幕表示フィルターは、主動画の字幕のみを素材時刻の重なりで抽出する。
  字幕のない追加動画は空表示とする。
- **UIフィルターは表示状態であり、プロジェクトの正本ではない。**
  字幕ID、タイミング、話者、Codex提案対象、ASS/SRT/VTT出力に影響させない。

## 今後の候補（別Issueが必要）

1. 追加した各動画素材と外部音声の対応関係をプロジェクトに定義し、素材ごとの文字起こしを行う。
2. その結果を完成シーケンス時間に配置し、クリップごとの字幕確認と書き出しを整合させる。
3. 独立BGM/SEトラックをデータモデル・タイムライン表示・保存・書き出しで一貫して扱う。

いずれもUIだけを追加せず、素材・タイムライン・プレビュー・レンダー間の契約と
自動テストを揃えてから公開する。
