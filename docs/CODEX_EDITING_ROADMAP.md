# Codex字幕編集Epicの実装計画

Issue #168のEpicは、Codexへ字幕を無条件に編集させる機能ではなく、`提案 → 差分確認 → 明示適用` を一貫した境界として実装する。

## 実装順

| 順序 | Issue | 責務 | 前提 |
|---|---:|---|---|
| 1 | #169 | stdio app-server、JSON-RPC、認証、process lifecycle | #167のログ方針 |
| 2 | #170 | 提案schema、operation validation、diff、原子適用、Undo entry | 既存project validation |
| 3 | #171 | GUI backendのscope/context、session state、revision競合 | #169、#170 |
| 4 | #172 | prompt、stream、diff、apply/discardの字幕編集UI | #171、#167 |
| 5 | #173 | 配布形態検出、診断、fake/opt-in E2E、利用手順 | #167〜#172 |

各Issueは独立Draft PRとしてレビューできる。依存PRを先にmainへ取り込み、後続PRは同じ受け入れ条件を再利用する。

## 固定する境界

- Codexへの接続はローカルstdioのみ。WebSocketと待受ポートはMVP対象外
- GUIはCodex未導入・未認証・接続失敗でも通常の手動編集を利用できる
- contextは選択範囲の字幕ID、時刻、本文、話者、字幕設定に限定し、動画・音声本体、秘密情報、不要なローカルパスを含めない
- app-serverのcommand / file change approval要求は自動承認しない
- CodexはプロジェクトJSONを直接編集しない
- 提案適用前はメモリ上・ディスク上の正本を変更しない
- 適用時は候補コピーを既存のnormalize/validateへ通し、失敗時に部分適用しない
- 生成した複数operationはUndo/Redoの1単位として扱う
- project revisionが変わった提案は拒否し、再送信を案内する
- ASS更新・動画書き出しは適用後に利用者が明示的に実行する

## Epic受け入れチェック

- [ ] fake app-serverでinitialize、account、thread、turn、stream、停止、異常終了を再現できる
- [ ] JSON Schema外の提案と不正なsegment idを拒否できる
- [ ] 全件適用・選択適用・破棄・一括Undoを実行できる
- [ ] 4つのscopeと送信件数をGUIで確認できる
- [ ] stale revisionを検出し、手動編集を壊さない
- [ ] 通常ログ・診断コピーにsecretと字幕本文を漏らさない
- [ ] Codex未導入時の手動編集をWindows配布版で確認できる
- [ ] 実アカウント接続はopt-inテストへ分離されている
- [ ] セットアップ、送信範囲、再認証、障害切り分けが文書化されている

## リリース判定

Epic完了は、#169〜#173のCIが成功し、Windows配布形態ごとの手動確認が終わり、上記のチェックをIssue本文へ反映した時点とする。個別PRがDraftの間は、Codex機能をリリース機能として案内しない。
