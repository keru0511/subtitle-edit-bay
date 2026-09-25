# GUIの機能別窓口

QMLは`backend`の機能別QObjectを通じて画面の状態を読み取り、操作を呼び出します。
外部フレームワークは追加せず、PySide6のProperty・Slot・Signalを使います。

| 窓口 | 実装 | 担当 |
| --- | --- | --- |
| `backend.subtitles` | `gui_subtitles_facade.py` | 字幕一覧、編集、選択、履歴、プレビュー |
| `backend.audio` | `gui_audio_facade.py` | ミキサー、音声プレビュー、音量調整案 |
| `backend.ai` | `gui_ai_facade.py` | AIチャット、認証、字幕編集提案 |
| `backend.workflow` | `gui_workflow_facade.py` | 文字起こし、書き出し、進捗、中止 |
| `backend.workspace` | `gui_workspace_facade.py` | 画面切替、再生位置、カット編集 |
| `backend.sequence` | `gui_sequence_facade.py` | 素材とシーケンス編集 |
| `backend.shortVideo` | `gui_short_video_facade.py` | ショート動画、ハイライト候補 |
| `backend.updates` | `gui_updates_facade.py` | 更新確認、取得、適用 |

例えば字幕を編集するときは、`backend.subtitles.updateSegment(index, changes)`を呼びます。
一覧は`backend.subtitles.subtitleModel`、変更通知は同じ窓口の`segmentsChanged`を使います。
機能内では自分のメソッドを呼び、機能間では相手の窓口を明示します。
画面側の通知購読も、単独の機能に属するものは同じ窓口へ接続します。

## データと寿命

機能別窓口はアプリケーションの子QObjectであり、一定のインスタンスを
`constant=True`のPropertyとして公開します。QMLエンジンごとに作り直しません。
`FeatureFacade`は親子関係と共有バックエンド参照だけを持ちます。
動的な`__getattr__`、メソッドの自動登録、多重継承でAPIを隠しません。

プロジェクトの保存・履歴・自動保存は既存の`ProjectEditorController`、
音声再生とキャッシュは`AudioPreviewController`が引き続き担当します。
通知は既存のコントローラー接続からバックエンドを経て各窓口へ中継するため、
旧APIと新APIは同じ状態を参照します。通知を受けた画面が別の派生プロパティを
使う場合は、元イベントの処理順に依存せず、そのプロパティの変更に追従します。

この分割は画面窓口の整理です。共有プロジェクト辞書への更新経路の一本化、
共有状態の所有権の全面移行、非Qtの処理層への分離はまだ完了していません。
各窓口は既存コントローラーや共有状態をバックエンドから参照します。

## 互換窓口と変更先

`gui.py`は起動、コントローラーの生成と接続、プロジェクトの開閉、診断、
既存APIの明示的な転送を残します。`backend.updateSegment(...)`などの旧APIも
同じ機能別窓口へ転送するため、Python側の既存呼び出しとQtメタオブジェクトを維持します。
この互換層のため、分割によってリポジトリ全体の行数は増えます。
新しい画面操作や判定は担当する窓口へ追加し、旧APIの転送先に処理を重複させません。

画面からの操作、Undo、プロパティの変更通知、処理中の編集拒否は
`tests/test_gui_editor.py`で実際のQMLを読み込んで確認します。
Qtが認識する型・Slot・通知の互換性は`tests/test_gui_backend_metaobject.py`で確認します。
外部関数を差し替えるテストは、その関数を利用する機能別モジュールを対象にします。

性能計測は`tests/gui_performance_scenarios.py`の計測用窓口で、QMLからの呼び出し、
全件配列生成、プレビューのキャッシュミスを記録します。比較対象の旧リビジョンには
旧バックエンド用の計測を使い、両方を同じハーネスで実行します。
`tests/test_gui_test_harness.py`では実際のQMLから計測対象を呼び、計測漏れと
互換APIによる二重計測を検証します。機能別窓口だけの変更も性能CIの対象です。

```sh
python -m unittest tests.test_gui_backend_metaobject tests.test_gui_ai_chat_state tests.test_gui_editor
python scripts/check_quality.py --lint-only
python scripts/check_quality.py --type-only
```
