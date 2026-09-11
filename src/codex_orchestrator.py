from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .codex_actions import ActionDispatcher, ActionScope, ActionStatus


PLAN_SCHEMA_VERSION = 1
TERMINAL_PLAN_STATUSES = frozenset({"success", "failed", "canceled"})
RENDER_ACTIONS = frozenset({"render_normal", "render_short"})
REVIEW_CATEGORIES = frozenset(
    {"project", "subtitle", "audio", "timeline", "short", "processing", "render"}
)
SUPPORTED_PLAN_SCOPES = frozenset(
    {
        "transcription",
        "subtitle",
        "audio",
        "timeline",
        "short_timeline",
        "render",
        "short_render",
    }
)
_COMPLETION_PATTERNS = (
    re.compile(r"(?:動画|ビデオ|ショート|YouTube|ユーチューブ|short).{0,24}(?:完成|仕上げ|公開)"),
    re.compile(r"\b(?:complete|finish)\b.{0,32}\b(?:video|movie|short)\b", re.IGNORECASE),
    re.compile(r"\b(?:video|movie|short)\b.{0,32}\b(?:complete|finished|publish)\b", re.IGNORECASE),
)


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


class PlanPersistenceError(PlanError):
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
    phase: str = ""
    issue_ids: tuple[str, ...] = ()

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
        if self.phase:
            payload["phase"] = self.phase
        if self.issue_ids:
            payload["issue_ids"] = list(self.issue_ids)
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
    review_result: dict[str, Any] | None = None
    handled_issue_ids: list[str] = field(default_factory=list)
    rendered_targets: list[str] = field(default_factory=list)
    final_review_revision: int | None = None
    project_fingerprint: str = ""
    last_project_state: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "goal": self.goal,
            "scope": list(self.scope),
            "scope_id": self.scope_id,
            "project_revision": self.project_revision,
            "status": self.status,
            "steps": [step.to_json() for step in self.steps],
            "blocking_issues": deepcopy(self.blocking_issues),
            "message": self.message,
            "handled_issue_ids": list(self.handled_issue_ids),
            "rendered_targets": list(self.rendered_targets),
            "last_project_state": deepcopy(self.last_project_state),
        }
        if self.review_result is not None:
            payload["review_result"] = deepcopy(self.review_result)
        if self.final_review_revision is not None:
            payload["final_review_revision"] = self.final_review_revision
        if self.project_fingerprint:
            payload["project_fingerprint"] = self.project_fingerprint
        return payload

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "PlanState":
        if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
            raise PlanError("unsupported plan schema_version")
        scope = payload.get("scope")
        raw_steps = payload.get("steps")
        revision = payload.get("project_revision")
        if not isinstance(scope, list) or not all(isinstance(item, str) for item in scope):
            raise PlanError("plan scope must be a string array")
        normalized_scope = tuple(dict.fromkeys(item.strip() for item in scope if item.strip()))
        if len(normalized_scope) < 2 or set(normalized_scope) - SUPPORTED_PLAN_SCOPES:
            raise PlanError("plan scope is invalid")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlanError("plan steps must be a non-empty array")
        if type(revision) is not int or revision < 0:
            raise PlanError("plan project_revision must be a non-negative integer")
        if not str(payload.get("goal", "")).strip() or not str(payload.get("scope_id", "")).strip():
            raise PlanError("plan goal and scope_id must not be empty")
        status = payload.get("status", PlanStatus.PENDING.value)
        if status not in VALID_STATUSES:
            raise PlanError("plan status is invalid")
        steps = [_step_from_json(raw) for raw in raw_steps]
        step_ids = [step.id for step in steps]
        if len(step_ids) != len(set(step_ids)):
            raise PlanError("plan step ids must be unique")
        blocking = _mapping_list(payload.get("blocking_issues", []), "blocking_issues")
        handled = _string_list(payload.get("handled_issue_ids", []), "handled_issue_ids")
        rendered = _string_list(payload.get("rendered_targets", []), "rendered_targets")
        if set(rendered) - {"normal", "short"}:
            raise PlanError("rendered_targets contains an unsupported target")
        final_revision = payload.get("final_review_revision")
        if final_revision is not None and (type(final_revision) is not int or final_revision < 0):
            raise PlanError("final_review_revision must be a non-negative integer")
        review_result = payload.get("review_result")
        if review_result is not None and not isinstance(review_result, Mapping):
            raise PlanError("review_result must be an object")
        last_state = payload.get("last_project_state", {})
        if not isinstance(last_state, Mapping):
            raise PlanError("last_project_state must be an object")
        return cls(
            goal=str(payload.get("goal", "")),
            scope=normalized_scope,
            scope_id=str(payload.get("scope_id", "")),
            project_revision=revision,
            steps=steps,
            status=str(status),
            blocking_issues=blocking,
            message=str(payload.get("message", "")),
            review_result=deepcopy(dict(review_result)) if isinstance(review_result, Mapping) else None,
            handled_issue_ids=handled,
            rendered_targets=rendered,
            final_review_revision=final_revision,
            project_fingerprint=str(payload.get("project_fingerprint", "")),
            last_project_state=_safe_project_state(last_state),
        )


def _step_from_json(raw: object) -> PlanStep:
    if not isinstance(raw, Mapping):
        raise PlanError("plan step must be an object")
    args = raw.get("args", {})
    if not isinstance(args, Mapping):
        raise PlanError("plan step args must be an object")
    if not all(str(raw.get(name, "")).strip() for name in ("id", "domain", "action_kind", "action_type")):
        raise PlanError("plan step identity fields must not be empty")
    status = raw.get("status", PlanStatus.PENDING.value)
    if status not in VALID_STATUSES:
        raise PlanError("plan step status is invalid")
    issue_ids = _string_list(raw.get("issue_ids", []), "step issue_ids")
    result = raw.get("result")
    if result is not None and not isinstance(result, Mapping):
        raise PlanError("plan step result must be an object")
    return PlanStep(
        id=str(raw.get("id", "")),
        domain=str(raw.get("domain", "")),
        action_kind=str(raw.get("action_kind", "")),
        action_type=str(raw.get("action_type", "")),
        args=deepcopy(dict(args)),
        status=str(status),
        result=deepcopy(dict(result)) if isinstance(result, Mapping) else None,
        phase=str(raw.get("phase", "")),
        issue_ids=tuple(issue_ids),
    )


