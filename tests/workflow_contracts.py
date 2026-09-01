from __future__ import annotations

import re
import shlex
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


def _expression_body(value: str) -> str:
    expression = value.strip()
    wrapped = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", expression, re.DOTALL)
    if wrapped:
        return wrapped.group(1).strip()
    return expression


def _continue_on_error_enabled(value: Any) -> bool:
    if value is None or value is False:
        return False
    return not (isinstance(value, str) and _expression_body(value).lower() == "false")


def _condition_requires_success(item: Mapping[str, Any]) -> bool:
    if "if" not in item:
        return True
    condition = item["if"]
    if not isinstance(condition, str):
        return False
    expression = _expression_body(condition)
    return re.fullmatch(r"success\s*\(\s*\)", expression, re.IGNORECASE) is not None


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

    release_path = {publish_job, *ancestors}
    for job_id in sorted(release_path):
        job = _job_mapping(jobs, job_id)
        if _continue_on_error_enabled(job.get("continue-on-error")):
            raise WorkflowContractError(f"required release job enables continue-on-error: {job_id}")
        if "steps" in job:
            for index, step in enumerate(job_steps(workflow, job_id)):
                if _continue_on_error_enabled(step.get("continue-on-error")):
                    step_id = step.get("id", index)
                    raise WorkflowContractError(f"required release step enables continue-on-error: {job_id}/{step_id}")
        if not _condition_requires_success(job):
            raise WorkflowContractError(f"release dependency job must use the default success condition: {job_id}")


def _permission_summary(
    value: Any,
    *,
    location: str,
) -> tuple[str | None, set[str]]:
    if value is None:
        return None, set()
    if isinstance(value, str):
        if value == "read-all":
            return "read", set()
        if value == "write-all":
            return "write", {"*"}
        raise WorkflowContractError(f"invalid permissions value at {location}: {value}")
    if not isinstance(value, Mapping):
        raise WorkflowContractError(f"permissions must be a mapping at {location}")
    invalid = [
        f"{scope}={access}"
        for scope, access in value.items()
        if not isinstance(scope, str) or not isinstance(access, str) or access not in {"none", "read", "write"}
    ]
    if invalid:
        raise WorkflowContractError(f"invalid permission entries at {location}: {', '.join(invalid)}")
    contents = value.get("contents", "none")
    write_scopes = {scope for scope, access in value.items() if access == "write"}
    return contents, write_scopes


def validate_publish_permissions(
    workflow: Mapping[str, Any],
    *,
    publish_job: str,
) -> None:
    top_level_permissions = workflow.get("permissions")
    top_level_contents, top_level_writes = _permission_summary(
        top_level_permissions,
        location="workflow",
    )
    if "contents" in top_level_writes or "*" in top_level_writes:
        raise WorkflowContractError("contents: write must be scoped to the publish job")
    if top_level_writes:
        raise WorkflowContractError("workflow must not grant write permissions: " + ", ".join(sorted(top_level_writes)))

    jobs = _workflow_jobs(workflow)
    for job_id in jobs:
        job = _job_mapping(jobs, job_id)
        permissions = job.get("permissions")
        if job_id == publish_job:
            if permissions == "write-all":
                raise WorkflowContractError(f"publish job must not grant write-all: {publish_job}")
            contents, write_scopes = _permission_summary(
                permissions,
                location=f"job {job_id}",
            )
            if "permissions" not in job or contents != "write":
                raise WorkflowContractError(f"publish job must grant contents: write: {publish_job}")
            unexpected_writes = write_scopes - {"contents"}
            if unexpected_writes:
                raise WorkflowContractError(
                    f"publish job grants unexpected write permissions: {publish_job} "
                    f"({', '.join(sorted(unexpected_writes))})"
                )
            continue
        if "permissions" in job:
            contents, write_scopes = _permission_summary(
                permissions,
                location=f"job {job_id}",
            )
        else:
            contents, write_scopes = top_level_contents, top_level_writes
        if contents is None:
            raise WorkflowContractError(f"non-publish job inherits an implicit contents permission: {job_id}")
        if write_scopes:
            raise WorkflowContractError(
                f"non-publish job grants write permissions: {job_id} ({', '.join(sorted(write_scopes))})"
            )


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


def validate_step_command(
    workflow: Mapping[str, Any],
    job_id: str,
    step_id: str,
    *,
    expected_shell: str,
    expected_tokens: Sequence[str],
) -> None:
    """Require one contract command whose exit status is propagated by a built-in shell."""

    step = step_by_id(workflow, job_id, step_id)
    if step.get("shell") != expected_shell:
        raise WorkflowContractError(f"required step must use shell {expected_shell}: {job_id}/{step_id}")
    command = step.get("run")
    if not isinstance(command, str):
        raise WorkflowContractError(f"required step must define a run command: {job_id}/{step_id}")
    try:
        tokens = tuple(shlex.split(command, posix=True))
    except ValueError as exc:
        raise WorkflowContractError(f"required step has an invalid run command: {job_id}/{step_id}") from exc
    if tokens != tuple(expected_tokens):
        raise WorkflowContractError(f"required step must invoke the release contract directly: {job_id}/{step_id}")


def validate_step_order(
    workflow: Mapping[str, Any],
    job_id: str,
    required_step_ids: Sequence[str],
    *,
    adjacent_pairs: Sequence[tuple[str, str]] = (),
) -> None:
    """Require stable step IDs in order with fail-closed success conditions."""

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
    for predecessor, successor in adjacent_pairs:
        if predecessor not in positions or successor not in positions:
            raise WorkflowContractError(f"job {job_id} cannot validate adjacent steps: {predecessor} -> {successor}")
        if positions[successor] != positions[predecessor] + 1:
            raise WorkflowContractError(f"job {job_id} requires adjacent steps: {predecessor} -> {successor}")
    for step_id in required_step_ids:
        step = steps[positions[step_id]]
        if not _condition_requires_success(step):
            raise WorkflowContractError(f"required step must use the default success condition: {job_id}/{step_id}")
