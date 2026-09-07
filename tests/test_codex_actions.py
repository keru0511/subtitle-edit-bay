from __future__ import annotations

from copy import deepcopy
import unittest
from typing import Any, Mapping

from src.codex_actions import (
    ACTION_DEFINITIONS,
    ActionDispatcher,
    ActionErrorCode,
    ActionRejected,
    ActionScope,
    GuiActionBackend,
    HandlerResult,
)


class FakeBackend:
    def __init__(self) -> None:
        self.current_revision = 7
        self.active_job = ""
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.project = {"segments": [{"id": "s1", "text": "before"}]}

    def inspect(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        self.calls.append(("inspect", action_type, dict(args)))
        return HandlerResult("inspected", state=deepcopy(self.project))

    def propose(self, action_type: str, args: Mapping[str, Any], revision: int) -> HandlerResult:
        self.calls.append(("propose", action_type, dict(args)))
        if args.get("intent") == "invalid operation":
            raise ActionRejected(ActionErrorCode.INVALID_OPERATION, "unknown proposal operation")
        return HandlerResult(
            "proposed",
            proposal={
                "base_revision": revision,
                "operations": [{"id": "op-1", "type": "update_segment"}],
            },
        )

    def execute(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        self.calls.append(("execute", action_type, dict(args)))
        return HandlerResult("started", job={"id": "job-1", "status": "running"})


def request(
    kind: str,
    action_type: str,
    args: Mapping[str, Any] | None = None,
    *,
    revision: int | None = None,
    scope_id: str = "request-1",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "type": action_type,
        "args": dict(args or {}),
        "scope_id": scope_id,
    }
    if revision is not None:
        payload["project_revision"] = revision
    return payload


class CodexActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.dispatcher = ActionDispatcher(self.backend)
        self.scope = ActionScope(
            "request-1",
            frozenset(ACTION_DEFINITIONS),
            project_revision=7,
        )

    def test_allowlisted_inspect_is_read_only_and_structured(self) -> None:
        before = deepcopy(self.backend.project)

        result = self.dispatcher.dispatch(
            request("inspect", "inspect_project_state"),
            trusted_scope=self.scope,
        ).to_json()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["current_state"], before)
        self.assertEqual(result["revision"], 7)
        self.assertEqual(self.backend.project, before)

    def test_unknown_action_and_kind_mismatch_never_reach_backend(self) -> None:
        unknown = self.dispatcher.dispatch(
            request("execute", "run_python", {"code": "open('project.json', 'w')"}),
            trusted_scope=self.scope,
        )
        mismatch = self.dispatcher.dispatch(
            request("execute", "inspect_project_state"),
            trusted_scope=self.scope,
        )

        self.assertEqual(unknown.code, "unknown_action")
        self.assertEqual(mismatch.code, "kind_mismatch")
        self.assertEqual(self.backend.calls, [])
        self.assertNotIn("shell", self.dispatcher.allowed_action_types)

    def test_schema_rejects_unknown_fields_types_ranges_and_values(self) -> None:
        cases = [
            {**request("inspect", "inspect_project_state"), "method": "saveProject"},
            request("execute", "start_transcription", {"mode": "arbitrary"}),
            request(
                "propose",
                "propose_subtitle_edit",
                {"intent": "変更", "selection_scope": "time_range", "range_start": 3, "range_end": 1},
                revision=7,
            ),
            request("execute", "render_normal", {"overwrite": "yes"}, revision=7),
        ]

        for payload in cases:
            with self.subTest(payload=payload):
                result = self.dispatcher.dispatch(payload, trusted_scope=self.scope)
                self.assertEqual(result.code, "invalid_schema")
        self.assertEqual(self.backend.calls, [])

    def test_scope_id_and_allowlist_are_checked_against_backend_owned_scope(self) -> None:
        wrong_id = self.dispatcher.dispatch(
            request("inspect", "inspect_project_state", scope_id="model-invented"),
            trusted_scope=self.scope,
        )
        narrow_scope = ActionScope("request-1", frozenset({"inspect_project_state"}))
        broadened = self.dispatcher.dispatch(
            request("execute", "start_highlight_analysis", revision=7),
            trusted_scope=narrow_scope,
        )

        self.assertEqual(wrong_id.code, "out_of_scope")
        self.assertEqual(broadened.code, "out_of_scope")
        self.assertEqual(self.backend.calls, [])

    def test_stale_request_or_stale_trusted_scope_is_rejected(self) -> None:
        stale_request = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_subtitle_edit",
                {"intent": "自然に", "selection_scope": "all"},
                revision=6,
            ),
            trusted_scope=self.scope,
        )
        stale_scope = ActionScope(
            "request-1",
            frozenset(ACTION_DEFINITIONS),
            project_revision=6,
        )
        stale_context = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_subtitle_edit",
                {"intent": "自然に", "selection_scope": "all"},
                revision=7,
            ),
            trusted_scope=stale_scope,
        )

        self.assertEqual(stale_request.code, "stale_revision")
        self.assertEqual(stale_context.code, "stale_revision")
        self.assertEqual(self.backend.calls, [])

    def test_proposal_generation_does_not_mutate_and_preserves_revision(self) -> None:
        before = deepcopy(self.backend.project)
        result = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_subtitle_edit",
                {"intent": "自然に", "selection_scope": "selected"},
                revision=7,
            ),
            trusted_scope=self.scope,
        )

        self.assertEqual(result.status.value, "success")
        self.assertEqual(result.proposal["base_revision"], 7)
        self.assertEqual(self.backend.project, before)

    def test_timeline_proposal_action_requires_an_allowlisted_target(self) -> None:
        accepted = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_timeline_edit",
                {"intent": "60秒にして", "target": "short"},
                revision=7,
            ),
            trusted_scope=self.scope,
        )
        rejected = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_timeline_edit",
                {"intent": "切って", "target": "source-file"},
                revision=7,
            ),
            trusted_scope=self.scope,
        )

        self.assertEqual(accepted.status.value, "success")
        self.assertEqual(rejected.code, "invalid_schema")

    def test_unknown_proposal_operation_is_returned_as_structured_rejection(self) -> None:
        result = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_subtitle_edit",
                {"intent": "invalid operation", "selection_scope": "all"},
                revision=7,
            ),
            trusted_scope=self.scope,
        )

        self.assertEqual(result.code, "invalid_operation")
        self.assertEqual(result.status.value, "rejected")

    def test_running_job_rejects_conflicting_propose_and_execute(self) -> None:
        self.backend.active_job = "render"
        propose = self.dispatcher.dispatch(
            request(
                "propose",
                "propose_audio_mix",
                {"intent": "BGMを下げる"},
                revision=7,
            ),
            trusted_scope=self.scope,
        )
        execute = self.dispatcher.dispatch(
            request("execute", "start_highlight_analysis", revision=7),
            trusted_scope=self.scope,
        )

        self.assertEqual(propose.code, "job_conflict")
        self.assertEqual(execute.code, "job_conflict")
        self.assertEqual(self.backend.calls, [])

    def test_destructive_execute_needs_confirmation_from_trusted_scope(self) -> None:
        unconfirmed = self.dispatcher.dispatch(
            request("execute", "start_transcription", {"mode": "replace"}, revision=7),
            trusted_scope=self.scope,
        )
        confirmed_scope = ActionScope(
            "request-1",
            frozenset({"start_transcription"}),
            confirmed_actions=frozenset({"start_transcription"}),
        )
        confirmed = self.dispatcher.dispatch(
            request("execute", "start_transcription", {"mode": "replace"}, revision=7),
            trusted_scope=confirmed_scope,
        )

        self.assertEqual(unconfirmed.code, "confirmation_required")
        self.assertEqual(confirmed.status.value, "success")
        self.assertEqual(confirmed.job["id"], "job-1")

    def test_non_destructive_execute_returns_existing_job_reference(self) -> None:
        result = self.dispatcher.dispatch(
            request("execute", "start_transcription", {"mode": "merge"}, revision=7),
            trusted_scope=self.scope,
        )

        self.assertEqual(result.status.value, "success")
        self.assertEqual(result.job, {"id": "job-1", "status": "running"})

    def test_handler_exception_is_sanitized(self) -> None:
        def fail(_action_type: str, _args: Mapping[str, Any]) -> HandlerResult:
            raise RuntimeError("secret at C:/Users/name/project.json")

        self.backend.inspect = fail  # type: ignore[method-assign]
        result = self.dispatcher.dispatch(
            request("inspect", "inspect_project_state"),
            trusted_scope=self.scope,
        )

        self.assertEqual(result.code, "handler_failed")
        self.assertNotIn("C:/", result.message)


