from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from .codex_actions import ActionDispatcher, ActionScope, ActionStatus


PLAN_SCHEMA_VERSION = 1
TERMINAL_STEP_STATUSES = frozenset({"success", "failed", "canceled", "stale"})
TERMINAL_PLAN_STATUSES = frozenset({"success", "failed", "canceled", "stale"})
RENDER_ACTIONS = frozenset({"render_normal", "render_short"})


class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"
    STALE = "stale"


VALID_STATUSES = frozenset(item.value for item in PlanStatus)


class PlanError(ValueError):
    pass


@dataclass
class PlanStep:
    id: str
    domain: str
    action_kind: str
    action_type: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = PlanStatus.PENDING.value
    result: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "domain": self.domain,
            "action_kind": self.action_kind,
            "action_type": self.action_type,
            "args": deepcopy(self.args),
            "status": self.status,
        }
        if self.result is not None:
            payload["result"] = deepcopy(self.result)
        return payload


@dataclass
class PlanState:
    goal: str
    scope: tuple[str, ...]
    scope_id: str
    project_revision: int
    steps: list[PlanStep]
    status: str = PlanStatus.PENDING.value
    blocking_issues: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    schema_version: int = PLAN_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "goal": self.goal,
            "scope": list(self.scope),
            "scope_id": self.scope_id,
            "project_revision": self.project_revision,
            "status": self.status,
            "steps": [step.to_json() for step in self.steps],
            "blocking_issues": deepcopy(self.blocking_issues),
            "message": self.message,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PlanState":
        if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
            raise PlanError("unsupported plan schema_version")
        scope = payload.get("scope")
        raw_steps = payload.get("steps")
        revision = payload.get("project_revision")
        if not isinstance(scope, list) or not all(isinstance(item, str) for item in scope):
            raise PlanError("plan scope must be a string array")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlanError("plan steps must be a non-empty array")
        if type(revision) is not int or revision < 0:
            raise PlanError("plan project_revision must be a non-negative integer")
        if not str(payload.get("goal", "")).strip() or not str(payload.get("scope_id", "")).strip():
            raise PlanError("plan goal and scope_id must not be empty")
        if payload.get("status", PlanStatus.PENDING.value) not in VALID_STATUSES:
            raise PlanError("plan status is invalid")
        steps: list[PlanStep] = []
        for raw in raw_steps:
            if not isinstance(raw, Mapping):
                raise PlanError("plan step must be an object")
            args = raw.get("args", {})
            if not isinstance(args, Mapping):
                raise PlanError("plan step args must be an object")
            if not all(str(raw.get(name, "")).strip() for name in ("id", "domain", "action_kind", "action_type")):
                raise PlanError("plan step identity fields must not be empty")
            if raw.get("status", PlanStatus.PENDING.value) not in VALID_STATUSES:
                raise PlanError("plan step status is invalid")
            steps.append(
                PlanStep(
                    id=str(raw.get("id", "")),
                    domain=str(raw.get("domain", "")),
                    action_kind=str(raw.get("action_kind", "")),
                    action_type=str(raw.get("action_type", "")),
                    args=deepcopy(dict(args)),
                    status=str(raw.get("status", PlanStatus.PENDING.value)),
                    result=deepcopy(dict(raw["result"])) if isinstance(raw.get("result"), Mapping) else None,
                )
            )
        return cls(
            goal=str(payload.get("goal", "")),
            scope=tuple(scope),
            scope_id=str(payload.get("scope_id", "")),
            project_revision=revision,
            steps=steps,
            status=str(payload.get("status", PlanStatus.PENDING.value)),
            blocking_issues=deepcopy(list(payload.get("blocking_issues", []))),
            message=str(payload.get("message", "")),
        )


