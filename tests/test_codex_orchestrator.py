from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from typing import Any, Mapping

from src.codex_actions import (
    ACTION_DEFINITIONS,
    ActionDispatcher,
    ActionScope,
    GuiActionBackend,
    HandlerResult,
)
from src.codex_orchestrator import (
    CodexOrchestrator,
    CodexPlanController,
    PlanError,
    PlanState,
    PlanStateStore,
    PlanStep,
    build_completion_plan,
    completion_scope_for_request,
    plan_state_path,
)


def finding(
    issue_id: str,
    category: str,
    severity: str,
    *,
    route: str = "",
) -> dict[str, Any]:
    return {
        "id": issue_id,
        "category": category,
        "severity": severity,
        "target": {},
        "reason": f"{category} needs attention",
        "recommendation": {"available": bool(route), "route": route},
    }


def review(revision: int, *issues: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_revision": revision,
        "issues": [deepcopy(dict(item)) for item in issues],
        "recommended_order": [str(item["id"]) for item in issues],
        "truncated": False,
        "remaining_count": 0,
    }
    payload.update(extra)
    return payload


class ReviewBackend:
    def __init__(self) -> None:
        self.current_revision = 4
        self.active_job = ""
        self.active_job_id = ""
        self.job_sequence = 0
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.reviews: list[dict[str, Any]] = []
        self.project_state: dict[str, Any] = {
            "loaded": True,
            "dirty": False,
            "revision": 4,
            "segment_count": 2,
            "has_video": True,
            "render_complete": False,
            "short_render_complete": False,
        }
        self.proposal_async = False
        self.running_jobs: set[str] = {"start_transcription"}

    def inspect(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        self.calls.append(("inspect", action_type, dict(args)))
        if action_type == "inspect_project_state":
            state = dict(self.project_state)
            state["revision"] = self.current_revision
            return HandlerResult("project inspected", state=state)
        if action_type == "review_project":
            payload = self.reviews.pop(0) if self.reviews else review(self.current_revision)
            payload = deepcopy(payload)
            payload["project_revision"] = self.current_revision
            return HandlerResult("project reviewed", state={"review_result": payload})
        if action_type == "inspect_processing_state":
            return HandlerResult(
                "processing inspected",
                state={"running": bool(self.active_job), "job_id": self.active_job_id},
            )
        return HandlerResult("inspected", state={})

    def propose(self, action_type: str, args: Mapping[str, Any], revision: int) -> HandlerResult:
        self.calls.append(("propose", action_type, dict(args)))
        if self.proposal_async:
            return HandlerResult("proposal started", state={"status": "running"})
        return HandlerResult(
            "proposed",
            proposal={
                "base_revision": revision,
                "operations": [{"id": f"{action_type}-op-1"}],
            },
        )

    def execute(self, action_type: str, args: Mapping[str, Any]) -> HandlerResult:
        self.calls.append(("execute", action_type, dict(args)))
        if action_type == "cancel_processing":
            if str(args.get("job_id", "")) != self.active_job_id:
                raise ValueError("unexpected job identity")
            job_type = str(args.get("job_type") or self.active_job or "transcribe")
            return HandlerResult(
                "cancel requested",
                job={"job_id": self.active_job_id, "type": job_type, "status": "cancelling"},
            )
        job_type = {
            "start_transcription": "transcribe",
            "render_normal": "render",
            "render_short": "render_short",
        }.get(action_type, action_type)
        self.job_sequence += 1
        self.active_job_id = f"job-{self.job_sequence}"
        if action_type in self.running_jobs:
            self.active_job = job_type
            return HandlerResult(
                "started",
                job={"job_id": self.active_job_id, "type": job_type, "status": "running"},
            )
        return HandlerResult(
            "completed",
            job={"job_id": self.active_job_id, "type": job_type, "status": "completed"},
        )


class CodexOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = ReviewBackend()
        self.scope = ActionScope(
            "goal-1",
            frozenset(ACTION_DEFINITIONS),
            project_revision=4,
        )
        self.orchestrator = CodexOrchestrator(ActionDispatcher(self.backend), self.scope)

    def build(self, scope: tuple[str, ...] = ("subtitle", "audio", "timeline", "render")) -> PlanState:
        return build_completion_plan(
            goal="YouTube用の動画を完成させる",
            scope=scope,
            scope_id="goal-1",
            project_revision=4,
            current_state=self.backend.project_state,
        )

    def advance_until_stopped(self, plan: PlanState, limit: int = 16) -> None:
        for _ in range(limit):
            self.orchestrator.advance(plan)
            if plan.status in {"running", "waiting_approval", "paused", "success", "failed", "stale"}:
                return
        self.fail("plan did not reach a stable state")

    def test_single_action_request_does_not_create_plan(self) -> None:
        with self.assertRaisesRegex(PlanError, "single-action"):
            build_completion_plan(
                goal="文字起こしして",
                scope=("transcription",),
                scope_id="goal-1",
                project_revision=4,
                current_state={},
            )

    def test_plan_starts_with_review_instead_of_a_fixed_checklist(self) -> None:
        plan = self.build()

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].action_type, "review_project")
        self.assertEqual(plan.steps[0].phase, "initial")

    def test_recommended_order_selects_one_typed_action_at_a_time(self) -> None:
        subtitle = finding("subtitle-1", "subtitle", "warning", route="subtitle_proposal")
        audio = finding("audio-1", "audio", "warning", route="audio_mix_proposal")
        payload = review(4, subtitle, audio)
        payload["recommended_order"] = ["audio-1", "subtitle-1"]
        self.backend.reviews.append(payload)
        plan = self.build()

        self.orchestrator.advance(plan)

        self.assertEqual(plan.steps[-1].action_type, "propose_audio_mix")
        self.assertEqual(plan.steps[-1].issue_ids, ("audio-1",))
        self.assertFalse(any(step.action_type == "propose_subtitle_edit" for step in plan.steps))

    def test_timeline_proposals_always_include_normal_or_short_target(self) -> None:
        normal = finding("timeline-1", "timeline", "warning", route="timeline_proposal")
        self.backend.reviews.append(review(4, normal))
        normal_plan = self.build(("timeline", "render"))
        self.orchestrator.advance(normal_plan)
        self.assertEqual(normal_plan.steps[-1].args["target"], "normal")

        short_backend = ReviewBackend()
        short_backend.reviews.append(
            review(4, finding("short-1", "short", "warning", route="timeline_proposal"))
        )
        short_orchestrator = CodexOrchestrator(ActionDispatcher(short_backend), self.scope)
        short_plan = build_completion_plan(
            goal="ショート動画を完成させる",
            scope=("short_timeline", "short_render"),
            scope_id="goal-1",
            project_revision=4,
            current_state=short_backend.project_state,
        )
        short_orchestrator.advance(short_plan)
        self.assertEqual(short_plan.steps[-1].args["target"], "short")

    def test_pause_resume_preserves_waiting_approval(self) -> None:
        issue = finding("timeline-1", "timeline", "blocking", route="timeline_proposal")
        self.backend.reviews.append(review(4, issue))
        plan = self.build(("timeline", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)
        calls_before_pause = list(self.backend.calls)

        self.orchestrator.pause(plan)
        self.assertEqual(plan.status, "paused")
        self.assertEqual(plan.steps[-1].status, "waiting_approval")
        self.orchestrator.resume(plan, project_revision=4)
        self.assertEqual(plan.status, "waiting_approval")
        self.orchestrator.advance(plan)

        self.assertEqual(plan.status, "waiting_approval")
        self.assertEqual(
            [call for call in self.backend.calls if call[0] in {"propose", "execute"}],
            [call for call in calls_before_pause if call[0] in {"propose", "execute"}],
        )
        self.assertFalse(any(call[1] == "render_normal" for call in self.backend.calls))

    def test_discard_reinspects_rereviews_and_blocks_render_when_issue_remains(self) -> None:
        issue = finding("subtitle-1", "subtitle", "blocking", route="subtitle_proposal")
        self.backend.reviews.extend([review(4, issue), review(4, issue), review(4, issue)])
        plan = self.build(("subtitle", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)

        self.orchestrator.resolve_proposal(
            plan,
            action_type="propose_subtitle_edit",
            applied=False,
            project_revision=4,
        )
        self.advance_until_stopped(plan)

        self.assertEqual(plan.status, "paused")
        self.assertEqual(plan.blocking_issues[0]["id"], "subtitle-1")
        self.assertEqual(
            len([call for call in self.backend.calls if call[1] == "review_project"]),
            3,
        )
        self.assertFalse(any(call[1] == "render_normal" for call in self.backend.calls))

    def test_applied_proposal_requires_post_action_and_final_review_before_render(self) -> None:
        issue = finding("subtitle-1", "subtitle", "blocking", route="subtitle_proposal")
        self.backend.reviews.extend([review(4, issue), review(5), review(5)])
        plan = self.build(("subtitle", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)
        self.backend.current_revision = 5
        self.backend.project_state["segment_count"] = 3

        self.orchestrator.resolve_proposal(
            plan,
            action_type="propose_subtitle_edit",
            applied=True,
            project_revision=5,
        )
        self.advance_until_stopped(plan)

        reviews = [call for call in self.backend.calls if call[1] == "review_project"]
        renders = [call for call in self.backend.calls if call[1] == "render_normal"]
        self.assertEqual(len(reviews), 4)
        self.assertEqual(len(renders), 1)
        self.assertEqual(plan.status, "success")

    def test_job_terminal_reinspects_and_rereviews(self) -> None:
        issue = finding("processing-1", "processing", "blocking", route="processing_action")
        self.backend.project_state["segment_count"] = 0
        self.backend.reviews.extend([review(4, issue), review(5), review(5)])
        plan = self.build(("transcription", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)
        self.assertEqual(plan.status, "running")

        self.backend.active_job = ""
        self.backend.current_revision = 5
        self.backend.project_state["segment_count"] = 8
        self.orchestrator.resolve_job(plan, status="completed", project_revision=5)
        self.advance_until_stopped(plan)

        self.assertEqual(
            len([call for call in self.backend.calls if call[1] == "review_project"]),
            4,
        )
        self.assertEqual(plan.status, "success")

    def test_failed_job_is_reviewed_once_without_automatic_retry(self) -> None:
        issue = finding("processing-1", "processing", "blocking", route="processing_action")
        self.backend.project_state["segment_count"] = 0
        self.backend.reviews.extend([review(4, issue), review(4, issue)])
        plan = self.build(("transcription", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)
        self.backend.active_job = ""

        self.orchestrator.resolve_job(plan, status="error", project_revision=4)
        self.advance_until_stopped(plan)

        starts = [call for call in self.backend.calls if call[1] == "start_transcription"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(plan.status, "failed")

    def test_incomplete_final_review_is_a_render_blocker(self) -> None:
        self.backend.reviews.extend(
            [
                review(4),
                review(4, truncated=True, remaining_count=1),
            ]
        )
        plan = self.build(("subtitle", "render"))

        self.advance_until_stopped(plan)

        self.assertEqual(plan.status, "paused")
        self.assertEqual(plan.blocking_issues[0]["id"], "review-incomplete")
        self.assertFalse(any(call[1] == "render_normal" for call in self.backend.calls))

    def test_review_ignores_blockers_outside_the_requested_output_scope(self) -> None:
        short_only = finding("short-1", "short", "blocking", route="timeline_proposal")
        self.backend.reviews.extend([review(4, short_only), review(4, short_only)])
        plan = self.build(("timeline", "render"))

        self.advance_until_stopped(plan)

        self.assertEqual(plan.status, "success")
        self.assertEqual(plan.blocking_issues, [])
        self.assertEqual(
            len([call for call in self.backend.calls if call[1] == "render_normal"]),
            1,
        )

    def test_manual_edit_marks_future_work_stale_and_reinspect_queues_review(self) -> None:
        self.backend.reviews.append(review(4))
        plan = self.build(("subtitle", "render"))
        self.backend.current_revision = 5

        self.orchestrator.advance(plan)
        self.assertEqual(plan.status, "stale")
        self.orchestrator.reinspect(plan)

        self.assertEqual(plan.project_revision, 5)
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.steps[-1].action_type, "review_project")
        self.assertEqual(plan.steps[-1].phase, "recovery")

    def test_reinspect_rebinds_trusted_scope_to_the_backend_revision(self) -> None:
        plan = self.build(("timeline", "render"))
        self.backend.current_revision = 5
        self.backend.reviews.append(
            review(5, finding("timeline-1", "timeline", "warning", route="timeline_proposal"))
        )

        self.orchestrator.advance(plan)
        self.assertEqual(plan.status, "stale")
        self.orchestrator.reinspect(plan)
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)

        self.assertEqual(plan.project_revision, 5)
        self.assertEqual(plan.status, "waiting_approval")
        self.assertEqual(self.backend.calls[-1][1], "propose_timeline_edit")

    def test_cancel_stops_running_backend_job_through_typed_action(self) -> None:
        issue = finding("processing-1", "processing", "blocking", route="processing_action")
        self.backend.project_state["segment_count"] = 0
        self.backend.reviews.append(review(4, issue))
        plan = self.build(("transcription", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)

        self.orchestrator.cancel(plan)

        self.assertEqual(self.backend.calls[-1][1], "cancel_processing")
        self.assertEqual(self.backend.calls[-1][2]["job_id"], "job-1")
        self.assertEqual(plan.status, "canceled")

    def test_cancel_never_substitutes_a_generic_result_id_for_job_id(self) -> None:
        plan = self.build(("transcription", "render"))
        plan.steps.append(
            PlanStep(
                "transcribe",
                "transcription",
                "execute",
                "start_transcription",
                status="running",
                result={"job": {"id": "not-a-tracked-job-id", "type": "transcribe"}},
            )
        )
        plan.status = "running"

        self.orchestrator.cancel(plan)

        self.assertFalse(any(call[1] == "cancel_processing" for call in self.backend.calls))
        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.message, "running job has no tracking identity")

    def test_restart_does_not_adopt_another_job_with_the_same_type(self) -> None:
        issue = finding("processing-1", "processing", "blocking", route="processing_action")
        self.backend.project_state["segment_count"] = 0
        self.backend.reviews.append(review(4, issue))
        plan = self.build(("transcription", "render"))
        self.orchestrator.advance(plan)
        self.orchestrator.advance(plan)
        self.assertEqual(plan.status, "running")

        self.backend.active_job_id = "different-transcribe-job"
        self.orchestrator.recover(plan, same_project_state=True)

        running_step = next(step for step in plan.steps if step.action_type == "start_transcription")
        self.assertEqual(running_step.status, "stale")
        self.assertEqual(plan.status, "pending")
        self.assertEqual(plan.steps[-1].phase, "recovery")

    def test_plan_round_trip_validates_duplicate_step_ids(self) -> None:
        plan = self.build()
        restored = PlanState.from_json(plan.to_json())
        self.assertEqual(restored.to_json(), plan.to_json())
        invalid = plan.to_json()
        invalid["steps"].append(deepcopy(invalid["steps"][0]))
        with self.assertRaisesRegex(PlanError, "ids must be unique"):
            PlanState.from_json(invalid)


class PlanControllerTests(unittest.TestCase):
    def test_completion_request_router_is_narrow_and_distinguishes_short(self) -> None:
        self.assertIsNone(completion_scope_for_request("この字幕を少し直して"))
        self.assertIn("render", completion_scope_for_request("この動画を完成させて") or ())
        self.assertIn("render", completion_scope_for_request("YouTube用に完成させて") or ())
        short = completion_scope_for_request("ショート動画を完成させて") or ()
        self.assertIn("short_timeline", short)
        self.assertIn("short_render", short)
        self.assertNotIn("render", short)

    def test_plan_is_persisted_and_waiting_proposal_is_restored_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.write_text("{}", encoding="utf-8")
            backend = ReviewBackend()
            backend.reviews.append(
                review(4, finding("subtitle-1", "subtitle", "warning", route="subtitle_proposal"))
            )
            restored: list[tuple[str, dict[str, Any]]] = []
            controller = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "same-project",
            )

            self.assertTrue(controller.handle_chat_request("この動画を完成させて"))
            self.assertEqual(controller.plan.status, "waiting_approval")
            self.assertTrue(plan_state_path(project_path).is_file())

            backend.current_revision = 27
            restarted = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "same-project",
                restore_proposal=lambda action, proposal: restored.append((action, dict(proposal))),
            )
            restarted.restore()

            self.assertEqual(restarted.plan.status, "waiting_approval")
            self.assertEqual(restarted.plan.project_revision, 27)
            self.assertEqual(restored[0][0], "propose_subtitle_edit")
            self.assertEqual(restored[0][1]["base_revision"], 27)

    def test_changed_project_recovery_discards_stale_wait_and_starts_fresh_review(self) -> None:
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.write_text("{}", encoding="utf-8")
            backend = ReviewBackend()
            backend.reviews.append(
                review(4, finding("subtitle-1", "subtitle", "warning", route="subtitle_proposal"))
            )
            original = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "before",
            )
            original.start("この動画を完成させて", ("subtitle", "render"))
            self.assertEqual(original.plan.status, "waiting_approval")

            backend.current_revision = 5
            backend.reviews.extend([review(5), review(5)])
            restarted = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "after",
            )
            restarted.restore()

            stale_wait = next(
                step for step in restarted.plan.steps if step.action_type == "propose_subtitle_edit"
            )
            self.assertEqual(stale_wait.status, "stale")
            self.assertEqual(restarted.plan.status, "success")
            self.assertGreaterEqual(
                len([call for call in backend.calls if call[1] == "review_project"]),
                3,
            )

    def test_store_rejects_empty_path_and_round_trips(self) -> None:
        store = PlanStateStore()
        backend = ReviewBackend()
        plan = build_completion_plan(
            goal="完成させる",
            scope=("subtitle", "render"),
            scope_id="goal-1",
            project_revision=4,
            current_state=backend.project_state,
        )
        with self.assertRaisesRegex(PlanError, "path"):
            store.save("", plan)
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.touch()
            store.save(project_path, plan)
            self.assertEqual(store.load(project_path).to_json(), plan.to_json())

    def test_cancelled_plan_clears_a_running_or_waiting_product_proposal(self) -> None:
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.touch()
            backend = ReviewBackend()
            backend.reviews.append(
                review(4, finding("subtitle-1", "subtitle", "warning", route="subtitle_proposal"))
            )
            canceled: list[str] = []
            controller = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "same-project",
                cancel_proposal=canceled.append,
            )
            controller.start("この動画を完成させて", ("subtitle", "render"))

            controller.cancel()

            self.assertEqual(controller.plan.status, "canceled")
            self.assertEqual(canceled, ["propose_subtitle_edit"])

    def test_pause_stops_running_proposal_but_preserves_waiting_proposal(self) -> None:
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.touch()
            backend = ReviewBackend()
            backend.proposal_async = True
            backend.reviews.append(
                review(4, finding("subtitle-1", "subtitle", "warning", route="subtitle_proposal"))
            )
            stopped: list[str] = []
            controller = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "same-project",
                cancel_proposal=stopped.append,
            )
            controller.start("この動画を完成させて", ("subtitle", "render"))
            self.assertEqual(controller.plan.status, "running")

            controller.pause()

            self.assertEqual(controller.plan.status, "paused")
            self.assertEqual(stopped, ["propose_subtitle_edit"])
            self.assertEqual(controller.plan.steps[-2].status, "canceled")
            self.assertEqual(controller.plan.steps[-1].phase, "after_action")

    def test_saved_manual_edit_invalidates_and_clears_waiting_proposal(self) -> None:
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.touch()
            backend = ReviewBackend()
            backend.reviews.append(
                review(4, finding("subtitle-1", "subtitle", "warning", route="subtitle_proposal"))
            )
            cleared: list[str] = []
            controller = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: backend.current_revision,
                project_state=lambda: backend.project_state,
                project_fingerprint=lambda: "changed-project",
                cancel_proposal=cleared.append,
            )
            controller.start("この動画を完成させて", ("subtitle", "render"))
            waiting = controller.plan.steps[-1]
            backend.current_revision = 5

            controller.project_saved()

            self.assertEqual(waiting.status, "stale")
            self.assertEqual(cleared, ["propose_subtitle_edit"])
            self.assertEqual(controller.plan.status, "success")


