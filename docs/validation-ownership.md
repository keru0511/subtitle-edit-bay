# PR検証の実行責務

## 目的

通常CIとRelease readinessは同じPR仮マージcommitを検証するが、同じ検証を二重実行しない。`scripts/release_readiness.py` の分類結果を共通契約とし、ジョブの成功だけでなく「どちらが実行したか」もfail-closedで集約する。

## 検証プロファイル

| PR種別 | 通常CIが実行 | Release readinessが実行 | CIでskip必須 |
| --- | --- | --- | --- |
| 通常PR | quality、Linux portable/Qt/FFmpeg、Windows runtime/launcher/FFmpeg 6、installer build/install | 分類のみ | なし |
| VERSION-onlyリリースPR | quality、Windows runtime/launcher/FFmpeg 6 | Linux portable/Qt/FFmpeg、installer build、独立runnerでinstall/start | Linux portable/Qt/FFmpeg、installer smoke |
| リリース基盤変更PR | quality、Windows runtime/launcher/FFmpeg 6 | Linux portable/Qt/FFmpeg、installer build、独立runnerでinstall/start | Linux portable/Qt/FFmpeg、installer smoke |

リリース候補のCI identity schema v2は `validation_profile: release-candidate-v1` と `delegated_workflow: .github/workflows/release-readiness.yml` を記録する。公開側は通常CIの固有ジョブが成功し、委譲対象ジョブがskipされ、Release readiness側の対応ジョブが成功した場合だけ、この分割された検証グラフを1つの完了結果として扱う。未知schemaや別プロファイルは拒否する。

## 更新・再実行

分類、head/base、仮マージSHA/treeは同じ候補に固定する。PR更新やbase更新後は新しいrunが必要で、古い成功結果へ戻らない。分類失敗時は委譲対象を通常CIで代替実行せず、集約も失敗する。

失敗ジョブだけを再実行した場合は、各ジョブの最新attemptを確認する。Release readinessで既に成功したbuildとinstall/startは生成attemptを保持し、生成済みartifactを変更しない。

## カバレッジと実行回数

Windows runtime、launcher、FFmpeg 6互換は通常CIに残す。Release readinessのWindows buildと独立runnerのinstall/startも維持する。リリース候補1件あたり、重複していたLinux検証1回とinstaller build/install 1組を通常CIから除き、それぞれRelease readinessだけが実行する。

実runの比較では、同一のPR仮マージSHAについて各ジョブのrun ID、attempt、開始・終了時刻、runner時間を記録する。queue時間と実行時間を分け、#317の性能測定ジョブは本契約の対象に加えたり再起動したりしない。
