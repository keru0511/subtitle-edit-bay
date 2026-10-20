from __future__ import annotations

import threading
import time
import unittest
from typing import Callable, Mapping
from unittest.mock import patch

from openai_codex.generated import v2_all as v2
from openai_codex.models import JsonObject

from src.data_boundary import is_object_mapping
from src.gui_codex_state import (
    CODEX_SCOPES,
    CodexSessionController,
    CodexSessionSnapshot,
    build_codex_context,
)
from tests.codex_fixtures import FakeSDKClient, fake_structured_turn
from tests.typed_case import TypedTestCase


class FakeClient(FakeSDKClient):
    pass


class BlockingAccountClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.account_entered = threading.Event()
        self.account_release = threading.Event()

    def account_read(self, params: v2.GetAccountParams | JsonObject | None = None) -> v2.GetAccountResponse:
        self.account_entered.set()
        self.account_release.wait(2)
        return super().account_read(params)


class IsolatedClient(FakeClient):
    pass


class GuiCodexStateTests(TypedTestCase):
    def setUp(self) -> None:
        patcher = patch("src.gui_codex_state.run_structured_turn", side_effect=fake_structured_turn)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_context_supports_all_scopes_without_media_paths(self) -> None:
        project = {
            "video": {"path": "C:/secret/video.mkv"},
            "audio_sources": [{"path": "C:/secret/audio.wav"}],
            "subtitle_settings": {"font_size": 50, "outline_thickness": 3},
            "segments": [
                {"id": "s1", "start": 0.0, "end": 1.0, "end_secret": "no", "text": "A", "speaker": "X"},
                {"id": "s2", "start": 2.0, "end": 3.0, "text": "B", "speaker": "Y"},
            ],
        }
        self.assertEqual(set(CODEX_SCOPES), {"selected", "current", "time_range", "all"})
        for scope, kwargs in (
            ("selected", {"selected_segment_ids": {"s1"}}),
            ("current", {"current_time": 2.5}),
            ("time_range", {"range_start": 0.5, "range_end": 2.5}),
            ("all", {}),
        ):
            context = build_codex_context(project, scope, **kwargs)
            self.assertNotIn("video", context)
            self.assertNotIn("audio_sources", context)
            self.assertNotIn("end_secret", str(context))
            segment_count = context["segment_count"]
            assert isinstance(segment_count, int)
            self.assertGreaterEqual(segment_count, 1)

    def test_fake_client_streams_proposal_and_preserves_revision(self) -> None:
        client = FakeClient()
        snapshots: list[CodexSessionSnapshot] = []
        messages: list[str] = []
        controller = CodexSessionController(
            client_factory=lambda cwd: client,
            proposal_parser=lambda payload: payload,
            on_state=snapshots.append,
            on_message=messages.append,
        )
        controller.start(prompt="字幕を整える", context={"segments": []}, revision=7)
        deadline = time.time() + 2
        while controller.snapshot.state not in {"proposal_ready", "error"} and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(controller.snapshot.state, "proposal_ready")
        self.assertEqual(controller.snapshot.revision, 7)
        self.assertEqual(controller.snapshot.thread_id, "thread-1")
        self.assertIn("提案", controller.snapshot.message)
        self.assertEqual(messages, ["提案"])
        self.assertTrue(any(item.state == "running" for item in snapshots))
        self.assertEqual(
            client.thread_params,
            {
                "approvalPolicy": "never",
                "sandbox": "read-only",
            },
        )
        assert client.turn_params is not None
        self.assertEqual(client.turn_params["context"], {"segments": []})

    def test_isolated_turn_disables_tools_and_uses_structured_client(self) -> None:
        client = IsolatedClient()

        def create_client(cwd: str | None = None) -> IsolatedClient:
            return client

        controller = CodexSessionController(
            client_factory=create_client,
            proposal_parser=lambda payload: payload,
            isolated_turn=True,
        )
        controller.start(prompt="字幕を整える", context={"segments": []}, revision=8)
        worker = controller._thread
        assert worker is not None
        worker.join(2)

        self.assertEqual(controller.snapshot.state, "proposal_ready")
        self.assertEqual(len(client.mcp_calls), 2)
        thread_params = client.thread_params
        assert thread_params is not None
        self.assertEqual(thread_params["approvalPolicy"], "never")
        config = thread_params["config"]
        assert is_object_mapping(config)
        self.assertEqual(config["web_search"], "disabled")
        structured = client.structured_kwargs
        assert structured is not None
        self.assertIsInstance(structured["cwd"], str)

    def test_stop_during_blocking_account_read_discards_late_worker_result(self) -> None:
        client = BlockingAccountClient()
        snapshots: list[CodexSessionSnapshot] = []
        proposals: list[Mapping[str, object]] = []
        controller = CodexSessionController(
            client_factory=lambda cwd: client,
            proposal_parser=lambda payload: payload,
            on_state=snapshots.append,
            on_proposal=proposals.append,
        )
        controller.start(prompt="停止する", context={}, revision=3)
        self.assertTrue(client.account_entered.wait(1))

        controller.stop()
        client.account_release.set()
        deadline = time.time() + 2
        while controller._thread is not None and controller._thread.is_alive() and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(controller.snapshot.state, "stopped")
        self.assertEqual(proposals, [])
        self.assertFalse(any(item.state == "proposal_ready" for item in snapshots))

    def test_stop_interrupts_active_turn_with_thread_and_turn_ids(self) -> None:
        client = FakeClient()
        controller = CodexSessionController(client_factory=lambda cwd: client)
        controller._client = client
        controller._snapshot = CodexSessionSnapshot(
            state="running",
            thread_id="thread-1",
            turn_id="turn-1",
        )

        controller.stop()

        self.assertEqual(client.interrupted, ("thread-1", "turn-1"))
        self.assertEqual(controller.snapshot.state, "stopped")

    def test_stop_discards_queued_proposal_callback(self) -> None:
        callbacks: list[Callable[[], None]] = []
        proposals: list[Mapping[str, object]] = []
        controller = CodexSessionController(
            client_factory=lambda cwd: FakeClient(),
            proposal_parser=lambda payload: payload,
            on_proposal=proposals.append,
            callback_dispatcher=callbacks.append,
        )
        controller.start(prompt="停止する", context={})
        self.assertIsNotNone(controller._thread)
        worker = controller._thread
        assert worker is not None
        worker.join(2)

        self.assertEqual(controller.snapshot.state, "proposal_ready")
        self.assertTrue(callbacks)
        controller.stop()
        for callback in callbacks:
            callback()

        self.assertEqual(proposals, [])


if __name__ == "__main__":
    unittest.main()