class GuiActionBackendTests(unittest.TestCase):
    def test_timeline_proposal_dispatches_only_to_fixed_gui_boundary(self) -> None:
        class GuiStub:
            calls: list[tuple[str, str]] = []

            def start_codex_timeline_proposal(self, *, intent: str, target: str) -> bool:
                self.calls.append((intent, target))
                return True

        gui = GuiStub()
        result = GuiActionBackend(gui).propose(
            "propose_timeline_edit",
            {"intent": "冒頭を短く", "target": "normal"},
            4,
        )

        self.assertEqual(gui.calls, [("冒頭を短く", "normal")])
        self.assertEqual(result.state, {"status": "running", "target": "normal"})

    def test_inspect_filters_local_paths_and_unknown_dispatch_is_rejected(self) -> None:
        class GuiStub:
            _project_revision = 4
            _running = False
            _active_job = ""
            _project_dirty = False
            _selected_segment_index = 0
            _project = {
                "video": {"path": "C:/private/source.mp4"},
                "segments": [{"id": "s1", "text": "字幕", "path": "C:/private/subtitle.txt"}],
            }
            subtitleSegments = _project["segments"]
            audioMixerChannels = [{"id": "bgm", "enabled": True, "volume_percent": 70, "path": "C:/private/bgm.wav"}]
            highlightAnalysisState = "idle"

        backend = GuiActionBackend(GuiStub())
        subtitle = backend.inspect("inspect_subtitle_state", {}).state
        audio = backend.inspect("inspect_audio_mix_state", {}).state

        self.assertNotIn("path", subtitle["segments"][0])
        self.assertNotIn("path", audio["channels"][0])
        with self.assertRaisesRegex(ActionRejected, "no backend handler"):
            backend.inspect("save_project", {})


if __name__ == "__main__":
    unittest.main()
