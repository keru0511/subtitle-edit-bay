from __future__ import annotations

import time
import threading
import unittest

from src.gui_codex_state import (
    CODEX_SCOPES,
    CodexSessionController,
    CodexSessionSnapshot,
    build_codex_context,
)


class FakeNotification:
    def __init__(self, method: str, params: dict[str, object]) -> None:
        self.method = method
        self.params = params


class FakeClient:
    def __init__(self) -> None:
        self.notification_callback = None
        self.started = False
        self.interrupted = None

    def start(self) -> dict[str, object]:
        self.started = True
        return {"protocolVersion": "1"}

    def stop(self) -> None:
        self.started = False

    def account_read(self) -> dict[str, object]:
        return {"authenticated": True}

    def thread_start(self, params=None) -> dict[str, object]:
        return {"threadId": "thread-1"}

    def thread_resume(self, thread_id, params=None) -> dict[str, object]:
        return {"threadId": thread_id}

    def turn_start(self, **kwargs) -> dict[str, object]:
        if self.notification_callback:
            self.notification_callback(FakeNotification("turn/started", {"turnId": "turn-1"}))
            self.notification_callback(FakeNotification("item/agentMessage/delta", {"delta": "提案"}))
        return {
            "summary": "修正",
            "warnings": [],
            "operations": [
                {"type": "update_segment", "segment_id": "s1", "changes": {"text": "修正"}}
            ],
        }

    def turn_interrupt(self, turn_id: str, *, thread_id: str) -> dict[str, object]:
        self.interrupted = (thread_id, turn_id)
        return {"interrupted": True}


class BlockingAccountClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.account_entered = threading.Event()
        self.account_release = threading.Event()

    def account_read(self) -> dict[str, object]:
        self.account_entered.set()
        self.account_release.wait(2)
        return {"authenticated": True}


class GuiCodexStateTests(unittest.TestCase):
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
            self.assertGreaterEqual(context["segment_count"], 1)

    def test_fake_client_streams_proposal_and_preserves_revision(self) -> None:
        snapshots = []
        messages = []
        controller = CodexSessionController(
            client_factory=FakeClient,
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

    def test_stop_during_blocking_account_read_discards_late_worker_result(self) -> None:
        client = BlockingAccountClient()
        snapshots = []
        proposals = []
        controller = CodexSessionController(
            client_factory=lambda: client,
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
        controller = CodexSessionController(client_factory=lambda: client)
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
        callbacks = []
        proposals = []
        controller = CodexSessionController(
            client_factory=FakeClient,
            proposal_parser=lambda payload: payload,
            on_proposal=proposals.append,
            callback_dispatcher=callbacks.append,
        )
        controller.start(prompt="停止する", context={})
        self.assertIsNotNone(controller._thread)
        controller._thread.join(2)

        self.assertEqual(controller.snapshot.state, "proposal_ready")
        self.assertTrue(callbacks)
        controller.stop()
        for callback in callbacks:
            callback()

        self.assertEqual(proposals, [])


if __name__ == "__main__":
    unittest.main()
