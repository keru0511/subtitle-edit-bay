# Codex audio mix proposal contract

Codexからの音量変更は、共通Action境界の`propose_audio_mix`を通します。Codexの自由形式出力から
GUI methodやproject JSONを直接操作しません。

## Flow

1. `inspect_audio_mix_state`がpath-freeなchannel状態、preview level、master level、limiter reduction、
   playheadを返します。
2. trusted user scope、project revision、job競合を共通dispatcherが検証します。
3. `propose_audio_mix`が安定channel IDを持つ`update_audio_channel` operationを生成します。
4. proposal表示時点ではproject、preview、historyを変更しません。
5. GUIで選択されたoperationだけをdomain validatorが再検証し、明示適用します。
6. 適用後は既存audio mix、realtime preview、project dirty、undo/redo経路を更新します。

## Allowed changes

- `volume_percent`: 0〜200の有限数
- `muted`: boolean
- `solo`: boolean
- `enabled`: boolean

unknown operation、unknown field、存在しないchannel ID、生成後に変わったproject revisionまたは
audio stateは拒否します。全channelを無効化・muteして無音にする操作は、通常の明示適用とは別に
`confirm_silence`を必要とします。

inspect/proposalにはaudio source pathやproject pathを含めません。channel indexは並び替えで変わるため、
適用対象の識別には常にchannel IDを使います。

## Stacked dependencies

- #250: Action schema、trusted scope、revision policy、job conflictの正本です。
- #249: chat内のoperation選択、before/after/reason表示、適用・破棄UIを統合します。

本変更はbackend/domain契約とQML公開property/slotを提供し、chat表示自体は#249側で統合します。
