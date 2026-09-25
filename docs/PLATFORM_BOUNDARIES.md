# OS差分の境界

本変更はMac対応の前段として、プロセス操作・保存先・インストーラー更新のOS依存を分離する。Mac版の配布や動作保証を追加するものではない。

## 呼び出し側の契約

| 境界 | 責務 | 呼び出し側 |
| --- | --- | --- |
| `src/process_utils.py` | 非表示・独立起動のオプション、通常停止・強制停止 | GUI、GuiJobRunner、外部コマンド処理 |
| `src/qprocess_launcher.py` | QProcess向けのプログラム・引数・作業ディレクトリの解決 | GuiJobRunner |
| `src/platform_paths.py` | ログ・更新ダウンロード・音声プレビューの保存先 | ログ、更新管理、GUI |
| `src/platform_updates.py` | 対応するインストーラー名、保存名、適用コマンド | リリース取得、GUI、更新管理 |

GUIはOS名・Windows用拡張子・保存先の環境変数を使ってこれらの方針を決めない。更新管理はダウンロード・検証とエラー変換を担当し、OS別の適用コマンドを境界に委譲する。単純な関数の境界を使い、OS全体を扱う巨大な基底クラスは設けない。

## 維持する動作と変更点

- Windowsの保存先と更新ヘルパーへの検証情報の引き渡しを維持する。ログと更新キャッシュで異なる既存のアプリ名も、データ移行を伴わないよう維持する。
- キャンセル対象のジョブID・PIDの確認と5秒後の強制停止はGuiJobRunnerが担当する。OS別の停止方法だけを委譲する。
- Windowsでは従来どおり`taskkill /T`を使用する。POSIXでは従来どおり直接のQProcessを停止する。子孫プロセスの停止まで保証する共通契約は未実装であり、Mac対応の完了条件に残す。
- Windows以外では自動更新を未対応として拒否する。更新確認は通信前、更新起動はGUIの保存処理前、ZIP直接適用はバックアップの削除・ファイル書換え・ダウンロード前に停止する。インストーラー適用を直接要求された場合も明示的に失敗する。

## 残る対応

1. POSIXのプロセスグループ作成と子孫プロセス停止を、起動・キャンセル・タイムアウトの一連の契約として実装する。親だけの終了を成功条件にしない。
2. 従来のアーカイブ更新にあるPowerShellセットアップ前提を分離し、Mac用セットアップ・配布・更新を実装する。
3. Macアプリ配布時の設定・ログ保存先を定め、既存保存先からの移行方針を設ける。
4. GPU処理、外部CLI探索、フォント既定値の境界を整える。
5. Mac CIで依存導入、GUI起動、子孫停止、短い素材の処理を検証する。Windowsのプロセスツリー停止とインストーラー実行はWindows CIで検証する。

## 確認コマンド

依存導入済みの仮想環境でリポジトリルートから実行する。

```sh
python -m unittest tests.test_process_utils tests.test_gui_job_runner tests.test_qprocess_launcher tests.test_application_logging tests.test_update_manager tests.test_updater
python scripts/check_quality.py --lint-only
```

OS名を差し替える単体テストは、パッケージ選択とコマンド生成を検証する。実OS上でのインストーラー実行やプロセス停止の証明には代用しない。

GUI全体を生成するテストは、QCoreApplicationを使うジョブテストとは別プロセスで実行する。

```sh
QT_QPA_PLATFORM=offscreen python -m unittest tests.test_gui_editor.GuiEditorRegressionTests.test_backend_restart_application_launches_and_quits tests.test_gui_editor.GuiEditorRegressionTests.test_backend_apply_update_starts_update_command
```

今回のローカル確認はApple Silicon Mac、Python 3.13、PySide6 6.11.2で実施した。関連テスト46件中45件成功・Windows専用1件スキップ、別プロセスのGUIテスト2件成功、Ruff成功。配布用Python 3.10のWindows実行環境やWhisperXは、この確認に含まない。

セルフレビュー後、未対応OSからの更新確認・起動・ZIP直接適用を拒否する回帰テストを追加した。既存ファイルとバックアップが変化せず、通信・GUI保存・プロセス起動が行われないことを確認する。
