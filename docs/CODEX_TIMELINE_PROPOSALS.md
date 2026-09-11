# Codex カット・ショート構成 Proposal

Codex から通常カットとショート構成を変更するときは、`propose_timeline_edit` Action で
`normal` または `short` を明示します。Codex はプロジェクトを直接変更せず、型付き Proposal を返します。
GUI は内容を表示し、利用者が選択した operation だけを明示的な適用操作で反映します。

## 安全境界

- context と Proposal の時刻は常に元動画基準 (`source`) です。
- context にはメディアやプロジェクトのローカルパスを含めません。
- Proposal は生成時の `base_revision` と対象構成の `base_state_revision` を保持します。
  適用時にいずれかが変わっていれば、古い Proposal として全体を拒否します。
- operation ID、通常カット ID、ショート clip の `proposal_id` を安定 ID として扱います。
- 不正な範囲、存在しない ID、重複範囲、対象外 operation は、コピー上で検証してから
  Proposal 全体を拒否します。途中までプロジェクトへ反映することはありません。
- 全カットの消去、動画の半分以上を除外する提案、全ショート clip の削除には追加確認が必要です。
- FFmpeg 実行やファイル書き換えを行わず、既存の `VideoTimeline` と `ShortVideo` 契約へ dispatch します。

Proposal 境界の責務は、信頼できない JSON の schema、project/target revision、利用者が選択した
operation ID、追加確認の検証までです。ショート clip の検索・追加・削除・並べ替え・更新、
source/segment 範囲、重複 range、ハイライト候補、目標尺の検証と state 更新は
`short_video_commands` だけが担います。GUI Slot と Codex Proposal は同じ command API を呼び、
入力 project を変更せずに全 command が成功した場合だけ結果を反映します。

## operation

`normal` は `add_cut` / `remove_range`、`restore_cut`、`restore_range`、
`update_cut_range`、`clear_cuts` を受け付けます。

`short` は `add_clip_by_range`、`remove_clip`、`move_clip`、`update_clip_range`、
`use_highlight_candidate`、`set_short_duration_target` を受け付けます。
ハイライト由来 clip には候補 ID を残すため、提案根拠を追跡できます。

`add_clip_by_range`、`use_highlight_candidate`、`update_clip_range` は、更新対象自身を除く clip と
`(source_start, source_end)` が完全一致する場合を同じ重複 range として拒否します。

## 統合境界

この実装は型付き Action 境界 (#250) 上に構成 Proposal と適用 API を追加します。
共通チャット UI は #249、ショート専用 workspace と `time_basis=source` の永続化は #275、
ハイライト候補の生成は必要に応じて #251 の結果を利用します。通常カットは #254 で導入済みの
`VideoTimeline` を利用します。