def build_completion_plan(
    *,
    goal: str,
    scope: Sequence[str],
    scope_id: str,
    project_revision: int,
    current_state: Mapping[str, Any],
) -> PlanState:
    """Build only the requested multi-step workflow, skipping completed work."""
    ordered_scope = tuple(dict.fromkeys(str(item) for item in scope))
    if len(ordered_scope) < 2:
        raise PlanError("single-action requests must not create a PlanState")
    supported = {"transcription", "subtitle", "audio", "timeline", "render", "short_render"}
    unknown = sorted(set(ordered_scope) - supported)
    if unknown:
        raise PlanError("unsupported plan scope: " + ", ".join(unknown))

    steps: list[PlanStep] = []
    if "transcription" in ordered_scope and not bool(current_state.get("subtitles_ready")):
        steps.append(PlanStep("transcribe", "transcription", "execute", "start_transcription", {"mode": "merge"}))
    if "subtitle" in ordered_scope and not bool(current_state.get("subtitle_reviewed")):
        steps.append(
            PlanStep(
                "subtitle_review",
                "subtitle",
                "propose",
                "propose_subtitle_edit",
                {"intent": "完成品質を確認して必要な字幕修正を提案", "selection_scope": "all"},
            )
        )
    if "audio" in ordered_scope and not bool(current_state.get("audio_reviewed")):
        steps.append(
            PlanStep("audio_review", "audio", "propose", "propose_audio_mix", {"intent": "完成品質に調整"})
        )
    if "timeline" in ordered_scope and not bool(current_state.get("timeline_reviewed")):
        steps.append(
            PlanStep(
                "timeline_review",
                "timeline",
                "propose",
                "propose_timeline_edit",
                {"intent": "完成構成に調整"},
            )
        )
    if "render" in ordered_scope and not bool(current_state.get("render_complete")):
        steps.append(PlanStep("render", "render", "execute", "render_normal", {"overwrite": False}))
    if "short_render" in ordered_scope and not bool(current_state.get("short_render_complete")):
        steps.append(PlanStep("render_short", "short_render", "execute", "render_short", {"overwrite": False}))
    if not steps:
        raise PlanError("requested goal is already complete")
    return PlanState(goal, ordered_scope, scope_id, project_revision, steps)


