from __future__ import annotations

import unittest
from typing import Any, Mapping

from src.codex_actions import ACTION_DEFINITIONS, ActionDispatcher, ActionScope, HandlerResult
from src.codex_orchestrator import (
    CodexOrchestrator,
    PlanError,
    PlanState,
    build_completion_plan,
)


class OrchestratorBackend:
    def __init__(self) -> None:
        self.current_revision = 4
        self.active_job = ""
        self.calls: list[tuple[str, str]] = []

    def inspect(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        self.calls.append(("inspect", action_type))
        return HandlerResult("inspected", state={"revision": self.current_revision})

    def propose(self, action_type: str, args: Mapping[str, Any], revision: int) -> HandlerResult:
        self.calls.append(("propose", action_type))
        return HandlerResult("proposed", proposal={"base_revision": revision, "operations": [{"id": "op-1"}]})

    def execute(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        self.calls.append(("execute", action_type))
        if action_type == "cancel_processing":
            return HandlerResult("cancel requested", job={"job_type": "transcribe", "status": "cancelling"})
        if action_type.startswith("render"):
            return HandlerResult("rendered", job={"id": "render-1", "status": "success"})
        return HandlerResult("started", job={"id": "job-1", "status": "running"})


class CodexOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = OrchestratorBackend()
        self.scope = ActionScope("goal-1", frozenset(ACTION_DEFINITIONS))
        self.orchestrator = CodexOrchestrator(ActionDispatcher(self.backend), self.scope)

    def build(self, *, state: Mapping[str, Any] | None = None) -> PlanState:
        return build_completion_plan(
            goal="YouTube用に完成させる",
            scope=("transcription", "subtitle", "audio", "render"),
            scope_id="goal-1",
            project_revision=4,
            current_state=state or {},
        )

    def test_single_action_request_does_not_create_plan(self) -> None:
        with self.assertRaisesRegex(PlanError, "single-action"):
            build_completion_plan(
                goal="文字起こしして",
                scope=("transcription",),
                scope_id="goal-1",
                project_revision=4,
                current_state={},
            )

    def test_completed_domains_are_skipped(self) -> None:
        plan = self.build(state={"subtitles_ready": True, "subtitle_reviewed": True})
        self.assertEqual([step.id for step in plan.steps], ["audio_review", "render"])

    def test_plan_uses_inspect_then_typed_action_and_waits_for_job(self) -> None:
        plan = self.build()
        self.orchestrator.advance(plan)

        self.assertEqual(
            self.backend.calls,
            [("inspect", "inspect_project_state"), ("execute", "start_transcription")],
        )
        self.assertEqual(plan.status, "running")
        self.assertEqual(plan.steps[0].status, "running")

        self.backend.current_revision = 5
        self.orchestrator.resolve_job(plan, status="completed", project_revision=5)
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.steps[0].status, "success")
        self.assertEqual(self.backend.calls[-1], ("inspect", "inspect_project_state"))

    def test_proposal_stops_for_explicit_approval_then_reinspects(self) -> None:
        plan = self.build(state={"subtitles_ready": True})
        self.orchestrator.advance(plan)

        self.assertEqual(plan.status, "waiting_approval")
        self.assertEqual(plan.steps[0].action_type, "propose_subtitle_edit")
        self.assertEqual(plan.steps[0].status, "waiting_approval")

        self.backend.current_revision = 5
        self.orchestrator.resolve_proposal(plan, applied=True, project_revision=5)
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.steps[0].status, "success")

    def test_manual_edit_marks_pending_plan_stale_before_next_action(self) -> None:
        plan = self.build(state={"subtitles_ready": True})
        self.backend.current_revision = 5
        self.orchestrator.advance(plan)

        self.assertEqual(plan.status, "stale")
        self.assertEqual(plan.steps[0].status, "stale")
        self.assertEqual(self.backend.calls, [("inspect", "inspect_project_state")])

        self.orchestrator.reinspect(plan)
        self.assertEqual(plan.project_revision, 5)
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.steps[0].status, "pending")

    def test_blocking_review_prevents_render(self) -> None:
        plan = build_completion_plan(
            goal="問題なければ書き出す",
            scope=("subtitle", "render"),
            scope_id="goal-1",
            project_revision=4,
            current_state={"subtitle_reviewed": True},
        )
        self.orchestrator.set_blocking_issues(plan, [{"severity": "blocking", "reason": "字幕欠落"}])
        self.orchestrator.advance(plan)

        self.assertEqual(plan.status, "paused")
        self.assertEqual(plan.steps[0].status, "pending")
        self.assertNotIn(("execute", "render_normal"), self.backend.calls)

    def test_pause_resume_cancel_and_persisted_round_trip(self) -> None:
        plan = self.build()
        self.orchestrator.pause(plan)
        restored = PlanState.from_json(plan.to_json())
        self.assertEqual(restored.to_json(), plan.to_json())

        self.orchestrator.resume(restored, project_revision=4)
        self.assertEqual(restored.status, "pending")
        self.orchestrator.cancel(restored)
        self.assertEqual(restored.status, "canceled")

    def test_cancel_stops_running_backend_job_through_typed_action(self) -> None:
        plan = self.build()
        self.orchestrator.advance(plan)
        self.orchestrator.cancel(plan)

        self.assertEqual(self.backend.calls[-1], ("execute", "cancel_processing"))
        self.assertEqual(plan.steps[0].status, "canceled")
        self.assertEqual(plan.status, "canceled")

    def test_scope_cannot_be_broadened_by_plan(self) -> None:
        narrow = ActionScope("goal-1", frozenset({"inspect_project_state"}))
        orchestrator = CodexOrchestrator(ActionDispatcher(self.backend), narrow)
        plan = self.build()
        orchestrator.advance(plan)

        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.steps[0].result["code"], "out_of_scope")
        self.assertNotIn(("execute", "start_transcription"), self.backend.calls)


if __name__ == "__main__":
    unittest.main()
