# Codex Action境界

Codexからアプリ機能を利用するときは、`src/codex_actions.py` の型付きActionだけを入口にします。Codexの文章からbackend method名を組み立てたり、shell、Python、任意path、任意ファイル操作を実行したりしません。Codexが無効でもGUIの既存経路は変わりません。

## Envelope

```json
{
  "schema_version": 1,
  "kind": "propose",
  "type": "propose_subtitle_edit",
  "args": {
    "intent": "選択した字幕を自然にして",
    "selection_scope": "selected"
  },
  "scope_id": "request-7f14",
  "project_revision": 42
}
```

共通JSON Schemaは `schemas/codex_action.schema.json`、Actionごとの引数Schemaとallowlistは `ACTION_DEFINITIONS` が正本です。未知のroot field、Action type、引数、enum値はbackend handlerへ渡る前に拒否されます。

`scope_id` は権限そのものではありません。Orchestrator/UIは、ユーザーの明示依頼からbackend側に `ActionScope` を作り、Actionをdispatchするときに信頼済みscopeを別経路で渡します。要求内のIDと一致しても、Action typeが信頼済みallowlistに含まれなければ実行されません。Codex出力だけでscopeや確認済み状態を拡張することはできません。

## ActionとProposal operation

- `inspect`: 状態を構造化して返すだけで、projectを変更しない
- `propose`: domain変更案の生成を開始するだけで、projectへ適用しない
- `execute`: 既存backendの長時間jobを開始する

`propose_subtitle_edit` はAction typeです。生成結果に含まれる `update_segment` や `delete_segment` はProposal operationであり、Action allowlistとは別に既存の `codex_edit_proposal` validatorが検証します。Proposal適用はこのAction境界に公開していません。GUI/チャットの明示的な適用操作で、stable ID、値、range、最新revisionを再検証してから適用します。

## Revision、競合、確認

変更対象を読む `propose` とrenderは `project_revision` が必須です。要求、信頼済みscope、backendの現在revisionの3つが一致しない場合は `stale_revision` になります。実行中jobと競合するpropose/executeは `job_conflict` です。

既存字幕を置き換える文字起こしや成果物上書きは、信頼済みscopeの `confirmed_actions` に同じAction typeがある場合だけ通ります。要求payloadへ確認フラグを追加しても受理されません。出力先がGUIで確定していないrenderは、Codex経路からファイル選択ダイアログを開かず `precondition_failed` として返します。

## 既存backendとの接続

`GuiActionBackend` はAction typeごとの固定handler mapだけを持ちます。inspectは不要なlocal pathを除いたsnapshotを返し、executeはGUIと同じ `transcribeProject`、`startHighlightAnalysis`、`renderVideo`、`renderShortVideo` を呼びます。jobの進捗・停止・完了状態は既存GUI backendと `ProcessingProgress` が引き続き正本です。

### 長時間job

`start_transcription`、`start_highlight_analysis`、`render_normal`、`render_short` は、開始結果に開始ごとに固有の `job_id`、job種別、進捗率、追跡用 `inspect_processing_state`、停止用 `cancel_processing` を返します。字幕プレビューの再生成は `rebuild_subtitle_preview` で既存の同期処理を呼び、完了済みResultを返します。

`inspect_processing_state` はGUIから開始した処理も含め、同じ `job_id` と工程、進捗率、現在内容、停止可否を既存trackerから取得します。`QProcess.started` の通知前でも、GUIが同期的に登録したactive jobとtrackerの `job_id` が一致すれば開始済みとして追跡し、通知後と同じIDを維持します。この起動待ち区間はまだ停止不可です。完了・失敗・中断後も `job_id` と `terminal_result` を返すため、Codex切断中に終了しても再接続後に結果を判断できます。`cancel_processing` は開始結果の `job_id` を必須引数として既存の停止経路だけを使用し、job種別が同じでもIDが変わっていればstale requestとして拒否します。失敗Resultから同じActionを自動再実行する処理はありません。

既存字幕がある文字起こしではGUIと同じ `transcribeProject` を使用します。`replace` は信頼済みscopeで明示確認済みの場合だけ実行でき、`merge` は既存字幕を保持する既存統合経路へ進みます。最終renderや既存出力の上書きにも同じ確認契約を適用します。

音量・timeline Proposalなど後続のdomain Issueは、新しいbackend handlerをこの固定mapへ明示登録し、`ACTION_DEFINITIONS` の引数契約とdomain validatorを追加します。反射的な `getattr` や自由形式method名には拡張しません。

## Result

結果は `success`、`rejected`、`failed` のいずれかで、`code`、ユーザー向けmessage、現在revisionを返します。成功時は必要に応じて `current_state`、`proposal`、`job` を含みます。予期しない例外は `handler_failed` に変換し、例外本文、秘密情報、local pathをCodexへ返しません。

代表的な拒否codeは次の通りです。

- `invalid_schema`
- `unknown_action`
- `kind_mismatch`
- `out_of_scope`
- `stale_revision`
- `confirmation_required`
- `precondition_failed`
- `job_conflict`
- `invalid_operation`

## 後続Issueとの関係

本契約は #248、#251、#252、#253、#255、#256 の共通前提です。#256は複数工程の `PlanState` と信頼済みscopeのライフサイクルを担当し、実処理は必ず本Action境界へdispatchします。各domainのProposal生成・検証は対応Issue、長時間jobとチャット表示は #251 が拡張します。