class GuiActionContractTests(unittest.TestCase):
    def gui_lifecycle_test_double(self) -> SimpleNamespace:
        """Build a test double; it is not the production EditBayBackend."""
        calls: list[tuple[str, dict[str, Any]]] = []
        progress = SimpleNamespace(
            job="",
            job_id="",
            status="idle",
            value=0.0,
            current_step="",
            as_list=lambda: [],
        )

        def start_audio(*, intent: str, revision: int) -> bool:
            calls.append(("audio", {"intent": intent, "revision": revision}))
            return True

        def start_timeline(*, intent: str, target: str) -> bool:
            calls.append(("timeline", {"intent": intent, "target": target}))
            return True

        gui = SimpleNamespace(
            _project_revision=4,
            _running=False,
            _active_job="",
            _processing_progress=progress,
            _project={
                "segments": [{"id": "s1", "start": 0.0, "end": 1.0, "text": "字幕"}],
                "video": {"path": "video.mp4"},
            },
            _project_dirty=False,
            _selected_segment_index=0,
            _codex_session=SimpleNamespace(running=False),
            _codex_audio_mix_session=SimpleNamespace(running=False),
            _codex_timeline_session=SimpleNamespace(running=False),
            _cut_editor_available=True,
            highlightAnalysisState="idle",
            highlightAnalysisProgress=0.0,
            actionCapabilities={
                "canRenderNormal": True,
                "normalRenderNeedsOutput": False,
                "canRenderShort": False,
                "shortRenderNeedsOutput": False,
                "canTranscribe": True,
                "canUseNvenc": False,
            },
            settings={},
            subtitleSegments=[{"id": "s1", "start": 0.0, "end": 1.0, "text": "字幕"}],
            audioMixerChannels=[
                {
                    "id": "audio:" + "1" * 32,
                    "kind": "video",
                    "label": "Main",
                    "enabled": True,
                    "volume_percent": 100.0,
                }
            ],
            audioPreviewLevels={},
            audioMasterLevel=1.0,
            audioLimiterReductionDb=0.0,
            cutTimeline={"cuts": []},
            shortVideoSettings={"enabled": False},
            shortVideoClips=[],
            highlightCandidates=[],
            _highlight_rejected=[],
            projectDuration=1.0,
            _dependencies=SimpleNamespace(
                ffmpeg=True,
                ffprobe=True,
                whisperx=True,
                cuda=False,
                nvenc=False,
            ),
            _codex_chat=SimpleNamespace(snapshot=SimpleNamespace(selected_model="gpt-test")),
            start_codex_audio_mix_proposal=start_audio,
            start_codex_timeline_proposal=start_timeline,
            codex_render_output_exists=lambda **_kwargs: False,
            calls=calls,
        )
        def render_video(_settings: Mapping[str, Any]) -> None:
            calls.append(("render", {}))
            gui._active_job = "render"
            gui._running = True
            progress.job = "render"
            progress.job_id = "render-job-1"
            progress.status = "running"

        gui.renderVideo = render_video
        return gui

    def test_review_routes_require_callable_gui_lifecycle_methods(self) -> None:
        gui = self.gui_lifecycle_test_double()
        del gui.start_codex_audio_mix_proposal
        gui.start_codex_timeline_proposal = None
        gui.startCodexEdit = object()

        availability = GuiActionBackend(gui)._review_route_availability()

        self.assertFalse(availability["audio_mix_proposal"])
        self.assertFalse(availability["timeline_proposal"])
        self.assertFalse(availability["subtitle_proposal"])

    def test_missing_proposal_lifecycle_fails_closed(self) -> None:
        gui = self.gui_lifecycle_test_double()
        del gui.start_codex_audio_mix_proposal
        gui.start_codex_timeline_proposal = None
        dispatcher = ActionDispatcher(GuiActionBackend(gui))
        scope = ActionScope(
            "goal",
            frozenset({"propose_audio_mix", "propose_timeline_edit"}),
            project_revision=4,
        )

        for action_type, args in (
            ("propose_audio_mix", {"intent": "音を整える"}),
            ("propose_timeline_edit", {"intent": "構成を整える", "target": "normal"}),
        ):
            with self.subTest(action_type=action_type):
                result = dispatcher.dispatch(
                    {
                        "schema_version": 1,
                        "kind": "propose",
                        "type": action_type,
                        "args": args,
                        "scope_id": "goal",
                        "project_revision": 4,
                    },
                    trusted_scope=scope,
                )
                self.assertEqual(result.code, "precondition_failed")

    def test_timeline_schema_requires_explicit_target_before_gui_call(self) -> None:
        gui = self.gui_lifecycle_test_double()
        dispatcher = ActionDispatcher(GuiActionBackend(gui))
        scope = ActionScope(
            "goal",
            frozenset({"propose_timeline_edit"}),
            project_revision=4,
        )

        missing = dispatcher.dispatch(
            {
                "schema_version": 1,
                "kind": "propose",
                "type": "propose_timeline_edit",
                "args": {"intent": "整える"},
                "scope_id": "goal",
                "project_revision": 4,
            },
            trusted_scope=scope,
        )
        invalid = dispatcher.dispatch(
            {
                "schema_version": 1,
                "kind": "propose",
                "type": "propose_timeline_edit",
                "args": {"intent": "整える", "target": "both"},
                "scope_id": "goal",
                "project_revision": 4,
            },
            trusted_scope=scope,
        )

        self.assertEqual(missing.code, "invalid_schema")
        self.assertEqual(invalid.code, "invalid_schema")
        self.assertEqual(gui.calls, [])

    def test_concrete_gui_backend_delegates_audio_and_both_timeline_targets(self) -> None:
        gui = self.gui_lifecycle_test_double()
        dispatcher = ActionDispatcher(GuiActionBackend(gui))
        scope = ActionScope(
            "goal",
            frozenset({"propose_audio_mix", "propose_timeline_edit"}),
            project_revision=4,
        )
        for action_type, args in (
            ("propose_audio_mix", {"intent": "音を整える"}),
            ("propose_timeline_edit", {"intent": "通常版を整える", "target": "normal"}),
            ("propose_timeline_edit", {"intent": "短尺版を整える", "target": "short"}),
        ):
            result = dispatcher.dispatch(
                {
                    "schema_version": 1,
                    "kind": "propose",
                    "type": action_type,
                    "args": args,
                    "scope_id": "goal",
                    "project_revision": 4,
                },
                trusted_scope=scope,
            )
            self.assertEqual(result.status.value, "success")

        self.assertEqual(gui.calls[0][0], "audio")
        self.assertEqual(gui.calls[1][1]["target"], "normal")
        self.assertEqual(gui.calls[2][1]["target"], "short")

    def test_chat_plan_wiring_with_lifecycle_test_double_through_approval_and_render(self) -> None:
        """Exercise PlanController wiring; dependency GUI lifecycle is a test double here."""

        gui = self.gui_lifecycle_test_double()
        backend = GuiActionBackend(gui)
        review_outputs: list[dict[str, Any]] = [
            {
                "project_revision": 4,
                "issues": [
                    {
                        "id": "timeline-1",
                        "category": "timeline",
                        "severity": "blocking",
                        "target": {
                            "segment_ids": [],
                            "channel_ids": [],
                            "clip_ids": [],
                            "cut_ids": [],
                            "candidate_ids": [],
                            "start_seconds": None,
                            "end_seconds": None,
                        },
                        "reason": "timeline needs attention",
                        "recommendation": {"available": True, "route": "timeline_proposal"},
                    }
                ],
                "recommended_order": ["timeline-1"],
            },
            {"project_revision": 5, "issues": [], "recommended_order": []},
            {"project_revision": 5, "issues": [], "recommended_order": []},
            {"project_revision": 5, "issues": [], "recommended_order": []},
        ]

        class ReviewClient:
            def __init__(self) -> None:
                self.thread_calls: list[dict[str, Any]] = []
                self.turn_calls: list[dict[str, Any]] = []

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

            def account_read(self, *, refresh_token: bool = False) -> Mapping[str, Any]:
                del refresh_token
                return {"authenticated": True}

            def mcp_server_status_list(self, **_kwargs: Any) -> Mapping[str, Any]:
                return {"data": [], "nextCursor": None}

            def thread_start(self, params: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
                self.thread_calls.append(dict(params or {}))
                return {"thread": {"id": f"review-thread-{len(self.thread_calls)}"}}

            def run_structured_turn(self, **kwargs: Any) -> Mapping[str, Any]:
                self.turn_calls.append(dict(kwargs))
                return deepcopy(review_outputs.pop(0))

        clients: list[ReviewClient] = []

        def create_review_client(*, cwd: str | None = None) -> ReviewClient:
            self.assertTrue(cwd)
            client = ReviewClient()
            clients.append(client)
            return client

        gui._create_codex_chat_client = create_review_client
        with TemporaryDirectory() as directory:
            project_path = Path(directory) / "sample.subtitle-project.json"
            project_path.touch()
            controller = CodexPlanController(
                ActionDispatcher(backend),
                project_path=lambda: str(project_path),
                project_revision=lambda: gui._project_revision,
                project_state=lambda: {
                    "loaded": True,
                    "dirty": False,
                    "revision": gui._project_revision,
                    "segment_count": 1,
                    "has_video": True,
                },
                project_fingerprint=lambda: "same-project",
            )

            self.assertTrue(controller.handle_chat_request("この動画を完成させて"))
            self.assertEqual(controller.plan.status, "running")
            self.assertEqual(gui.calls[-1], ("timeline", {"intent": "timeline needs attention", "target": "normal"}))

            controller.proposal_ready(
                "propose_timeline_edit",
                {"base_revision": 4, "operations": [{"id": "timeline-op-1"}]},
                project_revision=4,
            )
            self.assertEqual(controller.plan.status, "waiting_approval")

            gui._project_revision = 5
            self.assertTrue(
                controller.proposal_resolved(
                    "propose_timeline_edit",
                    applied=True,
                    project_revision=5,
                )
            )
            self.assertEqual(controller.plan.status, "running")
            self.assertEqual(gui.calls[-1], ("render", {}))

            gui._running = False
            gui._processing_progress.status = "completed"
            self.assertTrue(
                controller.job_terminal(
                    "render",
                    "completed",
                    project_revision=5,
                    job_id="render-job-1",
                )
            )
            self.assertEqual(controller.plan.status, "success")
            self.assertEqual(review_outputs, [])
            self.assertTrue(all(client.thread_calls for client in clients))
            self.assertTrue(all(client.turn_calls for client in clients))
            self.assertTrue(
                all(
                    call["sandbox_policy"] == {"type": "readOnly", "networkAccess": False}
                    for client in clients
                    for call in client.turn_calls
                )
            )


if __name__ == "__main__":
    unittest.main()
