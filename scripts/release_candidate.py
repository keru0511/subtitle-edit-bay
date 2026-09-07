from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
WORKFLOW_PATH = ".github/workflows/release-readiness.yml"
REQUIRED_JOB_NAMES = (
    "Classify merge candidate",
    "Validate source and version",
    "Release tests on Linux",
    "Build and verify Windows installer",
    "Install and start prepared package",
    "Preparation result",
    "Release readiness",
)
PER_PAGE = 100
MAX_PAGES = 100


class ReleaseCandidateError(RuntimeError):
    """Raised when a prepared artifact cannot be promoted safely."""


@dataclass(frozen=True)
class ReleaseCandidate:
    schema_version: int
    repository: str
    pull_request_number: int
    pull_request_head_sha: str
    pull_request_base_sha: str
    candidate_source_sha: str
    candidate_source_tree: str
    release_commit_sha: str
    release_commit_tree: str
    release_version: str
    workflow_path: str
    workflow_run_id: int
    workflow_run_attempt: int
    artifact_id: int
    artifact_name: str
    artifact_digest: str
    artifact_expires_at: str


class GitHubApi:
    def __init__(self, repository: str, token: str, api_url: str = "https://api.github.com") -> None:
        if not REPOSITORY_PATTERN.fullmatch(repository):
            raise ReleaseCandidateError(f"invalid GitHub repository: {repository}")
        if not token:
            raise ReleaseCandidateError("GH_TOKEN is required to select a release candidate")
        self.repository = repository
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get(self, path: str, query: dict[str, object] | None = None) -> object:
        url = f"{self.api_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "subtitle-edit-bay-release-candidate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ReleaseCandidateError(f"GitHub API request failed with HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ReleaseCandidateError(f"GitHub API request failed: {url}: {exc}") from exc

    def pages(self, path: str, key: str | None = None, query: dict[str, object] | None = None) -> list[object]:
        collected: list[object] = []
        for page in range(1, MAX_PAGES + 1):
            page_query = dict(query or {})
            page_query.update({"per_page": PER_PAGE, "page": page})
            payload = self.get(path, page_query)
            if key is not None:
                if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
                    raise ReleaseCandidateError(f"GitHub API response has no {key!r} array: {path}")
                items = payload[key]
            else:
                if not isinstance(payload, list):
                    raise ReleaseCandidateError(f"GitHub API response must be an array: {path}")
                items = payload
            collected.extend(items)
            if len(items) < PER_PAGE:
                return collected
        raise ReleaseCandidateError(f"GitHub API pagination exceeded {MAX_PAGES} pages: {path}")


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReleaseCandidateError(f"{context} must be an object")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseCandidateError(f"{context} must be a non-empty string")
    return value


def _integer(value: object, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ReleaseCandidateError(f"{context} must be a positive integer")
    return value


def _sha(value: object, context: str) -> str:
    result = _string(value, context)
    if not SHA_PATTERN.fullmatch(result):
        raise ReleaseCandidateError(f"{context} must be a full lowercase commit SHA")
    return result


def _nested(payload: dict[str, object], *keys: str) -> object:
    value: object = payload
    for key in keys:
        value = _object(value, ".".join(keys)).get(key)
    return value


def _matching_pull_request(api: GitHubApi, release_commit_sha: str) -> dict[str, object]:
    pulls = api.pages(f"/repos/{api.repository}/commits/{release_commit_sha}/pulls")
    matches = []
    for item in pulls:
        pull = _object(item, "associated pull request")
        if pull.get("merge_commit_sha") == release_commit_sha:
            matches.append(pull)
    if len(matches) != 1:
        raise ReleaseCandidateError(
            f"release commit must map to exactly one merged pull request; found {len(matches)}"
        )
    number = _integer(matches[0].get("number"), "pull request number")
    pull = _object(api.get(f"/repos/{api.repository}/pulls/{number}"), "pull request")
    if pull.get("merged") is not True or pull.get("merge_commit_sha") != release_commit_sha:
        raise ReleaseCandidateError("pull request is not the merge that approved the release commit")
    if _nested(pull, "base", "ref") != "main":
        raise ReleaseCandidateError("release pull request must target main")
    if _nested(pull, "head", "repo", "full_name") != api.repository:
        raise ReleaseCandidateError("fork pull request artifacts cannot be promoted")
    return pull


def _require_version_only(api: GitHubApi, pull_number: int) -> None:
    files = api.pages(f"/repos/{api.repository}/pulls/{pull_number}/files")
    names = [_string(_object(item, "pull request file").get("filename"), "changed filename") for item in files]
    if names != ["VERSION"]:
        raise ReleaseCandidateError("release pull request must change only VERSION")


def _commit_identity(api: GitHubApi, sha: str, context: str) -> tuple[str, tuple[str, ...]]:
    commit = _object(api.get(f"/repos/{api.repository}/git/commits/{sha}"), context)
    tree_sha = _sha(_nested(commit, "tree", "sha"), f"{context} tree SHA")
    parents_value = commit.get("parents")
    if not isinstance(parents_value, list):
        raise ReleaseCandidateError(f"{context} parents must be an array")
    parents = tuple(
        _sha(_object(parent, f"{context} parent").get("sha"), f"{context} parent SHA")
        for parent in parents_value
    )
    return tree_sha, parents


def _run_matches_pull(run: dict[str, object], pull_number: int, head_sha: str, repository: str) -> bool:
    pulls = run.get("pull_requests")
    if not isinstance(pulls, list):
        return False
    numbers = {value.get("number") for value in pulls if isinstance(value, dict)}
    head_repository = run.get("head_repository")
    return (
        pull_number in numbers
        and run.get("event") == "pull_request"
        and run.get("path") == WORKFLOW_PATH
        and run.get("head_sha") == head_sha
        and isinstance(head_repository, dict)
        and head_repository.get("full_name") == repository
        and run.get("status") == "completed"
    )


def _latest_candidate_run(api: GitHubApi, pull_number: int, head_sha: str) -> dict[str, object]:
    runs = api.pages(
        f"/repos/{api.repository}/actions/workflows/release-readiness.yml/runs",
        key="workflow_runs",
        query={"event": "pull_request", "status": "completed"},
    )
    matches = [
        _object(run, "workflow run")
        for run in runs
        if isinstance(run, dict) and _run_matches_pull(run, pull_number, head_sha, api.repository)
    ]
    if not matches:
        raise ReleaseCandidateError("no completed Release readiness run matches the approved pull request head")
    matches.sort(
        key=lambda run: (
            _string(run.get("created_at"), "workflow run created_at"),
            _integer(run.get("id"), "workflow run id"),
        ),
        reverse=True,
    )
    latest = matches[0]
    if latest.get("conclusion") != "success":
        raise ReleaseCandidateError(
            f"latest matching Release readiness run did not succeed: {latest.get('conclusion')}"
        )
    return latest


def _require_successful_jobs(api: GitHubApi, run_id: int, run_attempt: int) -> None:
    jobs = api.pages(
        f"/repos/{api.repository}/actions/runs/{run_id}/jobs",
        key="jobs",
        query={"filter": "latest"},
    )
    job_objects = [_object(job, "workflow job") for job in jobs]
    for required_name in REQUIRED_JOB_NAMES:
        matches = [
            job
            for job in job_objects
            if _string(job.get("name"), "workflow job name").split(" / ")[-1] == required_name
        ]
        if len(matches) != 1:
            raise ReleaseCandidateError(
                f"required workflow job must appear exactly once: {required_name}; found {len(matches)}"
            )
        job = matches[0]
        if job.get("conclusion") != "success" or job.get("status") != "completed":
            raise ReleaseCandidateError(f"required workflow job did not succeed: {required_name}")
        if _integer(job.get("run_attempt"), f"{required_name} run attempt") != run_attempt:
            raise ReleaseCandidateError(f"required workflow job belongs to a different run attempt: {required_name}")


def _candidate_artifact(api: GitHubApi, run_id: int, release_version: str) -> dict[str, object]:
    version = release_version.removeprefix("v")
    name_pattern = re.compile(
        rf"^subtitle-edit-bay-{re.escape(version)}-windows-installer-([0-9a-f]{{40}})$"
    )
    artifacts = api.pages(f"/repos/{api.repository}/actions/runs/{run_id}/artifacts", key="artifacts")
    matches = []
    for value in artifacts:
        artifact = _object(value, "workflow artifact")
        name = artifact.get("name")
        if isinstance(name, str) and name_pattern.fullmatch(name):
            matches.append(artifact)
    if len(matches) != 1:
        raise ReleaseCandidateError(
            f"matching prepared artifact must be unique; found {len(matches)}"
        )
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ReleaseCandidateError("prepared artifact is expired or has an unknown expiry state")
    digest = _string(artifact.get("digest"), "artifact digest")
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseCandidateError("artifact digest must be a GitHub SHA-256 digest")
    _string(artifact.get("expires_at"), "artifact expiry")
    return artifact


def select_release_candidate(
    api: GitHubApi,
    release_commit_sha: str,
    release_version: str,
) -> ReleaseCandidate:
    release_commit_sha = _sha(release_commit_sha, "release commit SHA")
    if not re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", release_version):
        raise ReleaseCandidateError("release version must use strict vX.Y.Z form")

    pull = _matching_pull_request(api, release_commit_sha)
    pull_number = _integer(pull.get("number"), "pull request number")
    head_sha = _sha(_nested(pull, "head", "sha"), "pull request head SHA")
    base_sha = _sha(_nested(pull, "base", "sha"), "pull request base SHA")
    _require_version_only(api, pull_number)

    run = _latest_candidate_run(api, pull_number, head_sha)
    run_id = _integer(run.get("id"), "workflow run id")
    run_attempt = _integer(run.get("run_attempt"), "workflow run attempt")
    _require_successful_jobs(api, run_id, run_attempt)
    artifact = _candidate_artifact(api, run_id, release_version)
    artifact_name = _string(artifact.get("name"), "artifact name")
    candidate_source_sha = _sha(artifact_name.rsplit("-", 1)[-1], "candidate source SHA")

    candidate_tree, candidate_parents = _commit_identity(api, candidate_source_sha, "candidate source")
    release_tree, _ = _commit_identity(api, release_commit_sha, "release commit")
    if candidate_parents != (base_sha, head_sha):
        raise ReleaseCandidateError("candidate source is not the final tested merge of the approved head and base")
    if candidate_tree != release_tree:
        raise ReleaseCandidateError("candidate and release source trees differ")

    return ReleaseCandidate(
        schema_version=1,
        repository=api.repository,
        pull_request_number=pull_number,
        pull_request_head_sha=head_sha,
        pull_request_base_sha=base_sha,
        candidate_source_sha=candidate_source_sha,
        candidate_source_tree=candidate_tree,
        release_commit_sha=release_commit_sha,
        release_commit_tree=release_tree,
        release_version=release_version,
        workflow_path=WORKFLOW_PATH,
        workflow_run_id=run_id,
        workflow_run_attempt=run_attempt,
        artifact_id=_integer(artifact.get("id"), "artifact id"),
        artifact_name=artifact_name,
        artifact_digest=_string(artifact.get("digest"), "artifact digest"),
        artifact_expires_at=_string(artifact.get("expires_at"), "artifact expiry"),
    )


def write_github_outputs(path: Path, candidate: ReleaseCandidate) -> None:
    values = asdict(candidate)
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def write_promotion_record(path: Path, candidate_path: Path, installer_sha256: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", installer_sha256):
        raise ReleaseCandidateError("installer SHA-256 must be 64 lowercase hexadecimal characters")
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"could not read selected candidate: {exc}") from exc
    if not isinstance(candidate, dict) or candidate.get("schema_version") != 1:
        raise ReleaseCandidateError("selected candidate has an unknown schema")
    record = {
        "schema_version": 1,
        "repository": candidate.get("repository"),
        "release_version": candidate.get("release_version"),
        "release_commit_sha": candidate.get("release_commit_sha"),
        "release_commit_tree": candidate.get("release_commit_tree"),
        "candidate_source_sha": candidate.get("candidate_source_sha"),
        "candidate_source_tree": candidate.get("candidate_source_tree"),
        "pull_request_number": candidate.get("pull_request_number"),
        "pull_request_head_sha": candidate.get("pull_request_head_sha"),
        "pull_request_base_sha": candidate.get("pull_request_base_sha"),
        "workflow_path": candidate.get("workflow_path"),
        "workflow_run_id": candidate.get("workflow_run_id"),
        "workflow_run_attempt": candidate.get("workflow_run_attempt"),
        "artifact_id": candidate.get("artifact_id"),
        "artifact_name": candidate.get("artifact_name"),
        "artifact_digest": candidate.get("artifact_digest"),
        "artifact_expires_at": candidate.get("artifact_expires_at"),
        "installer_sha256": installer_sha256,
    }
    required_strings = (
        "repository",
        "release_version",
        "release_commit_sha",
        "release_commit_tree",
        "candidate_source_sha",
        "candidate_source_tree",
        "pull_request_head_sha",
        "pull_request_base_sha",
        "workflow_path",
        "artifact_name",
        "artifact_digest",
        "artifact_expires_at",
    )
    if any(not isinstance(record[key], str) or not record[key] for key in required_strings):
        raise ReleaseCandidateError("selected candidate is missing required identity fields")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_promotion_record(path: Path, expected_path: Path) -> None:
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"could not read promotion record: {exc}") from exc
    if actual != expected:
        raise ReleaseCandidateError("published promotion record does not match the selected candidate")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select and record a verified release candidate artifact.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    select = subparsers.add_parser("select")
    select.add_argument("--repository", required=True)
    select.add_argument("--release-commit-sha", required=True)
    select.add_argument("--release-version", required=True)
    select.add_argument("--output", type=Path, required=True)
    select.add_argument("--github-output", type=Path, required=True)
    record = subparsers.add_parser("write-promotion")
    record.add_argument("--candidate", type=Path, required=True)
    record.add_argument("--installer-sha256", required=True)
    record.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify-promotion")
    verify.add_argument("--actual", type=Path, required=True)
    verify.add_argument("--expected", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "select":
            api = GitHubApi(
                args.repository,
                os.environ.get("GH_TOKEN", ""),
                os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            )
            candidate = select_release_candidate(api, args.release_commit_sha, args.release_version)
            args.output.write_text(json.dumps(asdict(candidate), indent=2) + "\n", encoding="utf-8")
            write_github_outputs(args.github_output, candidate)
        elif args.command == "write-promotion":
            write_promotion_record(args.output, args.candidate, args.installer_sha256)
        else:
            verify_promotion_record(args.actual, args.expected)
    except ReleaseCandidateError as exc:
        print(f"Release candidate error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
