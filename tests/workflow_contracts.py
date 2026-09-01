from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


class WorkflowContractError(ValueError):
    """Raised when a workflow can bypass a required release contract."""


class GitHubActionsLoader(yaml.SafeLoader):
    """YAML 1.2-like loader that keeps GitHub Actions' ``on`` key as text."""


GitHubActionsLoader.yaml_implicit_resolvers = {
    key: list(resolvers) for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for resolver_key, resolvers in GitHubActionsLoader.yaml_implicit_resolvers.items():
    GitHubActionsLoader.yaml_implicit_resolvers[resolver_key] = [
        (tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"
    ]
GitHubActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_workflow(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=GitHubActionsLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise WorkflowContractError(f"could not load workflow {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowContractError(f"workflow root must be a mapping: {path}")
    return payload


def _workflow_jobs(workflow: Mapping[str, Any]) -> Mapping[str, Any]:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, Mapping) or not jobs:
        raise WorkflowContractError("workflow jobs must be a non-empty mapping")
    if not all(isinstance(job_id, str) for job_id in jobs):
        raise WorkflowContractError("workflow job IDs must be strings")
    return jobs


def _job_mapping(jobs: Mapping[str, Any], job_id: str) -> Mapping[str, Any]:
    job = jobs.get(job_id)
    if not isinstance(job, Mapping):
        raise WorkflowContractError(f"job must be a mapping: {job_id}")
    return job


def _job_needs(job_id: str, job: Mapping[str, Any]) -> tuple[str, ...]:
    raw_needs = job.get("needs")
    if raw_needs is None:
        return ()
    if isinstance(raw_needs, str):
        return (raw_needs,)
    if not isinstance(raw_needs, Sequence) or isinstance(raw_needs, (str, bytes)):
        raise WorkflowContractError(f"job needs must be a string or list: {job_id}")
    needs = tuple(raw_needs)
    if not all(isinstance(dependency, str) for dependency in needs):
        raise WorkflowContractError(f"job needs entries must be strings: {job_id}")
    if len(needs) != len(set(needs)):
        raise WorkflowContractError(f"job has duplicate dependencies: {job_id}")
    return needs


def build_job_graph(workflow: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    jobs = _workflow_jobs(workflow)
    graph = {job_id: _job_needs(job_id, _job_mapping(jobs, job_id)) for job_id in jobs}
    for job_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in graph:
                raise WorkflowContractError(f"job {job_id} needs missing job {dependency}")

    states: dict[str, int] = {}
    trail: list[str] = []

    def visit(job_id: str) -> None:
        state = states.get(job_id, 0)
        if state == 2:
            return
        if state == 1:
            cycle_start = trail.index(job_id)
            cycle = trail[cycle_start:] + [job_id]
            raise WorkflowContractError(f"job dependency cycle: {' -> '.join(cycle)}")
        states[job_id] = 1
        trail.append(job_id)
        for dependency in graph[job_id]:
            visit(dependency)
        trail.pop()
        states[job_id] = 2

    for job_id in graph:
        visit(job_id)
    return graph


def job_ancestors(
    graph: Mapping[str, Sequence[str]],
    job_id: str,
) -> set[str]:
    if job_id not in graph:
        raise WorkflowContractError(f"job is missing: {job_id}")
    ancestors: set[str] = set()
    pending = list(graph[job_id])
    while pending:
        dependency = pending.pop()
        if dependency in ancestors:
            continue
        ancestors.add(dependency)
        pending.extend(graph[dependency])
    return ancestors


def _continue_on_error_enabled(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str) and value.strip().lower() in {
        "false",
        "${{ false }}",
    }:
        return False
    return True


def validate_publish_gate(
    workflow: Mapping[str, Any],
    *,
    publish_job: str,
    required_gates: Sequence[str],
) -> None:
    jobs = _workflow_jobs(workflow)
    graph = build_job_graph(workflow)
    publish = _job_mapping(jobs, publish_job)
    ancestors = job_ancestors(graph, publish_job)
    missing_gates = sorted(set(required_gates) - ancestors)
    if missing_gates:
        raise WorkflowContractError(
            f"publish job {publish_job} does not depend on required gates: {', '.join(missing_gates)}"
        )

    for job_id in (publish_job, *required_gates):
        job = _job_mapping(jobs, job_id)
        if _continue_on_error_enabled(job.get("continue-on-error")):
            raise WorkflowContractError(f"required release job enables continue-on-error: {job_id}")
        if "steps" in job:
            for index, step in enumerate(job_steps(workflow, job_id)):
                if _continue_on_error_enabled(step.get("continue-on-error")):
                    step_id = step.get("id", index)
                    raise WorkflowContractError(f"required release step enables continue-on-error: {job_id}/{step_id}")

    publish_condition = str(publish.get("if", ""))
    bypass = re.search(
        r"\b(always|failure|cancelled)\s*\(",
        publish_condition,
        re.IGNORECASE,
    )
    if bypass:
        status_function = bypass.group(1).lower()
        raise WorkflowContractError(f"publish job must not use {status_function}(): {publish_job}")


def validate_publish_permissions(
    workflow: Mapping[str, Any],
    *,
    publish_job: str,
) -> None:
    top_level_permissions = workflow.get("permissions")
    if top_level_permissions == "write-all":
        raise WorkflowContractError("workflow must not grant write-all permissions")
    if isinstance(top_level_permissions, Mapping) and top_level_permissions.get("contents") == "write":
        raise WorkflowContractError("contents: write must be scoped to the publish job")

    jobs = _workflow_jobs(workflow)
    for job_id in jobs:
        job = _job_mapping(jobs, job_id)
        permissions = job.get("permissions")
        if job_id == publish_job:
            if permissions == "write-all":
                raise WorkflowContractError(f"publish job must not grant write-all: {publish_job}")
            if not isinstance(permissions, Mapping) or permissions.get("contents") != "write":
                raise WorkflowContractError(f"publish job must grant contents: write: {publish_job}")
            continue
        if permissions == "write-all" or (isinstance(permissions, Mapping) and permissions.get("contents") == "write"):
            raise WorkflowContractError(f"non-publish job grants contents: write: {job_id}")


def job_steps(
    workflow: Mapping[str, Any],
    job_id: str,
) -> list[Mapping[str, Any]]:
    jobs = _workflow_jobs(workflow)
    job = _job_mapping(jobs, job_id)
    raw_steps = job.get("steps")
    if not isinstance(raw_steps, list) or not all(isinstance(step, Mapping) for step in raw_steps):
        raise WorkflowContractError(f"job steps must be a list of mappings: {job_id}")
    return list(raw_steps)


def step_by_id(
    workflow: Mapping[str, Any],
    job_id: str,
    step_id: str,
) -> Mapping[str, Any]:
    matches = [step for step in job_steps(workflow, job_id) if step.get("id") == step_id]
    if len(matches) != 1:
        raise WorkflowContractError(f"job {job_id} must contain exactly one step with id {step_id}")
    return matches[0]


def validate_step_order(
    workflow: Mapping[str, Any],
    job_id: str,
    required_step_ids: Sequence[str],
) -> None:
    steps = job_steps(workflow, job_id)
    positions: dict[str, int] = {}
    for index, step in enumerate(steps):
        step_id = step.get("id")
        if isinstance(step_id, str):
            if step_id in positions:
                raise WorkflowContractError(f"job {job_id} contains duplicate step id {step_id}")
            positions[step_id] = index
    missing = [step_id for step_id in required_step_ids if step_id not in positions]
    if missing:
        raise WorkflowContractError(f"job {job_id} is missing required steps: {', '.join(missing)}")
    ordered_positions = [positions[step_id] for step_id in required_step_ids]
    if ordered_positions != sorted(ordered_positions):
        raise WorkflowContractError(f"job {job_id} has unsafe step order: {' -> '.join(required_step_ids)}")
