# 見どころ候補検出の実装計画

Issue #174は、候補を自動採用する機能ではなく、ローカルsignalで候補を作り、利用者がpreview・調整・採用／却下できる編集支援機能として実装する。

## 子Issueと依存順

| 順序 | Issue | 範囲 | 前提 |
|---|---:|---|---|
| 1 | #175 | 字幕・音声signal、scene grouping、local ranking、cache | 既存字幕project |
| 2 | #176 | ShortModeの候補確認、preview、追加、却下 | #175、#163、#164 |
| 3 | #177 | 上位候補だけのCodex意味評価、fallback | #169、#175 |
| 4 | #178 | 採用・却下feedback、個人の保守的な順位補正 | #175、#176 |
| 5 | #179 | 既定OFFの軽量FFmpeg映像signal | #175、効果検証 |

## 共通のデータ境界

- 候補は `id`、開始・終了、カテゴリ、score、score内訳、理由、字幕抜粋、元segment idを持つ
- 候補生成・閲覧・却下だけでは字幕と `short_video.clips` を変更しない
- 動画・音声本体、認証情報、不要なlocal pathはCodexへ送らない
- Codex障害、未導入、未認証、検証失敗はlocal rankingへfallbackする
- 映像signalは既定OFFとし、改善が測定できるまで既存順位を変更しない
- キャッシュには入力fingerprint、設定、model/prompt/schema、FFmpeg/signal versionを含める

## 候補選定の制約

- 最小・最大候補長、前後余白、top_kを設定可能にする
- 重複区間を抑制し、動画全体から時間的に分散させる
- 境界調整はsource/word時刻があれば発話境界へ寄せる
- 長尺解析は進捗・キャンセル・timeout・resource budgetを提供する
- 候補追加時は既存clipとの重複を検出し、意図しない二重追加を防ぐ

## 効果測定

候補表示後に、候補IDと匿名化された操作だけをローカルで記録する。

- Top 5候補の採用率
- 最初の候補を採用するまでの時間
- 候補境界の平均調整量
- 重複候補率
- 1時間の動画あたりの解析時間とメモリ
- 映像signal ON/OFFのoffline proxyまたはTop 5採用率

字幕本文、動画パス、秘密情報はfeedbackへ保存しない。個人最適化は履歴不足時にbaselineへ戻り、重み上限と学習率で極端な順位変化を防ぐ。

### 効果測定の固定契約

- baseline fixtureは `tests/fixtures/highlight/baseline_segments.json` とし、字幕ID・時刻・話者・カテゴリを固定する。音声signalを使う場合は同じfixtureに対応する `baseline_audio_levels.json` を使い、入力順も固定する。
- 評価母集団はfixtureの全候補とし、`top_k=5` の候補ID集合をbaselineと比較する。人手評価を行う場合も、同じ候補IDを使い、母集団・期間・除外理由を記録する。
- 改善判定はbaseline比でTop 5採用率が `+5ポイント以上` 改善し、かつ重複候補率・解析時間がbaselineの `+10%以内` であること。片方だけを満たす場合は改善扱いにしない。
- 候補IDは入力segment ID、丸め済み開始・終了時刻、`scoring_version` から決定的に生成する。同じ入力の再実行でIDを変えず、アルゴリズム変更時は `scoring_version` を上げて旧cacheと混在させない。
- feedbackのschema versionを変更する場合はmigrationまたは明示的な読み捨てを実装し、未知versionを黙って解釈しない。
- 映像signal、Codex再ranking、個人補正のいずれかがbaselineより悪化した場合は、該当signalの重みを0にしてlocal baselineへrollbackする。rollback後も候補生成・追加・却下は継続できることを受け入れ条件とする。

## Epic完了条件

- [ ] Codexなしで候補生成、確認、preview、追加、却下ができる
- [ ] 同じ入力と設定から同じlocal候補を生成できる
- [ ] 候補のscore内訳と理由を確認できる
- [ ] 解析中もGUIを操作でき、キャンセルできる
- [ ] Codex再ランキングが候補件数・送信文字数を制限し、失敗時fallbackする
- [ ] 採用・却下履歴のexport/reset/deleteと個人化ON/OFFがある
- [ ] 映像signalが既定OFFで、失敗時にbaselineを壊さない
- [ ] 長尺fixtureとopt-in FFmpeg smoke/benchmarkをWindowsで確認する
- [ ] baseline fixtureを使ったTop 5比較と、改善閾値・rollback結果をCI artifactまたはレビュー記録に残す

