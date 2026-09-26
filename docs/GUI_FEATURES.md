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
起動時にそれぞれのコントローラーを担当窓口へ直接渡し、機能側はバックエンドの
非公開フィールドを経由せずに呼び出します。
AIチャット、編集案、操作ディスパッチャーも起動時に`AIChatFacade`へまとめて渡し、
非同期接続を始める前に依存関係を確定します。
通知は既存のコントローラー接続からバックエンドを経て各窓口へ中継するため、
旧APIと新APIは同じ状態を参照します。通知を受けた画面が別の派生プロパティを
使う場合は、元イベントの処理順に依存せず、そのプロパティの変更に追従します。

字幕・カット・シーケンス・音量・ショート編集は`ProjectEditorController`の
`commit_*`を通して確定し、Undoと変更通知の境界を共有します。詳細と残る更新経路は
[GUIアーキテクチャ](GUI_ARCHITECTURE.md)を参照してください。
この分割は画面窓口の整理であり、共有状態の所有権の全面移行や
非Qtの処理層への分離はまだ完了していません。
一部の共有状態と通知は引き続きバックエンドを経由します。
更新処理とハイライト解析の一時状態は、それぞれ`UpdateFacade`と
`ShortVideoFacade`が所有します。バックエンドには非公開状態の転送プロパティを
置かず、テストも状態の所有者を直接確認します。
画面切替・共通再生位置・編集モードは`WorkspaceFacade`、シーケンスの再生位置と
直近のエラーは`SequenceFacade`が所有します。
`SequenceFacade`が必要とする素材パスの変換・検証と状態表示は、境界アダプターへ
まとめて接続します。シーケンス操作の本体からバックエンドの非公開メソッドを呼びません。
処理進捗、エンコード時間の推定、文字起こし結果を既存編集へ統合するための
一時状態は`WorkflowFacade`が所有します。AI操作からの進捗照会も同じ状態を読みます。

字幕ID索引、開始時刻と終了時刻の検索配列、レイアウト集計値、プレビュー文字列の
キャッシュは`SubtitleFacade`自身が所有します。親バックエンドには保持しません。
保存対象の字幕データとUndo/Redoは引き続き`ProjectEditorController`が所有し、
既存の字幕同期処理で表示状態を再構築します。削除済みIDのキャッシュを除去し、
残るIDは文字列・時刻等の署名が一致する場合だけ再利用します。
プロジェクトの切り替えやUndo/Redoも同じ同期経路を通ります。
Qtの字幕一覧モデルとフォント一覧の所有先は今回の対象に含めません。


## 互換窓口と変更先

`gui.py`は起動、コントローラーの生成と接続、プロジェクトの開閉、診断を残します。
`gui_backend_compatibility.py`は`backend.updateSegment(...)`などの旧公開APIを
同じ機能別窓口へ転送し、Python側の既存呼び出しとQtメタオブジェクトを維持します。
現行QMLの機能操作は機能別窓口を呼び、旧公開APIの転送は追加しません。
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
