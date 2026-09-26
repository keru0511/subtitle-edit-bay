from __future__ import annotations

from typing import TYPE_CHECKING

import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from PySide6.QtCore import (
    Property,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices

from .audio_mix_proposal import (
    AudioMixProposalError,
    build_audio_mix_proposal,
)
from .gui_codex_state import (
    CODEX_OUTPUT_SCHEMA,
    CODEX_SCOPES,
    CodexSessionError,
    CodexSessionSnapshot,
    build_codex_context,
)
from .codex_app_server_client import CodexAppServerClient
from .codex_actions import ActionResult, ActionScope
from .codex_chat_routing import route_subtitle_chat_request
from .codex_runtime import detect_codex
from .gui_codex_chat_state import (
    CodexChatError,
    CodexChatSnapshot,
)
from .gemini_acp_provider import GeminiAcpProvider
from .workflow_actions import (
    render_output_path,
)

from .gui_feature_facade import FeatureFacade

if TYPE_CHECKING:
    from .gui import EditBayBackend


class AIChatFacade(FeatureFacade):
    """AIチャットと字幕編集提案の画面窓口。"""

    aiChatChanged = Signal()
    codexChatChanged = Signal()
    codexMessageChanged = Signal()
    codexProposalChanged = Signal()
    codexStateChanged = Signal()

    def __init__(self, backend: "EditBayBackend") -> None:
        super().__init__(backend)
        backend.aiChatChanged.connect(self.aiChatChanged.emit)
        backend.codexChatChanged.connect(self.codexChatChanged.emit)
        backend.codexMessageChanged.connect(self.codexMessageChanged.emit)
        backend.codexProposalChanged.connect(self.codexProposalChanged.emit)
        backend.codexStateChanged.connect(self.codexStateChanged.emit)

    @Property(str, notify=codexStateChanged)
    def codexState(self) -> str:
        backend = self._backend
        return backend._codex_session.snapshot.state

    @Property(str, notify=codexMessageChanged)
    def codexMessage(self) -> str:
        backend = self._backend
        return backend._codex_session.snapshot.message

    @Property(str, notify=codexStateChanged)
    def codexError(self) -> str:
        backend = self._backend
        return backend._codex_session.snapshot.error

    @Property("QVariantMap", notify=codexProposalChanged)
    def codexProposal(self) -> dict[str, Any]:
        backend = self._backend
        return dict(backend._codex_proposal or {})

    @Property("QStringList", constant=True)
    def codexScopes(self) -> list[str]:
        return list(CODEX_SCOPES)

    @Property(str, notify=codexChatChanged)
    def codexConnectionState(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.connection_state

    @Property(str, notify=codexChatChanged)
    def codexAuthState(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.auth_state

    @Property(str, notify=codexChatChanged)
    def codexAuthLabel(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.auth_label

    @Property(str, notify=codexChatChanged)
    def codexLoginUrl(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.login_url

    @Property(str, notify=codexChatChanged)
    def codexChatState(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.chat_state

    @Property(str, notify=codexChatChanged)
    def codexChatError(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.error

    @Property("QVariantList", notify=codexChatChanged)
    def codexModels(self) -> list[dict[str, Any]]:
        backend = self._backend
        return [dict(item) for item in backend._ai_chat.snapshot.models]

    @Property(str, notify=codexChatChanged)
    def codexSelectedModel(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.selected_model

    @Property(str, notify=codexChatChanged)
    def codexModelError(self) -> str:
        backend = self._backend
        return backend._ai_chat.snapshot.model_error

    @Property("QVariantList", notify=codexChatChanged)
    def codexChatMessages(self) -> list[dict[str, Any]]:
        backend = self._backend
        return [dict(item) for item in backend._ai_chat.snapshot.messages]

    @Property(str, notify=aiChatChanged)
    def aiChatProviderId(self) -> str:
        backend = self._backend
        return backend._ai_chat.active_provider_id

    @Property(str, notify=aiChatChanged)
    def aiChatProviderName(self) -> str:
        backend = self._backend
        return backend._ai_chat.active_provider_name

    @Property("QVariantList", notify=aiChatChanged)
    def aiChatProviders(self) -> list[dict[str, Any]]:
        backend = self._backend
        return backend._ai_chat.available_providers()

    @Property(bool, notify=aiChatChanged)
    def aiChatModelSelectionSupported(self) -> bool:
        backend = self._backend
        return backend._ai_chat.snapshot.model_selection_supported

    @Property(bool, notify=aiChatChanged)
    def aiChatLoginAvailable(self) -> bool:
        backend = self._backend
        return backend._ai_chat.snapshot.login_available

    @Property(str, notify=aiChatChanged)
    def aiChatAuthHint(self) -> str:
        backend = self._backend
        snapshot = backend._ai_chat.snapshot
        if snapshot.provider_id == "gemini" and snapshot.auth_state != "authenticated" and not snapshot.login_available:
            return "Gemini CLIでログインしてください"
        return ""

    @Slot()
    def reconnectCodexChat(self) -> None:
        backend = self._backend
        backend._ai_chat.reconnect()

    @Slot()
    def startCodexLogin(self) -> None:
        backend = self._backend
        backend._ai_chat.login()

    @Slot()
    def reloginCodex(self) -> None:
        backend = self._backend
        backend._ai_chat.login(relogin=True)

    @Slot()
    def logoutCodex(self) -> None:
        backend = self._backend
        if backend._codex_session.running:
            backend._codex_session.stop()
        if backend._codex_audio_mix_session.running:
            backend._codex_audio_mix_session.stop()
        backend._codex_proposal = None
        backend.codexProposalChanged.emit()
        backend._audio_mix_proposal = None
        backend.audioMixProposalChanged.emit()
        backend._ai_chat.logout()

    @Slot()
    def openCodexLoginPage(self) -> None:
        backend = self._backend
        login_url = backend._ai_chat.snapshot.login_url
        if login_url:
            QDesktopServices.openUrl(QUrl(login_url))

    @Slot(str)
    def selectCodexModel(self, model: str) -> None:
        backend = self._backend
        backend._ai_chat.select_model(model)

    @Slot(str, result=bool)
    def selectAIProvider(self, provider_id: str) -> bool:
        backend = self._backend
        return backend._ai_chat.select_provider(provider_id)

    @Slot()
    def reconnectAIChat(self) -> None:
        backend = self._backend
        backend._ai_chat.reconnect()

    @Slot()
    def startAIProviderLogin(self) -> None:
        backend = self._backend
        backend._ai_chat.login()

    @Slot()
    def reloginAIProvider(self) -> None:
        backend = self._backend
        backend._ai_chat.login(relogin=True)

    @Slot()
    def logoutAIProvider(self) -> None:
        backend = self._backend
        backend._ai_chat.logout()

    @Slot()
    def openAIProviderLoginPage(self) -> None:
        self.openCodexLoginPage()

    @Slot(str)
    @Slot(str, str, float, float)
    def sendCodexChatMessage(
        self,
        message: str,
        requested_scope: str = "auto",
        range_start: float = 0.0,
        range_end: float = 0.0,
    ) -> None:
        backend = self._backend
        if self._is_audio_mix_chat_request(message):
            self._start_audio_mix_chat_proposal(message)
            return
        route = route_subtitle_chat_request(
            message,
            requested_scope,
            project_loaded=backend._project is not None,
            has_selection=backend._selected_segment_index >= 0,
            current_time=float(backend.workspace.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0,
            range_start=range_start,
            range_end=range_end,
        )
        if route is None:
            backend._ai_chat.send_message(message)
            return
        # Typed edit proposals are a Codex action contract.  Gemini remains a
        # normal chat provider until a provider-neutral proposal protocol is
        # introduced; never route the same prompt to both providers.
        if backend._ai_chat.active_provider_id != "codex":
            backend._ai_chat.send_message(message)
            return
        if not backend._ai_chat.begin_proposal(message):
            return
        if route.scope == "unavailable":
            backend._codex_chat.fail_proposal("字幕を編集するには、先に編集プロジェクトを開いてください。")
            return
        if route.scope == "current":
            backend._codex_current_time = float(backend.workspace.editorPlayhead.get("sourcePositionMs", 0)) / 1000.0
        scope_id = f"chat-subtitle-{uuid4().hex}"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": "propose",
            "type": "propose_subtitle_edit",
            "args": {
                "intent": str(message).strip(),
                "selection_scope": route.scope,
            },
            "scope_id": scope_id,
            "project_revision": backend._project_revision,
        }
        if route.scope == "time_range":
            payload["args"].update({"range_start": route.range_start, "range_end": route.range_end})
        result = self.dispatch_codex_action(
            payload,
            trusted_scope=ActionScope(
                id=scope_id,
                allowed_actions=frozenset({"propose_subtitle_edit"}),
                project_revision=backend._project_revision,
            ),
        )
        if result.status.value != "success":
            backend._ai_chat.fail_proposal(result.message or "字幕の変更案を開始できませんでした。")

    @staticmethod
    def _is_audio_mix_chat_request(message: str) -> bool:
        # Route natural-language sound adjustments to the typed audio proposal.
        prompt = str(message).strip().casefold()
        if not prompt:
            return False

        # Explicit mixer controls remain unambiguous. For ordinary language,
        # combine an audio target with an adjustment intent instead of requiring
        # one exact phrase (for example, 「声を聞きやすくして」).
        explicit_audio_controls = (
            "音量",
            "ミキサー",
            "ミックス",
            "ミュート",
            "bgm",
            "volume",
            "mute",
            "solo",
            "audio mix",
            "audio mixer",
        )
        if any(term in prompt for term in explicit_audio_controls):
            return True

        audio_targets = (
            "声",
            "音声",
            "音楽",
            "ナレーション",
            "ボーカル",
            "voice",
            "audio",
            "sound",
            "music",
            "track",
        )
        adjustment_intents = (
            "聞きやす",
            "聞こえ",
            "明瞭",
            "クリア",
            "大き",
            "小さ",
            "上げ",
            "下げ",
            "調整",
            "整え",
            "バランス",
            "抑え",
            "目立",
            "clear",
            "adjust",
            "balance",
            "raise",
            "lower",
            "louder",
            "quieter",
        )
        return any(term in prompt for term in audio_targets) and any(term in prompt for term in adjustment_intents)

    def _start_audio_mix_chat_proposal(self, message: str) -> None:
        backend = self._backend
        if backend._ai_chat.active_provider_id != "codex":
            backend._ai_chat.send_message(message)
            return
        if not backend._ai_chat.begin_proposal(
            message,
            content_type="audio_mix_proposal",
            pending_text="音量ミキサーの変更案を作成しています…",
        ):
            return
        scope_id = f"chat-audio-{uuid4().hex}"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": "propose",
            "type": "propose_audio_mix",
            "args": {"intent": str(message).strip()},
            "scope_id": scope_id,
            "project_revision": backend._project_revision,
        }
        result = self.dispatch_codex_action(
            payload,
            trusted_scope=ActionScope(
                id=scope_id,
                allowed_actions=frozenset({"propose_audio_mix"}),
                project_revision=backend._project_revision,
            ),
        )
        if result.status.value != "success":
            backend._ai_chat.fail_proposal(result.message or "音量ミキサーの変更案を開始できませんでした。")

    @Slot()
    def stopCodexChat(self) -> None:
        backend = self._backend
        stopped_proposal = False
        if backend._codex_session.running:
            backend._codex_session.stop()
            stopped_proposal = True
        if backend._codex_audio_mix_session.running:
            backend._codex_audio_mix_session.stop()
            stopped_proposal = True
        if stopped_proposal:
            backend._ai_chat.fail_proposal("", cancelled=True)
        else:
            backend._ai_chat.interrupt()

    @Slot()
    def startNewCodexChat(self) -> None:
        backend = self._backend
        if backend._codex_session.running:
            backend._codex_session.stop()
        if backend._codex_audio_mix_session.running:
            backend._codex_audio_mix_session.stop()
        backend._codex_proposal = None
        backend.codexProposalChanged.emit()
        backend._audio_mix_proposal = None
        backend.audioMixProposalChanged.emit()
        backend._ai_chat.new_chat()

    def _queue_codex_system_log(self, message: object, *, severity: str = "INFO") -> None:
        backend = self._backend
        safe_message = str(message)
        self._dispatch_codex_callback(
            lambda: backend._record_log(
                safe_message,
                severity=severity,
                component="codex",
                stage="CODEX",
            )
        )

    def _create_codex_chat_client(self, cwd: str | Path | None = None) -> CodexAppServerClient:
        backend = self._backend
        runtime = detect_codex(backend.workspace_root)
        if not runtime.available:
            self._queue_codex_system_log(
                f"Codex CLI検出失敗: {runtime.error}",
                severity="ERROR",
            )
            raise CodexChatError(runtime.error)
        self._queue_codex_system_log(
            "Codex CLI検出成功: "
            f"version={runtime.version}, distribution={runtime.distribution}, "
            f"executable={runtime.executable}"
        )

        def record_app_server(message: str) -> None:
            self._queue_codex_system_log(
                message,
                severity="ERROR" if message.startswith("ERROR:") else "INFO",
            )

        return CodexAppServerClient(
            runtime.command,
            cwd=cwd or backend.workspace_root,
            log_callback=record_app_server,
        )

    def _create_gemini_chat_provider(self) -> GeminiAcpProvider:
        backend = self._backend
        return GeminiAcpProvider(
            workspace_root=backend.workspace_root,
            preferred_model=str(backend._settings.get("gemini_model", "")),
        )

    @Slot(str, str, float, float)
    def startCodexEdit(
        self,
        prompt: str,
        scope: str,
        range_start: float = 0.0,
        range_end: float = 0.0,
    ) -> None:
        backend = self._backend
        if backend._project is None:
            backend._set_status("先に編集プロジェクトを開いてください", "CHECK")
            return
        if backend._codex_session.running or backend._codex_audio_mix_session.running:
            return
        selected_ids = (
            {str(backend._project.get("segments", [])[backend._selected_segment_index].get("id"))}
            if 0 <= backend._selected_segment_index < len(backend._project.get("segments", []))
            else set()
        )
        current_time = backend._codex_current_time
        if current_time is None:
            if 0 <= backend._selected_segment_index < len(backend._project.get("segments", [])):
                current_time = float(
                    backend._project["segments"][backend._selected_segment_index].get("start", range_start)
                )
            else:
                current_time = range_start
        try:
            context = build_codex_context(
                backend._project,
                scope,
                selected_segment_ids=selected_ids,
                current_time=current_time,
                range_start=range_start,
                range_end=range_end,
            )
            backend._codex_proposal = None
            backend.codexProposalChanged.emit()
            backend._codex_session.start(
                prompt=prompt,
                context=context,
                output_schema=CODEX_OUTPUT_SCHEMA,
                revision=backend._project_revision,
            )
            backend._set_status("Codexへ編集案を依頼しています", "CODEX")
        except (CodexSessionError, ValueError) as error:
            backend._set_status(f"Codex編集を開始できません: {error}", "ERROR")

    @Slot(float)
    def setCodexCurrentTime(self, seconds: float) -> None:
        backend = self._backend
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if math.isfinite(value) and value >= 0.0:
            backend._codex_current_time = value

    @Slot()
    def stopCodexEdit(self) -> None:
        backend = self._backend
        backend._codex_session.stop()
        backend._set_status("Codex編集を停止しました", "CODEX")

    @Slot("QVariantList")
    def applyCodexProposal(self, selected_operation_ids: list[Any] | None = None) -> None:
        backend = self._backend
        if backend._running:
            return
        if backend._project is None or not backend._codex_proposal:
            backend._set_status("適用するCodex編集案がありません", "CHECK")
            return
        before = deepcopy(backend._project.get("segments", []))
        try:
            result = backend._codex_session.apply_to_project(
                backend._project,
                backend._codex_proposal,
                selected_operation_ids={str(item) for item in (selected_operation_ids or [])} or None,
                current_revision=backend._project_revision,
            )
            after = result.project.get("segments", [])
            before_by_id = {item["id"]: item for item in before}
            after_by_id = {item["id"]: item for item in after}
            changed_ids = {
                segment_id for segment_id in before_by_id.keys() | after_by_id.keys()
                if before_by_id.get(segment_id) != after_by_id.get(segment_id)
            }
            backend._project_editor_controller.commit_segment_change(
                [item for item in before if item["id"] in changed_ids],
                [item for item in after if item["id"] in changed_ids],
                result.changed_segment_ids[0] if result.changed_segment_ids else None,
            )
        except (CodexSessionError, ValueError, TypeError) as error:
            backend._set_status(f"Codex編集案を適用できません: {error}", "ERROR")
            return
        backend._codex_proposal = None
        backend.codexProposalChanged.emit()
        backend._set_status("Codex編集案を適用しました。内容を確認して保存してください", "EDIT")

    @Slot()
    def discardCodexProposal(self) -> None:
        backend = self._backend
        backend._codex_proposal = None
        backend.codexProposalChanged.emit()
        backend._set_status("Codex編集案を破棄しました", "EDIT")

    def dispatch_codex_action(
        self,
        payload: Mapping[str, Any],
        *,
        trusted_scope: ActionScope,
    ) -> ActionResult:
        """Dispatch an Action with scope authorization created outside Codex output."""

        backend = self._backend

        return backend._codex_actions.dispatch(payload, trusted_scope=trusted_scope)

    def codex_render_output_exists(self, *, short: bool) -> bool:
        """Check overwrite policy using the same output resolver as GUI render."""

        backend = self._backend

        if backend._project is None or not backend._project_path:
            return False
        try:
            return render_output_path(backend._project_path, backend._project, short=short).exists()
        except ValueError:
            return False

    def _on_codex_state(self, _snapshot: CodexSessionSnapshot) -> None:
        backend = self._backend
        backend.codexStateChanged.emit()
        backend.codexMessageChanged.emit()
        if backend._codex_session.snapshot.error:
            backend._codex_chat.fail_proposal(backend._codex_session.snapshot.error)
            backend._set_status(backend._codex_session.snapshot.error, "ERROR")

    def _on_codex_message(self, _message: str) -> None:
        backend = self._backend
        backend.codexMessageChanged.emit()

    def _on_codex_proposal(self, proposal: Mapping[str, Any]) -> None:
        backend = self._backend
        backend._codex_proposal = dict(proposal)
        backend.codexProposalChanged.emit()
        backend._codex_chat.complete_proposal(str(proposal.get("summary", "")))
        backend._set_status("Codex編集案を確認できます", "CODEX")

    def _on_codex_audio_mix_state(self, _snapshot: CodexSessionSnapshot) -> None:
        backend = self._backend
        backend.audioMixProposalChanged.emit()
        snapshot = backend._codex_audio_mix_session.snapshot
        if snapshot.error:
            backend._codex_chat.fail_proposal(snapshot.error)
            backend._set_status(snapshot.error, "ERROR")
        elif snapshot.state == "unauthenticated":
            backend._codex_chat.fail_proposal("Codexへログインしてください。")
            backend._set_status("Codexへログインしてください", "CHECK")

    def _on_codex_audio_mix_proposal(self, proposal: Mapping[str, Any]) -> None:
        backend = self._backend
        if backend._project is None:
            backend._codex_chat.fail_proposal("編集プロジェクトが閉じられました。", cancelled=False)
            return
        try:
            stored = build_audio_mix_proposal(
                proposal,
                backend.audio.audioMixerChannels,
                project_revision=backend._project_revision,
            )
        except (AudioMixProposalError, ValueError, TypeError) as error:
            backend._codex_chat.fail_proposal(f"音量ミキサーの変更案を検証できません: {error}")
            backend._set_status("音量ミキサーの変更案を検証できません", "ERROR")
            return
        backend._audio_mix_proposal = stored
        backend.audioMixProposalChanged.emit()
        backend._codex_chat.complete_proposal(str(stored.get("summary", "")))
        backend._set_status("音量ミキサーの変更案を確認できます", "CODEX")

    def _on_codex_provider_state(self, snapshot: CodexChatSnapshot) -> None:
        backend = self._backend
        if hasattr(backend, "_ai_chat"):
            backend._ai_chat._provider_state_changed("codex", snapshot)
        else:
            self._on_codex_chat_state(snapshot)

    def _on_gemini_provider_state(self, snapshot: CodexChatSnapshot) -> None:
        backend = self._backend
        if hasattr(backend, "_ai_chat"):
            backend._ai_chat._provider_state_changed("gemini", snapshot)

    def _persist_ai_provider(self, provider_id: str) -> None:
        backend = self._backend
        if backend._settings.get("ai_provider") == provider_id:
            return
        backend._save_settings({"ai_provider": provider_id}, announce=False)

    def _on_codex_chat_state(self, snapshot: CodexChatSnapshot) -> None:
        backend = self._backend
        backend.codexChatChanged.emit()
        backend.aiChatChanged.emit()
        if (
            snapshot.provider_id == "codex"
            and snapshot.connection_state == "disconnected"
            and backend._codex_session.running
        ):
            backend._codex_session.stop()
            backend._codex_chat.fail_proposal("Codexとの接続が切れたため、変更案の作成を停止しました。")
        if (
            snapshot.provider_id == "codex"
            and snapshot.connection_state == "disconnected"
            and backend._codex_audio_mix_session.running
        ):
            backend._codex_audio_mix_session.stop()
            backend._codex_chat.fail_proposal("Codexとの接続が切れたため、変更案の作成を停止しました。")
        log_state: tuple[object, ...] = (
            snapshot.provider_id,
            snapshot.connection_state,
            snapshot.auth_state,
            snapshot.chat_state,
            snapshot.selected_model,
            len(snapshot.models),
            snapshot.model_error,
            snapshot.error,
        )
        if log_state != backend._last_codex_log_state:
            backend._last_codex_log_state = log_state
            detail = (
                f"{snapshot.provider_name}状態: "
                f"connection={snapshot.connection_state}, "
                f"auth={snapshot.auth_state}, "
                f"chat={snapshot.chat_state}, "
                f"models={len(snapshot.models)}, "
                f"selected_model={snapshot.selected_model or 'none'}"
            )
            if snapshot.model_error:
                detail += f", model_error={snapshot.model_error}"
            if snapshot.error:
                detail += f", error={snapshot.error}"
            backend._record_log(
                detail,
                severity="ERROR" if snapshot.error else "INFO",
                component=snapshot.provider_id,
                stage="CODEX",
            )
        if snapshot.login_url and snapshot.login_url != backend._last_codex_login_url:
            backend._last_codex_login_url = snapshot.login_url
            QDesktopServices.openUrl(QUrl(snapshot.login_url))

    def _persist_codex_model(self, model: str) -> None:
        backend = self._backend
        if backend._settings.get("codex_model") == model:
            return
        backend._save_settings({"codex_model": model}, announce=False)

    def _persist_gemini_model(self, model: str) -> None:
        backend = self._backend
        if backend._settings.get("gemini_model") == model:
            return
        backend._save_settings({"gemini_model": model}, announce=False)

    def _dispatch_codex_callback(self, callback: Callable[[], None]) -> None:
        backend = self._backend
        backend.codexCallbackRequested.emit(callback)

    @Slot(object)
    def _run_codex_callback(self, callback: object) -> None:
        if callable(callback):
            callback()
