# ショート用ワークスペース

ショートは通常動画の編集モードではなく、通常動画プロジェクトを参照して作る別成果物です。通常動画の `currentEditMode` は `subtitle`、`cut`、`audio` のみに限定し、ショート画面への遷移は `currentWorkspace` で管理します。

## 状態境界

- 通常動画編集は共通プレイヤーと `editorPlayhead` を所有します。
- ショート用ワークスペースは `ShortModePreview` 内の別 `MediaPlayer`、独立した縦長プレビュー、選択クリップ、clip/range/settingsを所有します。通常動画のplayerを付け替えたり共有したりしません。
- ワークスペースを開く際に通常動画の再生位置を明示的に保存し、戻る際に復元します。ショートのプレビュー位置から通常動画のplayheadを更新しません。
- 字幕と見どころ候補はクリップ作成の補助情報です。ASR / WhisperXによる文字起こし済みであることは開始条件ではなく、字幕0件でも元動画の範囲を直接追加できます。

画面遷移の正本は `currentWorkspace` です。通常動画側は `normal-video`、ショート側は `short-artifact` とし、`currentEditMode` や `activeOverlay` に `short` を追加しません。`shortMode` bool、通常動画のモードレール内にあるshort項目、通常プレイヤーを付け替える実装は廃止済みです。

## 時刻基準

`short_video.time_basis` の正本は `source` です。各clipの `start` / `end` は元ソース動画上の秒を表し、通常動画の非破壊カット後のoutput timelineとは混在させません。

通常動画のoutput timelineを将来ショート素材として選べるようにする場合は、#254のsource/output mappingで範囲を明示変換してから保存します。`time_basis: output` を暗黙に読み替えることはせず、現時点のloaderと書き出しpreflightは拒否します。

## ショートシーケンスと完成尺

ショートの操作UIは、元ソース上のclip範囲と、完成動画上のoutput timelineを分けて扱います。

- clipの `start` / `end` と選択中素材の再生位置はsource-timeです。
- transport、完成尺メーター、タイムラインclick、書き出し進捗のdurationはoutput-timeです。
- `src.short_video_timeline.build_short_video_timeline()` がproduct codeの正本です。字幕再配置、FFmpeg filter、BGM長、進捗durationもこの結果を使用します。
- `docs/short-workspace-contract.js` はHTMLモック用の同一契約です。モック内で個別に尺を再計算しません。

clip `i` の素材尺を `d_i`、直前までの完成尺を `T_(i-1)`、設定されたtransition尺を `t` とすると、`i > 0` の重なりと配置は次のとおりです。

```text
overlap_i = transitionがcutなら0、それ以外はmin(t, T_(i-1), d_i)
output_start_i = T_(i-1) - overlap_i
T_i = output_start_i + d_i
```

したがって、60秒判定はclip尺の単純和ではなく、境界ごとの有効なoverlapを差し引いた `T_last` で行います。transition尺が短いclipや、それまでの完成尺を超える場合も上式でclampします。

## 再生・seek・timeline clickの契約

ショート側は `outputTimeSeconds` と、現在プレビューする `{clipId, sourceTimeSeconds}` を別々に保持します。すべてのtransport操作は、完成timelineのoutput位置を先に決めてから、共通のoutput→source mappingを1回だけ適用します。

| 操作 | 入力 | 共通経路 | 結果 |
|---|---:|---|---|
| 再生tick | output差分 | `outputToSource` | clip境界で次clipのsource startへ移動 |
| ±seek | output差分 | `outputToSource` | 離れた素材範囲を連続した完成尺として移動 |
| timeline click | track比率 × 実効完成尺 | `outputToSource` | 選択clip・source位置・playheadを同時更新 |
| clip選択 / In・Out編集 | clip ID + source位置 | `sourceToOutput` | 完成timeline上のplayheadへ明示的に逆変換 |

cut境界のちょうど同じoutput時刻では、次clipを選びます。crossfade中は2素材が同時に存在しますが、単一素材しか表示できないHTMLモックではincoming clipをプレビュー所有者とします。production renderは映像・音声の両方をtransitionとして合成します。

通常動画側のplayer stateとショート側のplayer stateは共有しません。ワークスペース遷移時は再生だけを停止し、それぞれの再生位置を保持します。

## 本編選択範囲からのbridge

bridge入力は `{start, end, time_basis}` を必須契約とします。

- `time_basis=source`: 範囲をsource clipとしてそのまま登録します。
- `time_basis=output`: #254の通常動画timeline mappingを境界で1回だけ適用します。除外cutをまたぐ範囲は、連続する複数のsource clipへ分割します。
- 変換後はすべて `time_basis=source` として保存します。未知のbasisや未変換のoutput clipは登録・書き出しを拒否します。

字幕セグメントから追加する場合も、その字幕が参照するsource時刻を使います。字幕が0件なら字幕由来の候補だけを無効にし、source範囲の直接追加、編集、プレビュー、書き出しは利用できます。

## UIプロトタイプの確認項目

`docs/ui-redesign-mockup.html` では次を確認します。

1. `ショート作成` で通常モードレールとは別の `short-artifact` workspaceを開き、戻ると元の通常workspaceへ復帰する。
2. clip #1=`0.8..3.2`、clip #2=`21.5..25.0`、cut transitionの場合、output `2.4` 秒がclip #2のsource `21.5` 秒になる。
3. 同じ2clipを0.5秒crossfadeにすると、メーター、playhead全長、export planがすべて `5.4` 秒になる。
4. 再生、±seek、timeline clickのいずれでも同じmapping結果になる。
5. 本編output範囲が除外cutをまたぐ場合、bridge結果が複数のsource範囲に分かれる。
6. 字幕配列が空でもsource範囲からclipを追加できる。

## 保存と書き出し

clip、transition、縦横比等のショート構成は編集プロジェクトに保存し、完成動画の出力先とは分離します。`ショート動画を書き出す` を実行した時点で出力先を検証し、通常動画と共通のFFmpeg依存確認、NVENC/CPU選択、progress/停止/失敗境界を利用します。書き出し中も `currentEditMode` やclip編集状態を別のrender modeへ変更しません。

Codexチャットの認証・thread・streaming状態は既存の単一backendを共有します。ワークスペース横断でサイドバーを常設する最終レイアウトは #249 が所有します。