class CodexOrchestrator:
    """Advance a persisted plan exclusively through the typed Action dispatcher."""

    def __init__(self, dispatcher: ActionDispatcher, trusted_scope: ActionScope) -> None:
        self._dispatcher = dispatcher
        self._scope = trusted_scope

    def advance(self, plan: PlanState) -> PlanState:
        if plan.scope_id != self._scope.id:
            raise PlanError("plan scope does not match trusted scope")
        if plan.status in TERMINAL_PLAN_STATUSES or plan.status == PlanStatus.PAUSED.value:
            return plan
        if plan.status == PlanStatus.WAITING_APPROVAL.value or any(
            step.status == PlanStatus.RUNNING.value for step in plan.steps
        ):
            return plan
        if not self._refresh(plan):
            return plan
        step = next((item for item in plan.steps if item.status == PlanStatus.PENDING.value), None)
        if step is None:
            plan.status = PlanStatus.SUCCESS.value
            plan.message = "plan completed"
            return plan
        if step.action_type in RENDER_ACTIONS and plan.blocking_issues:
            plan.status = PlanStatus.PAUSED.value
            plan.message = "blocking issues must be resolved before render"
            return plan

        request = {
            "schema_version": 1,
            "kind": step.action_kind,
            "type": step.action_type,
            "args": deepcopy(step.args),
            "scope_id": plan.scope_id,
        }
        if step.action_kind != "inspect":
            request["project_revision"] = plan.project_revision
        result = self._dispatcher.dispatch(request, trusted_scope=self._scope)
        step.result = result.to_json()
        plan.project_revision = result.revision if result.revision is not None else plan.project_revision
        if result.status is not ActionStatus.SUCCESS:
            step.status = PlanStatus.STALE.value if result.code == "stale_revision" else PlanStatus.FAILED.value
            plan.status = step.status
            plan.message = result.message
        elif result.proposal is not None:
            step.status = PlanStatus.WAITING_APPROVAL.value
            plan.status = PlanStatus.WAITING_APPROVAL.value
            plan.message = "proposal approval is required"
        elif result.job is not None and str(result.job.get("status")) not in {"success", "completed"}:
            step.status = PlanStatus.RUNNING.value
            plan.status = PlanStatus.RUNNING.value
            plan.message = "job is running"
        else:
            step.status = PlanStatus.SUCCESS.value
            plan.status = PlanStatus.RUNNING.value
            plan.message = result.message
        return plan

    def resolve_proposal(self, plan: PlanState, *, applied: bool, project_revision: int) -> PlanState:
        step = next((item for item in plan.steps if item.status == PlanStatus.WAITING_APPROVAL.value), None)
        if step is None:
            raise PlanError("plan has no proposal waiting for approval")
        step.status = PlanStatus.SUCCESS.value if applied else PlanStatus.CANCELED.value
        plan.project_revision = project_revision
        plan.status = PlanStatus.PENDING.value
        plan.message = "proposal applied" if applied else "proposal discarded"
        self._refresh(plan)
        return plan

    def resolve_job(self, plan: PlanState, *, status: str, project_revision: int) -> PlanState:
        step = next((item for item in plan.steps if item.status == PlanStatus.RUNNING.value), None)
        if step is None:
            raise PlanError("plan has no running job")
        normalized = {"completed": "success", "cancelled": "canceled", "error": "failed"}.get(status, status)
        if normalized not in TERMINAL_STEP_STATUSES:
            raise PlanError("job result must be terminal")
        step.status = normalized
        plan.project_revision = project_revision
        plan.status = PlanStatus.PENDING.value if normalized == "success" else normalized
        plan.message = f"job {normalized}"
        if normalized == "success":
            self._refresh(plan)
        return plan

    def pause(self, plan: PlanState) -> PlanState:
        if plan.status not in TERMINAL_PLAN_STATUSES:
            if not self._stop_running_step(plan):
                return plan
            plan.status = PlanStatus.PAUSED.value
            plan.message = "paused by user"
        return plan

    def resume(self, plan: PlanState, *, project_revision: int) -> PlanState:
        if plan.status != PlanStatus.PAUSED.value:
            raise PlanError("only a paused plan can resume")
        plan.project_revision = project_revision
        plan.status = PlanStatus.PENDING.value
        plan.message = ""
        self._refresh(plan)
        return plan

    def cancel(self, plan: PlanState) -> PlanState:
        if plan.status in TERMINAL_PLAN_STATUSES:
            return plan
        if not self._stop_running_step(plan):
            return plan
        plan.status = PlanStatus.CANCELED.value
        plan.message = "canceled by user"
        return plan

    def reinspect(self, plan: PlanState) -> PlanState:
        """Rebase a stale plan only after reading the actual backend revision."""
        result = self._dispatcher.dispatch(
            {
                "schema_version": 1,
                "kind": "inspect",
                "type": "inspect_project_state",
                "args": {},
                "scope_id": plan.scope_id,
            },
            trusted_scope=self._scope,
        )
        if result.status is not ActionStatus.SUCCESS:
            plan.status = PlanStatus.FAILED.value
            plan.message = result.message
            return plan
        if result.revision is not None:
            plan.project_revision = result.revision
        for step in plan.steps:
            if step.status == PlanStatus.STALE.value:
                step.status = PlanStatus.PENDING.value
                step.result = None
        plan.status = PlanStatus.PENDING.value
        plan.message = "plan re-inspected after external project changes"
        return plan

    def set_blocking_issues(self, plan: PlanState, issues: Sequence[Mapping[str, Any]]) -> PlanState:
        plan.blocking_issues = [deepcopy(dict(issue)) for issue in issues]
        return plan

    def _stop_running_step(self, plan: PlanState) -> bool:
        step = next((item for item in plan.steps if item.status == PlanStatus.RUNNING.value), None)
        if step is None:
            return True
        job = step.result.get("job", {}) if step.result else {}
        job_type = str(job.get("job_type") or job.get("type") or "")
        args = {"job_type": job_type} if job_type else {}
        result = self._dispatcher.dispatch(
            {
                "schema_version": 1,
                "kind": "execute",
                "type": "cancel_processing",
                "args": args,
                "scope_id": plan.scope_id,
            },
            trusted_scope=self._scope,
        )
        if result.status is not ActionStatus.SUCCESS:
            plan.status = PlanStatus.FAILED.value
            plan.message = result.message
            return False
        step.status = PlanStatus.CANCELED.value
        step.result = result.to_json()
        return True

    def _refresh(self, plan: PlanState) -> bool:
        result = self._dispatcher.dispatch(
            {
                "schema_version": 1,
                "kind": "inspect",
                "type": "inspect_project_state",
                "args": {},
                "scope_id": plan.scope_id,
            },
            trusted_scope=self._scope,
        )
        if result.status is not ActionStatus.SUCCESS:
            plan.status = PlanStatus.FAILED.value
            plan.message = result.message
            return False
        actual_revision = result.revision if result.revision is not None else plan.project_revision
        if actual_revision != plan.project_revision:
            plan.status = PlanStatus.STALE.value
            plan.message = "project changed outside the plan; re-inspection is required"
            for step in plan.steps:
                if step.status in {PlanStatus.PENDING.value, PlanStatus.WAITING_APPROVAL.value}:
                    step.status = PlanStatus.STALE.value
            return False
        return True
