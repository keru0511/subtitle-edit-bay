# Codex audio mix proposal contract

Codexからの音量変更は、共通Action境界の`propose_audio_mix`を通します。Codexの自由形式出力から
GUI methodやproject JSONを直接操作しません。

## Flow

1. `inspect_audio_mix_state`がpath-freeなchannel状態、preview level、master level、limiter reduction、
   playheadを返します。
2. trusted user scope、project revision、job競合を共通dispatcherが検証します。
3. `propose_audio_mix`がpath-free contextをread-onlyのCodex turnへ渡し、
   `schemas/codex_audio_mix_proposal.schema.json`に従うoperation生成を開始します。
   固定キーワードや固定増減量ではなく、現在値とpreview/master/limiter levelを判断材料にします。
4. proposal表示時点ではproject、preview、historyを変更しません。
5. Codex出力はroot、operation、changesの全階層で未知fieldを拒否し、backendが現在値から
   `before`を付与します。GUIで選択されたoperationだけを再検証し、明示適用します。
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
適用対象の識別には常に`audio:<opaque id>`を使います。旧projectでexternal channel IDにpathが
埋め込まれている場合は、audio sourceへ永続化するopaque IDへ移行し、既存のchannel設定を引き継ぎます。

GUI操作とCodex proposalは、どちらも`audio_mixer.update_audio_mix_channel()`を正本として利用します。
volume型・範囲、boolean、unknown field、channel IDの検証と実際のchannel mutationを別実装にしません。

## Stacked dependencies

- #250: Action schema、trusted scope、revision policy、job conflictの正本です。
- #249: chat内のoperation選択、before/after/reason表示、適用・破棄UIを統合します。

本変更はbackend/domain契約とQML公開property/slotを提供し、chat表示自体は#249側で統合します。