def _mapping_list(value: object, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise PlanError(f"{field_name} must be an object array")
    return [deepcopy(dict(item)) for item in value]


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PlanError(f"{field_name} must be a string array")
    if len(value) != len(set(value)):
        raise PlanError(f"{field_name} must not contain duplicates")
    return list(value)


def _safe_project_state(value: Mapping[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for name in ("loaded", "dirty", "has_video", "render_complete", "short_render_complete"):
        if name in value:
            state[name] = bool(value[name])
    for name in ("revision", "segment_count"):
        item = value.get(name)
        if type(item) is int and item >= 0:
            state[name] = item
    return state


def project_state_fingerprint(project: Mapping[str, Any] | None) -> str:
    """Return a stable content fingerprint used only to validate a restored plan."""

    payload = deepcopy(dict(project or {}))
    payload.pop("updated_at", None)
    payload.pop("codex_plan_state", None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def completion_scope_for_request(goal: str) -> tuple[str, ...] | None:
    """Recognize explicit completion goals without broadening ordinary chat requests."""

    text = str(goal).strip()
    if not text or not any(pattern.search(text) for pattern in _COMPLETION_PATTERNS):
        return None
    if re.search(r"(?:ショート|縦動画|\bshort\b)", text, re.IGNORECASE):
        return ("transcription", "subtitle", "audio", "short_timeline", "short_render")
    return ("transcription", "subtitle", "audio", "timeline", "render")


def allowed_actions_for_scope(scope: Sequence[str]) -> frozenset[str]:
    normalized = set(scope)
    allowed = {"inspect_project_state", "review_project"}
    if "transcription" in normalized:
        allowed.update({"start_transcription", "inspect_processing_state", "cancel_processing"})
    if "subtitle" in normalized:
        allowed.add("propose_subtitle_edit")
    if "audio" in normalized:
        allowed.add("propose_audio_mix")
    if normalized.intersection({"timeline", "short_timeline"}):
        allowed.add("propose_timeline_edit")
    if "render" in normalized:
        allowed.update({"render_normal", "inspect_processing_state", "cancel_processing"})
    if "short_render" in normalized:
        allowed.update({"render_short", "inspect_processing_state", "cancel_processing"})
    return frozenset(allowed)


def build_completion_plan(
    *,
    goal: str,
    scope: Sequence[str],
    scope_id: str,
    project_revision: int,
    current_state: Mapping[str, Any],
) -> PlanState:
    """Create a review-first plan; later steps come only from fresh recommendations."""

    ordered_scope = tuple(dict.fromkeys(str(item).strip() for item in scope if str(item).strip()))
    if "short_render" in ordered_scope and "render" not in ordered_scope and "timeline" in ordered_scope:
        ordered_scope = tuple("short_timeline" if item == "timeline" else item for item in ordered_scope)
    if len(ordered_scope) < 2:
        raise PlanError("single-action requests must not create a PlanState")
    unknown = sorted(set(ordered_scope) - SUPPORTED_PLAN_SCOPES)
    if unknown:
        raise PlanError("unsupported plan scope: " + ", ".join(unknown))
    if type(project_revision) is not int or project_revision < 0:
        raise PlanError("project_revision must be a non-negative integer")
    if not str(goal).strip() or not str(scope_id).strip():
        raise PlanError("goal and scope_id must not be empty")

    rendered_targets: list[str] = []
    if "render" in ordered_scope and bool(current_state.get("render_complete")):
        rendered_targets.append("normal")
    if "short_render" in ordered_scope and bool(current_state.get("short_render_complete")):
        rendered_targets.append("short")
    return PlanState(
        goal=str(goal).strip(),
        scope=ordered_scope,
        scope_id=str(scope_id),
        project_revision=project_revision,
        steps=[PlanStep("initial_review", "review", "inspect", "review_project", phase="initial")],
        rendered_targets=rendered_targets,
        last_project_state=_safe_project_state(current_state),
    )


class CodexOrchestrator:
    """Advance a persisted plan exclusively through the typed Action dispatcher."""

    def __init__(self, dispatcher: ActionDispatcher, trusted_scope: ActionScope) -> None:
        self._dispatcher = dispatcher
        self._scope = trusted_scope

    def advance(self, plan: PlanState) -> PlanState:
        self._validate_plan_scope(plan)
        if plan.status in TERMINAL_PLAN_STATUSES or plan.status == PlanStatus.PAUSED.value:
            return plan
        waiting = next(
            (step for step in plan.steps if step.status == PlanStatus.WAITING_APPROVAL.value),
            None,
        )
        if waiting is not None:
            plan.status = PlanStatus.WAITING_APPROVAL.value
            plan.message = "proposal approval is required"
            return plan
        if any(step.status == PlanStatus.RUNNING.value for step in plan.steps):
            plan.status = PlanStatus.RUNNING.value
            return plan
        if not self._refresh(plan):
            return plan
        step = next((item for item in plan.steps if item.status == PlanStatus.PENDING.value), None)
        if step is None:
            self._queue_review(plan, "final")
            step = next(item for item in plan.steps if item.status == PlanStatus.PENDING.value)
        if step.action_type in RENDER_ACTIONS:
            if plan.blocking_issues:
                plan.status = PlanStatus.PAUSED.value
                plan.message = "blocking issues must be resolved before render"
                return plan
            if plan.final_review_revision != plan.project_revision:
                step.status = PlanStatus.STALE.value
                self._queue_review(plan, "final")
                plan.status = PlanStatus.PENDING.value
                plan.message = "a fresh final review is required before render"
                return plan

        result = self._dispatcher.dispatch(self._request(plan, step), trusted_scope=self._scope)
        step.result = result.to_json()
        if result.status is not ActionStatus.SUCCESS:
            step.status = PlanStatus.STALE.value if result.code == "stale_revision" else PlanStatus.FAILED.value
            plan.status = step.status
            plan.message = result.message
            return plan
        if result.revision is not None:
            plan.project_revision = result.revision
        if step.action_type == "review_project":
            return self._complete_review(plan, step, result.current_state)
        if step.action_kind == "propose":
            return self._handle_proposal_result(plan, step, result.to_json())
        return self._handle_execute_result(plan, step, result.to_json())

    def proposal_ready(
        self,
        plan: PlanState,
        *,
        action_type: str,
        proposal: Mapping[str, Any],
        project_revision: int,
    ) -> PlanState:
        step = next(
            (
                item
                for item in plan.steps
                if item.status == PlanStatus.RUNNING.value
                and item.action_kind == "propose"
                and item.action_type == action_type
            ),
            None,
        )
        if step is None:
            raise PlanError("plan has no matching proposal generation step")
        if project_revision != plan.project_revision:
            self._mark_stale(plan, "project changed while proposal was generated")
            return plan
        payload = dict(step.result or {})
        payload["proposal"] = deepcopy(dict(proposal))
        step.result = payload
        step.status = PlanStatus.WAITING_APPROVAL.value
        if plan.status != PlanStatus.PAUSED.value:
            plan.status = PlanStatus.WAITING_APPROVAL.value
        plan.message = "proposal approval is required"
        return plan

    def resolve_proposal(
        self,
        plan: PlanState,
        *,
        applied: bool,
        project_revision: int,
        action_type: str | None = None,
    ) -> PlanState:
        step = next(
            (
                item
                for item in plan.steps
                if item.status == PlanStatus.WAITING_APPROVAL.value
                and (action_type is None or item.action_type == action_type)
            ),
            None,
        )
        if step is None:
            raise PlanError("plan has no proposal waiting for approval")
        was_paused = plan.status == PlanStatus.PAUSED.value
        step.status = PlanStatus.SUCCESS.value if applied else PlanStatus.CANCELED.value
        resolution = dict(step.result or {})
        resolution["resolution"] = "applied" if applied else "discarded"
        step.result = resolution
        self._remember_issues(plan, step.issue_ids)
        if applied:
            plan.rendered_targets.clear()
            plan.final_review_revision = None
        plan.project_revision = _valid_revision(project_revision)
        plan.status = PlanStatus.PAUSED.value if was_paused else PlanStatus.PENDING.value
        plan.message = "proposal applied" if applied else "proposal discarded"
        if not self._refresh(plan):
            return plan
        self._queue_review(plan, "after_action")
        if was_paused:
            plan.status = PlanStatus.PAUSED.value
        return plan

    def resolve_job(self, plan: PlanState, *, status: str, project_revision: int) -> PlanState:
        step = next(
            (
                item
                for item in plan.steps
                if item.status == PlanStatus.RUNNING.value and item.action_kind == "execute"
            ),
            None,
        )
        if step is None:
            raise PlanError("plan has no running job")
        normalized = {"completed": "success", "cancelled": "canceled", "error": "failed"}.get(status, status)
        if normalized not in {"success", "failed", "canceled"}:
            raise PlanError("job result must be terminal")
        step.status = normalized
        result = dict(step.result or {})
        result["terminal_status"] = normalized
        step.result = result
        self._remember_issues(plan, step.issue_ids)
        plan.project_revision = _valid_revision(project_revision)
        plan.status = PlanStatus.PENDING.value
        plan.message = f"job {normalized}"
        if normalized == "success":
            if step.action_type in RENDER_ACTIONS:
                self._remember_rendered_target(plan, step.action_type)
            else:
                plan.rendered_targets.clear()
                plan.final_review_revision = None
        if not self._refresh(plan):
            return plan
        if normalized == "success":
            phase = "post_render" if step.action_type in RENDER_ACTIONS else "after_action"
        else:
            phase = "after_failed_action"
        self._queue_review(plan, phase)
        return plan

    def fail_running_action(self, plan: PlanState, *, action_type: str, message: str) -> PlanState:
        step = next(
            (
                item
                for item in plan.steps
                if item.status == PlanStatus.RUNNING.value and item.action_type == action_type
            ),
            None,
        )
        if step is None:
            return plan
        step.status = PlanStatus.FAILED.value
        self._remember_issues(plan, step.issue_ids)
        plan.status = PlanStatus.PENDING.value
        plan.message = str(message)
        self._queue_review(plan, "after_failed_action")
        return plan

    def pause(self, plan: PlanState) -> PlanState:
        if plan.status in TERMINAL_PLAN_STATUSES or plan.status == PlanStatus.PAUSED.value:
            return plan
        running = next((item for item in plan.steps if item.status == PlanStatus.RUNNING.value), None)
        if running is not None:
            if running.action_kind == "execute":
                if not self._stop_running_job(plan, running):
                    return plan
            else:
                running.status = PlanStatus.CANCELED.value
                running.result = {**dict(running.result or {}), "resolution": "generation_stopped"}
            self._queue_review(plan, "after_action")
        plan.status = PlanStatus.PAUSED.value
        plan.message = "paused by user"
        return plan

    def resume(self, plan: PlanState, *, project_revision: int) -> PlanState:
        if plan.status != PlanStatus.PAUSED.value:
            raise PlanError("only a paused plan can resume")
        actual_revision = _valid_revision(project_revision)
        if actual_revision != plan.project_revision:
            self._mark_stale(plan, "project changed while the plan was paused")
            return plan
        if not self._refresh(plan):
            return plan
        if any(step.status == PlanStatus.WAITING_APPROVAL.value for step in plan.steps):
            plan.status = PlanStatus.WAITING_APPROVAL.value
            plan.message = "proposal approval is required"
        elif any(step.status == PlanStatus.RUNNING.value for step in plan.steps):
            plan.status = PlanStatus.RUNNING.value
            plan.message = "action is running"
        else:
            plan.status = PlanStatus.PENDING.value
            plan.message = ""
        return plan

    def cancel(self, plan: PlanState) -> PlanState:
        if plan.status in TERMINAL_PLAN_STATUSES:
            return plan
        running = next((item for item in plan.steps if item.status == PlanStatus.RUNNING.value), None)
        if running is not None and running.action_kind == "execute" and not self._stop_running_job(plan, running):
            return plan
        for step in plan.steps:
            if step.status in {
                PlanStatus.PENDING.value,
                PlanStatus.RUNNING.value,
                PlanStatus.WAITING_APPROVAL.value,
            }:
                step.status = PlanStatus.CANCELED.value
        plan.status = PlanStatus.CANCELED.value
        plan.message = "canceled by user"
        return plan

    def reinspect(self, plan: PlanState) -> PlanState:
        """Discard stale future work and rebuild it from a new project review."""

        result = self._inspect("inspect_project_state", plan)
        if result.status is not ActionStatus.SUCCESS:
            plan.status = PlanStatus.FAILED.value
            plan.message = result.message
            return plan
        if result.revision is not None:
            plan.project_revision = result.revision
        if result.current_state is not None:
            plan.last_project_state = _safe_project_state(result.current_state)
        self._discard_unfinished_steps(plan)
        plan.rendered_targets.clear()
        plan.final_review_revision = None
        plan.blocking_issues.clear()
        plan.status = PlanStatus.PENDING.value
        plan.message = "plan re-inspected after project changes"
        self._queue_review(plan, "recovery")
        return plan

    def recover(self, plan: PlanState, *, same_project_state: bool) -> PlanState:
        """Reconcile a persisted plan with the real backend after application restart."""

        self._validate_plan_scope(plan)
        result = self._inspect("inspect_project_state", plan)
        if result.status is not ActionStatus.SUCCESS:
            plan.status = PlanStatus.FAILED.value
            plan.message = result.message
            return plan
        actual_revision = result.revision if result.revision is not None else plan.project_revision
        plan.last_project_state = _safe_project_state(result.current_state or {})
        plan.project_revision = actual_revision
        if plan.status in TERMINAL_PLAN_STATUSES:
            return plan
        if not same_project_state:
            self._discard_unfinished_steps(plan)
            plan.rendered_targets.clear()
            plan.final_review_revision = None
            plan.blocking_issues.clear()
            plan.status = PlanStatus.PENDING.value
            plan.message = "project changed while the application was closed"
            self._queue_review(plan, "recovery")
            return plan

        waiting = next(
            (step for step in plan.steps if step.status == PlanStatus.WAITING_APPROVAL.value),
            None,
        )
        if waiting is not None:
            if plan.status != PlanStatus.PAUSED.value:
                plan.status = PlanStatus.WAITING_APPROVAL.value
            return plan
        running = next((step for step in plan.steps if step.status == PlanStatus.RUNNING.value), None)
        if running is not None and running.action_kind == "execute":
            processing = self._inspect("inspect_processing_state", plan)
            state = processing.current_state if isinstance(processing.current_state, Mapping) else {}
            backend_status = str(state.get("terminal_result") or state.get("status") or "")
            if bool(state.get("running")) or backend_status in {"running", "cancelling"}:
                plan.status = PlanStatus.RUNNING.value
                plan.message = "running job restored from backend state"
                return plan
            if backend_status in {"completed", "success", "cancelled", "canceled", "error", "failed"}:
                return self.resolve_job(plan, status=backend_status, project_revision=actual_revision)
        if running is not None and running.action_kind == "propose":
            running.status = PlanStatus.STALE.value
            self._queue_review(plan, "recovery")
            plan.message = "proposal generation was interrupted by application restart"
            if plan.status != PlanStatus.PAUSED.value:
                plan.status = PlanStatus.PENDING.value
            return plan
        if plan.status == PlanStatus.PAUSED.value:
            plan.message = "paused plan restored"
            return plan

        self._discard_unfinished_steps(plan)
        plan.status = PlanStatus.PENDING.value
        plan.message = "plan restored and queued for a fresh review"
        self._queue_review(plan, "recovery")
        return plan

    def set_blocking_issues(self, plan: PlanState, issues: Sequence[Mapping[str, Any]]) -> PlanState:
        """Compatibility hook; normal operation refreshes blockers from ReviewResult."""

        plan.blocking_issues = [deepcopy(dict(issue)) for issue in issues]
        return plan

    def _validate_plan_scope(self, plan: PlanState) -> None:
        if plan.scope_id != self._scope.id:
            raise PlanError("plan scope does not match trusted scope")
        expected = allowed_actions_for_scope(plan.scope)
        if not expected.issubset(self._scope.allowed_actions):
            raise PlanError("trusted scope does not cover the persisted plan")
        for step in plan.steps:
            if step.action_type not in expected:
                raise PlanError("persisted plan contains an action outside its scope")

    def _request(self, plan: PlanState, step: PlanStep) -> dict[str, Any]:
        request: dict[str, Any] = {
            "schema_version": 1,
            "kind": step.action_kind,
            "type": step.action_type,
            "args": deepcopy(step.args),
            "scope_id": plan.scope_id,
        }
        if step.action_kind != "inspect":
            request["project_revision"] = plan.project_revision
        return request

    def _inspect(self, action_type: str, plan: PlanState):
        return self._dispatcher.dispatch(
            {
                "schema_version": 1,
                "kind": "inspect",
                "type": action_type,
                "args": {},
                "scope_id": plan.scope_id,
            },
            trusted_scope=self._scope,
        )

    def _refresh(self, plan: PlanState) -> bool:
        result = self._inspect("inspect_project_state", plan)
        if result.status is not ActionStatus.SUCCESS:
            plan.status = PlanStatus.FAILED.value
            plan.message = result.message
            return False
        if result.current_state is not None:
            plan.last_project_state = _safe_project_state(result.current_state)
        actual_revision = result.revision if result.revision is not None else plan.project_revision
        if actual_revision != plan.project_revision:
            self._mark_stale(plan, "project changed outside the plan; re-inspection is required")
            return False
        return True

    def _mark_stale(self, plan: PlanState, message: str) -> None:
        for step in plan.steps:
            if step.status in {
                PlanStatus.PENDING.value,
                PlanStatus.RUNNING.value,
                PlanStatus.WAITING_APPROVAL.value,
            }:
                step.status = PlanStatus.STALE.value
        plan.final_review_revision = None
        plan.status = PlanStatus.STALE.value
        plan.message = message

    def _complete_review(
        self,
        plan: PlanState,
        step: PlanStep,
        action_state: Mapping[str, Any] | None,
    ) -> PlanState:
        try:
            review = _normalized_review_result(action_state, expected_revision=plan.project_revision)
        except PlanError as error:
            step.status = PlanStatus.FAILED.value
            plan.status = PlanStatus.FAILED.value
            plan.message = str(error)
            return plan
        step.status = PlanStatus.SUCCESS.value
        plan.review_result = review
        issues = list(review["issues"])
        plan.blocking_issues = [
            deepcopy(item)
            for item in issues
            if item["severity"] == "blocking" and self._issue_is_in_scope(plan, item)
        ]
        if (
            bool(review.get("truncated"))
            or review.get("complete") is False
            or int(review.get("remaining_count", 0) or 0) > 0
        ):
            plan.blocking_issues.append(
                {
                    "id": "review-incomplete",
                    "category": "project",
                    "severity": "blocking",
                    "target": {},
                    "reason": "レビュー結果が省略されているため完了判定できません",
                    "recommendation": {"available": False, "route": ""},
                }
            )
        recommendation = self._next_recommendation(plan, review)
        if recommendation is not None:
            plan.steps.append(recommendation)
            plan.final_review_revision = None
            plan.status = PlanStatus.PENDING.value
            plan.message = "next step selected from the latest review"
            return plan
        if step.phase == "final":
            if plan.blocking_issues:
                plan.status = PlanStatus.PAUSED.value
                plan.message = "blocking review issues must be resolved before render"
                return plan
            plan.final_review_revision = plan.project_revision
            self._queue_missing_renders(plan)
            if any(item.status == PlanStatus.PENDING.value for item in plan.steps):
                plan.status = PlanStatus.PENDING.value
                plan.message = "final review passed"
            else:
                plan.status = PlanStatus.SUCCESS.value
                plan.message = "plan completed"
            return plan
        if step.phase == "post_render" and not plan.blocking_issues:
            self._queue_missing_renders(plan)
            if any(item.status == PlanStatus.PENDING.value for item in plan.steps):
                plan.status = PlanStatus.PENDING.value
                plan.message = "render reviewed; continuing remaining output"
            else:
                plan.status = PlanStatus.SUCCESS.value
                plan.message = "plan completed"
            return plan
        if step.phase == "after_failed_action":
            plan.status = PlanStatus.FAILED.value
            plan.message = "action failed after a fresh project review"
            return plan
        self._queue_review(plan, "final")
        plan.status = PlanStatus.PENDING.value
        plan.message = "final review queued"
        return plan

    def _next_recommendation(
        self,
        plan: PlanState,
        review: Mapping[str, Any],
    ) -> PlanStep | None:
        issues = {str(item["id"]): item for item in review["issues"]}
        handled = set(plan.handled_issue_ids)
        for issue_id in review["recommended_order"]:
            if issue_id in handled:
                continue
            issue = issues[issue_id]
            if not self._issue_is_in_scope(plan, issue):
                continue
            step = self._step_for_issue(plan, issue)
            if step is not None:
                return step
        return None

    @staticmethod
    def _issue_is_in_scope(plan: PlanState, issue: Mapping[str, Any]) -> bool:
        category = str(issue.get("category", ""))
        if category in {"project", "processing"}:
            return True
        scopes_by_category = {
            "subtitle": {"subtitle"},
            "audio": {"audio"},
            "timeline": {"timeline", "render"},
            "short": {"short_timeline", "short_render"},
            "render": {"render", "short_render"},
        }
        return bool(scopes_by_category.get(category, set()).intersection(plan.scope))

    def _step_for_issue(self, plan: PlanState, issue: Mapping[str, Any]) -> PlanStep | None:
        recommendation = issue["recommendation"]
        if not bool(recommendation.get("available")):
            return None
        route = str(recommendation.get("route", ""))
        category = str(issue["category"])
        reason = str(issue["reason"])
        issue_ids = (str(issue["id"]),)
        if route == "subtitle_proposal" and category == "subtitle" and "subtitle" in plan.scope:
            return PlanStep(
                self._next_step_id(plan, "subtitle_proposal"),
                "subtitle",
                "propose",
                "propose_subtitle_edit",
                {"intent": reason, "selection_scope": "all"},
                issue_ids=issue_ids,
            )
        if route == "audio_mix_proposal" and category == "audio" and "audio" in plan.scope:
            return PlanStep(
                self._next_step_id(plan, "audio_proposal"),
                "audio",
                "propose",
                "propose_audio_mix",
                {"intent": reason},
                issue_ids=issue_ids,
            )
        if route == "timeline_proposal" and category in {"timeline", "short"}:
            target = "short" if category == "short" else "normal"
            required_scope = "short_timeline" if target == "short" else "timeline"
            if required_scope not in plan.scope:
                return None
            return PlanStep(
                self._next_step_id(plan, f"{target}_timeline_proposal"),
                required_scope,
                "propose",
                "propose_timeline_edit",
                {"intent": reason, "target": target},
                issue_ids=issue_ids,
            )
        if (
            route == "processing_action"
            and category == "processing"
            and "transcription" in plan.scope
            and int(plan.last_project_state.get("segment_count", 0)) == 0
        ):
            return PlanStep(
                self._next_step_id(plan, "transcribe"),
                "transcription",
                "execute",
                "start_transcription",
                {"mode": "merge"},
                issue_ids=issue_ids,
            )
        return None

    def _handle_proposal_result(
        self,
        plan: PlanState,
        step: PlanStep,
        result: Mapping[str, Any],
    ) -> PlanState:
        proposal = result.get("proposal")
        state = result.get("current_state")
        if isinstance(proposal, Mapping):
            step.status = PlanStatus.WAITING_APPROVAL.value
            plan.status = PlanStatus.WAITING_APPROVAL.value
            plan.message = "proposal approval is required"
            return plan
        if isinstance(state, Mapping) and str(state.get("status", "")) in {
            "starting",
            "running",
            "generating",
        }:
            step.status = PlanStatus.RUNNING.value
            plan.status = PlanStatus.RUNNING.value
            plan.message = "proposal is being generated"
            return plan
        step.status = PlanStatus.FAILED.value
        plan.status = PlanStatus.FAILED.value
        plan.message = "proposal action completed without a proposal"
        return plan

    def _handle_execute_result(
        self,
        plan: PlanState,
        step: PlanStep,
        result: Mapping[str, Any],
    ) -> PlanState:
        job = result.get("job")
        status = str(job.get("status", "")) if isinstance(job, Mapping) else ""
        if isinstance(job, Mapping) and status not in {"success", "completed"}:
            if status in {"failed", "error", "canceled", "cancelled"}:
                step.status = PlanStatus.RUNNING.value
                return self.resolve_job(plan, status=status, project_revision=plan.project_revision)
            step.status = PlanStatus.RUNNING.value
            plan.status = PlanStatus.RUNNING.value
            plan.message = "job is running"
            return plan
        step.status = PlanStatus.SUCCESS.value
        self._remember_issues(plan, step.issue_ids)
        if step.action_type in RENDER_ACTIONS:
            self._remember_rendered_target(plan, step.action_type)
            phase = "post_render"
        else:
            plan.rendered_targets.clear()
            plan.final_review_revision = None
            phase = "after_action"
        self._queue_review(plan, phase)
        plan.status = PlanStatus.PENDING.value
        plan.message = str(result.get("message", ""))
        return plan

    def _stop_running_job(self, plan: PlanState, step: PlanStep) -> bool:
        job = step.result.get("job", {}) if step.result else {}
        job_type = str(job.get("job_type") or job.get("type") or "") if isinstance(job, Mapping) else ""
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
        self._remember_issues(plan, step.issue_ids)
        return True

    @staticmethod
    def _remember_issues(plan: PlanState, issue_ids: Sequence[str]) -> None:
        for issue_id in issue_ids:
            if issue_id not in plan.handled_issue_ids:
                plan.handled_issue_ids.append(issue_id)

    @staticmethod
    def _remember_rendered_target(plan: PlanState, action_type: str) -> None:
        target = "short" if action_type == "render_short" else "normal"
        if target not in plan.rendered_targets:
            plan.rendered_targets.append(target)

    def _queue_review(self, plan: PlanState, phase: str) -> None:
        if any(
            step.status in {PlanStatus.PENDING.value, PlanStatus.RUNNING.value}
            and step.action_type == "review_project"
            for step in plan.steps
        ):
            return
        prefix = "final_review" if phase == "final" else f"{phase}_review"
        plan.steps.append(
            PlanStep(
                self._next_step_id(plan, prefix),
                "review",
                "inspect",
                "review_project",
                phase=phase,
            )
        )

    def _queue_missing_renders(self, plan: PlanState) -> None:
        targets: list[tuple[str, str, str]] = []
        if "render" in plan.scope and "normal" not in plan.rendered_targets:
            targets.append(("normal", "render", "render_normal"))
        if "short_render" in plan.scope and "short" not in plan.rendered_targets:
            targets.append(("short", "short_render", "render_short"))
        pending_actions = {
            step.action_type
            for step in plan.steps
            if step.status in {PlanStatus.PENDING.value, PlanStatus.RUNNING.value}
        }
        for target, domain, action_type in targets:
            if action_type in pending_actions:
                continue
            plan.steps.append(
                PlanStep(
                    self._next_step_id(plan, f"render_{target}"),
                    domain,
                    "execute",
                    action_type,
                    {"overwrite": False},
                    phase="render",
                )
            )

    @staticmethod
    def _next_step_id(plan: PlanState, prefix: str) -> str:
        used = {step.id for step in plan.steps}
        if prefix not in used:
            return prefix
        index = 2
        while f"{prefix}_{index}" in used:
            index += 1
        return f"{prefix}_{index}"

    @staticmethod
    def _discard_unfinished_steps(plan: PlanState) -> None:
        for step in plan.steps:
            if step.status in {
                PlanStatus.PENDING.value,
                PlanStatus.RUNNING.value,
                PlanStatus.WAITING_APPROVAL.value,
            }:
                step.status = PlanStatus.STALE.value


def _valid_revision(value: object) -> int:
    if type(value) is not int or value < 0:
        raise PlanError("project_revision must be a non-negative integer")
    return value


def _normalized_review_result(
    action_state: Mapping[str, Any] | None,
    *,
    expected_revision: int,
) -> dict[str, Any]:
    if not isinstance(action_state, Mapping):
        raise PlanError("review_project did not return state")
    raw = action_state.get("review_result", action_state)
    if not isinstance(raw, Mapping):
        raise PlanError("review_project did not return ReviewResult")
    revision = raw.get("project_revision")
    if revision != expected_revision:
        raise PlanError("review result is stale")
    raw_issues = raw.get("issues")
    raw_order = raw.get("recommended_order")
    if not isinstance(raw_issues, list) or not isinstance(raw_order, list):
        raise PlanError("review result issues and recommended_order must be arrays")
    issues: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for raw_issue in raw_issues:
        if not isinstance(raw_issue, Mapping):
            raise PlanError("review issue must be an object")
        issue_id = str(raw_issue.get("id", "")).strip()
        category = str(raw_issue.get("category", "")).strip()
        severity = str(raw_issue.get("severity", "")).strip()
        reason = str(raw_issue.get("reason", "")).strip()
        recommendation = raw_issue.get("recommendation")
        target = raw_issue.get("target", {})
        if not issue_id or issue_id in identifiers or category not in REVIEW_CATEGORIES or not reason:
            raise PlanError("review issue identity is invalid")
        if severity not in {"blocking", "warning", "suggestion"}:
            raise PlanError("review issue severity is invalid")
        if not isinstance(recommendation, Mapping) or not isinstance(target, Mapping):
            raise PlanError("review issue target and recommendation must be objects")
        available = recommendation.get("available")
        route = recommendation.get("route")
        if type(available) is not bool or not isinstance(route, str):
            raise PlanError("review recommendation is invalid")
        if available and route not in {
            "subtitle_proposal",
            "audio_mix_proposal",
            "timeline_proposal",
            "processing_action",
        }:
            raise PlanError("review recommendation route is invalid")
        if not available and route:
            raise PlanError("unavailable review recommendation route must be empty")
        identifiers.add(issue_id)
        issues.append(
            {
                "id": issue_id,
                "category": category,
                "severity": severity,
                "target": deepcopy(dict(target)),
                "reason": reason,
                "recommendation": {"available": available, "route": route},
            }
        )
    order = [str(item) for item in raw_order]
    if len(order) != len(set(order)) or set(order) != identifiers:
        raise PlanError("review recommended_order must contain every issue exactly once")
    result: dict[str, Any] = {
        "project_revision": revision,
        "issues": issues,
        "recommended_order": order,
    }
    truncated = raw.get("truncated")
    remaining = raw.get("remaining_count")
    if type(truncated) is not bool or type(remaining) is not int or remaining < 0:
        raise PlanError("review completeness metadata is invalid")
    result["truncated"] = truncated
    result["remaining_count"] = remaining
    if "complete" in raw:
        if type(raw["complete"]) is not bool:
            raise PlanError("review complete flag is invalid")
        result["complete"] = raw["complete"]
    return result


def plan_state_path(project_path: str | Path) -> Path:
    if not str(project_path).strip():
        raise PlanPersistenceError("project path is required")
    path = Path(project_path)
    return path.with_name(f".{path.name}.codex-plan.json")


class PlanStateStore:
    """Persist PlanState independently so plan updates never change project revision."""

    def load(self, project_path: str | Path) -> PlanState | None:
        path = plan_state_path(project_path)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise PlanError("persisted plan must be an object")
            return PlanState.from_json(payload)
        except (OSError, json.JSONDecodeError, PlanError, TypeError, ValueError) as error:
            raise PlanPersistenceError("persisted plan could not be loaded") from error

    def save(self, project_path: str | Path, plan: PlanState) -> Path:
        path = plan_state_path(project_path)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(plan.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise PlanPersistenceError("plan state could not be saved") from error
        return path


class CodexPlanController:
    """Product bridge from chat and GUI lifecycle events to the Plan state machine."""

    def __init__(
        self,
        dispatcher: ActionDispatcher,
        *,
        project_path: Callable[[], str],
        project_revision: Callable[[], int],
        project_state: Callable[[], Mapping[str, Any]],
        project_fingerprint: Callable[[], str],
        on_change: Callable[[PlanState | None], None] | None = None,
        restore_proposal: Callable[[str, Mapping[str, Any]], None] | None = None,
        cancel_proposal: Callable[[str], None] | None = None,
        store: PlanStateStore | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._project_path = project_path
        self._project_revision = project_revision
        self._project_state = project_state
        self._project_fingerprint = project_fingerprint
        self._on_change = on_change
        self._restore_proposal = restore_proposal
        self._cancel_proposal = cancel_proposal
        self._store = store or PlanStateStore()
        self._plan: PlanState | None = None
        self._orchestrator: CodexOrchestrator | None = None

    @property
    def plan(self) -> PlanState | None:
        return self._plan

    def handle_chat_request(self, goal: str) -> bool:
        scope = completion_scope_for_request(goal)
        if scope is None:
            return False
        self.start(goal, scope)
        return True

    def start(self, goal: str, scope: Sequence[str]) -> PlanState:
        if self._plan is not None and self._plan.status not in TERMINAL_PLAN_STATUSES:
            raise PlanError("another completion plan is already active")
        path = self._project_path()
        if not path:
            raise PlanError("a saved project is required before starting a completion plan")
        plan = build_completion_plan(
            goal=goal,
            scope=scope,
            scope_id=f"plan-{uuid4().hex}",
            project_revision=_valid_revision(self._project_revision()),
            current_state=self._project_state(),
        )
        plan.project_fingerprint = self._project_fingerprint()
        self._attach(plan)
        self._publish()
        self.advance()
        return plan

    def advance(self) -> PlanState | None:
        if self._plan is None or self._orchestrator is None:
            return None
        for _ in range(32):
            before = self._plan.to_json()
            self._orchestrator.advance(self._plan)
            self._publish()
            if self._plan.status in {
                PlanStatus.RUNNING.value,
                PlanStatus.WAITING_APPROVAL.value,
                PlanStatus.PAUSED.value,
                PlanStatus.STALE.value,
                *TERMINAL_PLAN_STATUSES,
            }:
                break
            if self._plan.to_json() == before:
                break
        return self._plan

    def proposal_ready(
        self,
        action_type: str,
        proposal: Mapping[str, Any],
        *,
        project_revision: int,
    ) -> bool:
        if self._plan is None or self._orchestrator is None:
            return False
        try:
            self._orchestrator.proposal_ready(
                self._plan,
                action_type=action_type,
                proposal=proposal,
                project_revision=project_revision,
            )
        except PlanError:
            return False
        self._publish()
        return True

    def proposal_resolved(
        self,
        action_type: str,
        *,
        applied: bool,
        project_revision: int,
        auto_advance: bool = True,
    ) -> bool:
        if self._plan is None or self._orchestrator is None:
            return False
        try:
            self._orchestrator.resolve_proposal(
                self._plan,
                action_type=action_type,
                applied=applied,
                project_revision=project_revision,
            )
        except PlanError:
            return False
        self._publish()
        if auto_advance and self._plan.status == PlanStatus.PENDING.value:
            self.advance()
        return True

    def job_terminal(self, job_type: str, status: str, *, project_revision: int) -> bool:
        if self._plan is None or self._orchestrator is None:
            return False
        running = next(
            (
                step
                for step in self._plan.steps
                if step.status == PlanStatus.RUNNING.value and step.action_kind == "execute"
            ),
            None,
        )
        if running is None:
            return False
        expected = {
            "start_transcription": "transcribe",
            "render_normal": "render",
            "render_short": "render_short",
        }.get(running.action_type, "")
        if expected and str(job_type) != expected:
            return False
        self._orchestrator.resolve_job(
            self._plan,
            status=status,
            project_revision=project_revision,
        )
        self._publish()
        if self._plan.status == PlanStatus.PENDING.value:
            self.advance()
        return True

    def action_failed(self, action_type: str, message: str) -> bool:
        if self._plan is None or self._orchestrator is None:
            return False
        before = self._plan.to_json()
        self._orchestrator.fail_running_action(
            self._plan,
            action_type=action_type,
            message=message,
        )
        if self._plan.to_json() == before:
            return False
        self._publish()
        self.advance()
        return True

    def pause(self) -> PlanState | None:
        if self._plan is not None and self._orchestrator is not None:
            proposal_action = next(
                (
                    step.action_type
                    for step in self._plan.steps
                    if step.action_kind == "propose" and step.status == PlanStatus.RUNNING.value
                ),
                "",
            )
            self._orchestrator.pause(self._plan)
            self._publish()
            if proposal_action and self._cancel_proposal is not None:
                self._cancel_proposal(proposal_action)
        return self._plan

    def resume(self) -> PlanState | None:
        if self._plan is not None and self._orchestrator is not None:
            self._orchestrator.resume(
                self._plan,
                project_revision=_valid_revision(self._project_revision()),
            )
            self._publish()
            if self._plan.status == PlanStatus.PENDING.value:
                self.advance()
        return self._plan

    def cancel(self) -> PlanState | None:
        if self._plan is not None and self._orchestrator is not None:
            proposal_action = next(
                (
                    step.action_type
                    for step in self._plan.steps
                    if step.action_kind == "propose"
                    and step.status
                    in {
                        PlanStatus.RUNNING.value,
                        PlanStatus.WAITING_APPROVAL.value,
                    }
                ),
                "",
            )
            self._orchestrator.cancel(self._plan)
            self._publish()
            if proposal_action and self._cancel_proposal is not None:
                self._cancel_proposal(proposal_action)
        return self._plan

    def reinspect(self) -> PlanState | None:
        if self._plan is not None and self._orchestrator is not None:
            self._orchestrator.reinspect(self._plan)
            self._publish()
            if self._plan.status == PlanStatus.PENDING.value:
                self.advance()
        return self._plan

    def project_saved(self) -> PlanState | None:
        if self._plan is None or self._orchestrator is None:
            return self._plan
        stale_proposal = next(
            (
                step
                for step in self._plan.steps
                if step.action_kind == "propose"
                and step.status
                in {
                    PlanStatus.RUNNING.value,
                    PlanStatus.WAITING_APPROVAL.value,
                }
            ),
            None,
        )
        if (
            stale_proposal is not None
            and _valid_revision(self._project_revision()) != self._plan.project_revision
        ):
            self._orchestrator.reinspect(self._plan)
            self._publish()
            if self._cancel_proposal is not None:
                self._cancel_proposal(stale_proposal.action_type)
        if (
            self._plan.status == PlanStatus.PAUSED.value
            and self._plan.blocking_issues
            and "blocking" in self._plan.message
        ):
            self._orchestrator.reinspect(self._plan)
            self._publish()
        if self._plan.status == PlanStatus.PENDING.value:
            return self.advance()
        return self._plan

    def restore(self, *, auto_advance: bool = True) -> PlanState | None:
        path = self._project_path()
        if not path:
            self._attach(None)
            self._publish()
            return None
        plan = self._store.load(path)
        if plan is None:
            self._attach(None)
            self._publish()
            return None
        stored_fingerprint = plan.project_fingerprint
        same_state = bool(stored_fingerprint) and stored_fingerprint == self._project_fingerprint()
        self._attach(plan)
        assert self._orchestrator is not None
        self._orchestrator.recover(plan, same_project_state=same_state)
        self._restore_waiting_proposal(plan)
        self._publish()
        if auto_advance and plan.status == PlanStatus.PENDING.value:
            self.advance()
        return plan

    def clear(self) -> None:
        self._attach(None)
        self._publish()

    def _attach(self, plan: PlanState | None) -> None:
        self._plan = plan
        if plan is None:
            self._orchestrator = None
            return
        trusted = ActionScope(plan.scope_id, allowed_actions_for_scope(plan.scope))
        self._orchestrator = CodexOrchestrator(self._dispatcher, trusted)

    def _restore_waiting_proposal(self, plan: PlanState) -> None:
        if self._restore_proposal is None:
            return
        step = next(
            (item for item in plan.steps if item.status == PlanStatus.WAITING_APPROVAL.value),
            None,
        )
        if step is None or not isinstance(step.result, Mapping):
            return
        proposal = step.result.get("proposal")
        if isinstance(proposal, Mapping):
            restored = deepcopy(dict(proposal))
            if "base_revision" in restored:
                restored["base_revision"] = plan.project_revision
                step.result = {**dict(step.result), "proposal": deepcopy(restored)}
            self._restore_proposal(step.action_type, restored)

    def _publish(self) -> None:
        plan = self._plan
        if plan is not None:
            plan.project_fingerprint = self._project_fingerprint()
            path = self._project_path()
            if path:
                self._store.save(path, plan)
        if self._on_change is not None:
            self._on_change(plan)
