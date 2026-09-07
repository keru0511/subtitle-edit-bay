from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Sequence


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
WORKFLOW_PATH = ".github/workflows/release-readiness.yml"
CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
REQUIRED_READINESS_JOB_NAMES = (
    "Classify merge candidate",
    "Validate source and version",
    "Release tests on Linux",
    "Build and verify Windows installer",
    "Install and start prepared package",
    "Preparation result",
    "Release readiness",
)
REQUIRED_CI_JOB_NAMES = (
    "Python quality checks",
    "Portable, Qt, and FFmpeg tests",
    "Windows runtime tests",
    "Windows launcher tests",
    "FFmpeg 6 compatibility",
    "Windows installer smoke",
)
CI_VALIDATION_JOB_NAME = "CI validation result"
# Kept as the public compatibility name used by existing contract tests.
REQUIRED_JOB_NAMES = REQUIRED_READINESS_JOB_NAMES
PER_PAGE = 100
MAX_PAGES = 100
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 4 * 1024 * 1024 * 1024
RELEASE_ARTIFACT_FILES = (
    "SubtitleEditBay-Setup.exe",
    "SubtitleEditBay-Setup.exe.sha256",
    "SubtitleEditBay-Setup.exe.manifest.json",
    "release-preparation.json",
)
DECISION_ARTIFACT_FILES = ("selected-candidate.json", "release-promotion.json")
CI_IDENTITY_FILE = "ci-validation-identity.json"


class ReleaseCandidateError(RuntimeError):
    """Raised when a prepared artifact cannot be promoted safely."""


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if (
            redirected is not None
            and urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(newurl).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


@dataclass(frozen=True)
class ReleaseCandidate:
    schema_version: int
    repository: str
    pull_request_number: int
    pull_request_head_sha: str
    pull_request_head_branch: str
    pull_request_base_sha: str
    candidate_source_sha: str
    candidate_source_tree: str
    release_commit_sha: str
    release_commit_tree: str
    release_version: str
    workflow_path: str
    workflow_run_id: int
    workflow_run_attempt: int
    artifact_workflow_run_attempt: int
    installer_smoke_workflow_run_attempt: int
    ci_workflow_run_id: int
    ci_workflow_run_attempt: int
    ci_artifact_workflow_run_attempt: int
    ci_candidate_source_sha: str
    ci_candidate_source_tree: str
    ci_artifact_id: int
    ci_artifact_name: str
    ci_artifact_digest: str
    ci_artifact_expires_at: str
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
            with urllib.request.build_opener(_SafeRedirectHandler()).open(request, timeout=30) as response:
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

    def download_artifact(self, artifact_id: int, destination: Path, expected_digest: str) -> None:
        artifact_id = _integer(artifact_id, "artifact id")
        if not ARTIFACT_DIGEST_PATTERN.fullmatch(expected_digest):
            raise ReleaseCandidateError("artifact digest must be a GitHub SHA-256 digest")
        url = f"{self.api_url}/repos/{self.repository}/actions/artifacts/{artifact_id}/zip"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "subtitle-edit-bay-release-candidate",
            },
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (
                urllib.request.build_opener(_SafeRedirectHandler()).open(request, timeout=60) as response,
                destination.open("wb") as output,
            ):
                digest = hashlib.sha256()
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ReleaseCandidateError("artifact archive exceeds the size limit")
                    digest.update(chunk)
                    output.write(chunk)
        except urllib.error.HTTPError as exc:
            raise ReleaseCandidateError(f"artifact download failed with HTTP {exc.code}: {url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ReleaseCandidateError(f"artifact download failed: {url}: {exc}") from exc
        actual_digest = f"sha256:{digest.hexdigest()}"
        if actual_digest != expected_digest:
            destination.unlink(missing_ok=True)
            raise ReleaseCandidateError(
                f"artifact archive digest mismatch: expected {expected_digest}, got {actual_digest}"
            )


def extract_verified_artifact(
    archive_path: Path,
    destination: Path,
    expected_digest: str,
    expected_files: Sequence[str],
) -> None:
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(expected_digest):
        raise ReleaseCandidateError("artifact digest must be a GitHub SHA-256 digest")
    try:
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReleaseCandidateError(f"could not read artifact archive: {exc}") from exc
    if f"sha256:{digest}" != expected_digest:
        raise ReleaseCandidateError("artifact archive digest mismatch")
    expected = set(expected_files)
    if len(expected) != len(expected_files) or not expected:
        raise ReleaseCandidateError("expected artifact file set must be non-empty and unique")
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    extracted_size = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                member_path = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or len(member_path.parts) != 1
                    or member.filename in seen
                    or (mode & 0o170000) == 0o120000
                ):
                    raise ReleaseCandidateError(f"unsafe artifact archive member: {member.filename}")
                seen.add(member.filename)
                extracted_size += member.file_size
                if extracted_size > MAX_EXTRACTED_BYTES:
                    raise ReleaseCandidateError("artifact extracted contents exceed the size limit")
                with archive.open(member) as source, (destination / member.filename).open("wb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseCandidateError(f"could not extract artifact archive: {exc}") from exc
    if seen != expected:
        raise ReleaseCandidateError(
            f"artifact file set mismatch: missing={sorted(expected - seen)!r} unexpected={sorted(seen - expected)!r}"
        )


def download_verified_artifact(
    api: GitHubApi,
    artifact_id: int,
    artifact_digest: str,
    destination: Path,
    expected_files: Sequence[str],
) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        archive_path = Path(temp_dir) / "artifact.zip"
        api.download_artifact(artifact_id, archive_path, artifact_digest)
        extract_verified_artifact(archive_path, destination, artifact_digest, expected_files)


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
        raise ReleaseCandidateError(f"release commit must map to exactly one merged pull request; found {len(matches)}")
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
        _sha(_object(parent, f"{context} parent").get("sha"), f"{context} parent SHA") for parent in parents_value
    )
    return tree_sha, parents


def _run_matches_pull(
    run: dict[str, object],
    pull_number: int,
    head_sha: str,
    head_branch: str,
    repository: str,
    workflow_path: str,
) -> bool:
    pulls = run.get("pull_requests")
    if not isinstance(pulls, list):
        return False
    numbers = {value.get("number") for value in pulls if isinstance(value, dict)}
    # GitHub may clear this association after merge. A non-empty contradictory
    # association is rejected; an empty list is bound through immutable head,
    # branch, repository, workflow and (later) candidate commit/tree identity.
    pull_association_matches = not pulls or pull_number in numbers
    head_repository = run.get("head_repository")
    return (
        pull_association_matches
        and run.get("event") == "pull_request"
        and run.get("path") == workflow_path
        and run.get("head_sha") == head_sha
        and run.get("head_branch") == head_branch
        and isinstance(head_repository, dict)
        and head_repository.get("full_name") == repository
    )


def _latest_workflow_run(
    api: GitHubApi,
    pull_number: int,
    head_sha: str,
    head_branch: str,
    workflow_path: str,
) -> dict[str, object]:
    workflow_name = workflow_path.rsplit("/", 1)[-1]
    runs = api.pages(
        f"/repos/{api.repository}/actions/workflows/{workflow_name}/runs",
        key="workflow_runs",
        query={"event": "pull_request"},
    )
    matches = [
        _object(run, "workflow run")
        for run in runs
        if isinstance(run, dict)
        and _run_matches_pull(run, pull_number, head_sha, head_branch, api.repository, workflow_path)
    ]
    if not matches:
        raise ReleaseCandidateError(f"no {workflow_name} run matches the approved pull request head")
    matches.sort(
        key=lambda run: (
            _string(run.get("created_at"), "workflow run created_at"),
            _integer(run.get("id"), "workflow run id"),
        ),
        reverse=True,
    )
    latest = matches[0]
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ReleaseCandidateError(
            f"latest matching {workflow_name} run did not succeed: {latest.get('status')}/{latest.get('conclusion')}"
        )
    return latest


def _require_successful_jobs(
    api: GitHubApi,
    run_id: int,
    run_attempt: int,
    required_job_names: Sequence[str],
) -> dict[str, int]:
    jobs = api.pages(
        f"/repos/{api.repository}/actions/runs/{run_id}/jobs",
        key="jobs",
        query={"filter": "all"},
    )
    job_objects = [_object(job, "workflow job") for job in jobs]
    successful_attempts: dict[str, int] = {}
    for required_name in required_job_names:
        matches = [
            job
            for job in job_objects
            if _string(job.get("name"), "workflow job name").split(" / ")[-1] == required_name
        ]
        if not matches:
            raise ReleaseCandidateError(f"required workflow job is missing: {required_name}")
        attempts: dict[int, dict[str, object]] = {}
        for job in matches:
            attempt = _integer(job.get("run_attempt"), f"{required_name} run attempt")
            if attempt > run_attempt:
                raise ReleaseCandidateError(f"required workflow job has a future run attempt: {required_name}")
            if attempt in attempts:
                raise ReleaseCandidateError(
                    f"required workflow job appears more than once in attempt {attempt}: {required_name}"
                )
            attempts[attempt] = job
        job_attempt = max(attempts)
        job = attempts[job_attempt]
        if job.get("conclusion") != "success" or job.get("status") != "completed":
            raise ReleaseCandidateError(f"required workflow job did not succeed: {required_name}")
        successful_attempts[required_name] = job_attempt
    return successful_attempts


def _candidate_artifact(
    api: GitHubApi,
    run_id: int,
    release_version: str,
    producer_attempt: int,
) -> dict[str, object]:
    version = release_version.removeprefix("v")
    name_pattern = re.compile(
        rf"^subtitle-edit-bay-{re.escape(version)}-windows-installer-"
        rf"([0-9a-f]{{40}})-attempt-{producer_attempt}$"
    )
    artifacts = api.pages(f"/repos/{api.repository}/actions/runs/{run_id}/artifacts", key="artifacts")
    matches = []
    for value in artifacts:
        artifact = _object(value, "workflow artifact")
        name = artifact.get("name")
        if isinstance(name, str) and name_pattern.fullmatch(name):
            matches.append(artifact)
    if len(matches) != 1:
        raise ReleaseCandidateError(f"matching prepared artifact must be unique; found {len(matches)}")
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ReleaseCandidateError("prepared artifact is expired or has an unknown expiry state")
    digest = _string(artifact.get("digest"), "artifact digest")
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseCandidateError("artifact digest must be a GitHub SHA-256 digest")
    _string(artifact.get("expires_at"), "artifact expiry")
    return artifact


def _ci_identity_artifact(api: GitHubApi, run_id: int, producer_attempt: int) -> dict[str, object]:
    name_pattern = re.compile(rf"^ci-validation-identity-([0-9a-f]{{40}})-attempt-{producer_attempt}$")
    artifacts = api.pages(f"/repos/{api.repository}/actions/runs/{run_id}/artifacts", key="artifacts")
    matches = [
        artifact
        for value in artifacts
        if (artifact := _object(value, "CI validation artifact"))
        and isinstance(artifact.get("name"), str)
        and name_pattern.fullmatch(str(artifact["name"]))
    ]
    if len(matches) != 1:
        raise ReleaseCandidateError(
            f"CI validation identity artifact for attempt {producer_attempt} must be unique; found {len(matches)}"
        )
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ReleaseCandidateError("CI validation identity artifact is expired or has unknown expiry state")
    digest = _string(artifact.get("digest"), "CI validation artifact digest")
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseCandidateError("CI validation artifact digest must be a GitHub SHA-256 digest")
    _string(artifact.get("expires_at"), "CI validation artifact expiry")
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
    head_branch = _string(_nested(pull, "head", "ref"), "pull request head branch")
    base_sha = _sha(_nested(pull, "base", "sha"), "pull request base SHA")
    _require_version_only(api, pull_number)

    run = _latest_workflow_run(api, pull_number, head_sha, head_branch, WORKFLOW_PATH)
    run_id = _integer(run.get("id"), "workflow run id")
    run_attempt = _integer(run.get("run_attempt"), "workflow run attempt")
    readiness_job_attempts = _require_successful_jobs(
        api,
        run_id,
        run_attempt,
        REQUIRED_READINESS_JOB_NAMES,
    )
    ci_run = _latest_workflow_run(api, pull_number, head_sha, head_branch, CI_WORKFLOW_PATH)
    ci_run_id = _integer(ci_run.get("id"), "CI workflow run id")
    ci_run_attempt = _integer(ci_run.get("run_attempt"), "CI workflow run attempt")
    ci_job_attempts = _require_successful_jobs(
        api,
        ci_run_id,
        ci_run_attempt,
        (*REQUIRED_CI_JOB_NAMES, CI_VALIDATION_JOB_NAME),
    )
    artifact_producer_attempt = readiness_job_attempts["Build and verify Windows installer"]
    artifact = _candidate_artifact(api, run_id, release_version, artifact_producer_attempt)
    artifact_name = _string(artifact.get("name"), "artifact name")
    candidate_source_sha = _sha(
        artifact_name.split("-attempt-", 1)[0].rsplit("-", 1)[-1],
        "candidate source SHA",
    )
    ci_artifact = _ci_identity_artifact(
        api,
        ci_run_id,
        ci_job_attempts[CI_VALIDATION_JOB_NAME],
    )
    ci_artifact_name = _string(ci_artifact.get("name"), "CI validation artifact name")
    ci_source_sha = _sha(ci_artifact_name.split("-attempt-", 1)[0].rsplit("-", 1)[-1], "CI source SHA")

    candidate_tree, candidate_parents = _commit_identity(api, candidate_source_sha, "candidate source")
    ci_tree, ci_parents = _commit_identity(api, ci_source_sha, "CI source")
    release_tree, _ = _commit_identity(api, release_commit_sha, "release commit")
    if candidate_parents != (base_sha, head_sha):
        raise ReleaseCandidateError("candidate source is not the final tested merge of the approved head and base")
    if candidate_tree != release_tree:
        raise ReleaseCandidateError("candidate and release source trees differ")
    if ci_parents != (base_sha, head_sha):
        raise ReleaseCandidateError("CI source is not the final merge of the approved head and base")
    if ci_source_sha != candidate_source_sha or ci_tree != candidate_tree:
        raise ReleaseCandidateError("CI and release readiness validated different source content")

    return ReleaseCandidate(
        schema_version=1,
        repository=api.repository,
        pull_request_number=pull_number,
        pull_request_head_sha=head_sha,
        pull_request_head_branch=head_branch,
        pull_request_base_sha=base_sha,
        candidate_source_sha=candidate_source_sha,
        candidate_source_tree=candidate_tree,
        release_commit_sha=release_commit_sha,
        release_commit_tree=release_tree,
        release_version=release_version,
        workflow_path=WORKFLOW_PATH,
        workflow_run_id=run_id,
        workflow_run_attempt=run_attempt,
        artifact_workflow_run_attempt=artifact_producer_attempt,
        installer_smoke_workflow_run_attempt=readiness_job_attempts["Install and start prepared package"],
        ci_workflow_run_id=ci_run_id,
        ci_workflow_run_attempt=ci_run_attempt,
        ci_artifact_workflow_run_attempt=ci_job_attempts[CI_VALIDATION_JOB_NAME],
        ci_candidate_source_sha=ci_source_sha,
        ci_candidate_source_tree=ci_tree,
        ci_artifact_id=_integer(ci_artifact.get("id"), "CI validation artifact id"),
        ci_artifact_name=ci_artifact_name,
        ci_artifact_digest=_string(ci_artifact.get("digest"), "CI validation artifact digest"),
        ci_artifact_expires_at=_string(ci_artifact.get("expires_at"), "CI validation artifact expiry"),
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


def _load_candidate(path: Path) -> ReleaseCandidate:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"could not read selected candidate: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != set(ReleaseCandidate.__dataclass_fields__):
        raise ReleaseCandidateError("selected candidate has an unknown schema or field set")
    try:
        candidate = ReleaseCandidate(**payload)
    except TypeError as exc:
        raise ReleaseCandidateError(f"selected candidate is malformed: {exc}") from exc
    if candidate.schema_version != 1:
        raise ReleaseCandidateError("selected candidate has an unknown schema")
    if not REPOSITORY_PATTERN.fullmatch(candidate.repository):
        raise ReleaseCandidateError("selected candidate repository is invalid")
    for field_name in (
        "pull_request_head_sha",
        "pull_request_base_sha",
        "candidate_source_sha",
        "candidate_source_tree",
        "ci_candidate_source_sha",
        "ci_candidate_source_tree",
        "release_commit_sha",
        "release_commit_tree",
    ):
        _sha(getattr(candidate, field_name), f"selected candidate {field_name}")
    for field_name in (
        "pull_request_number",
        "workflow_run_id",
        "workflow_run_attempt",
        "artifact_workflow_run_attempt",
        "installer_smoke_workflow_run_attempt",
        "ci_workflow_run_id",
        "ci_workflow_run_attempt",
        "ci_artifact_workflow_run_attempt",
        "ci_artifact_id",
        "artifact_id",
    ):
        _integer(getattr(candidate, field_name), f"selected candidate {field_name}")
    if candidate.workflow_path != WORKFLOW_PATH:
        raise ReleaseCandidateError("selected candidate workflow path is invalid")
    if not candidate.pull_request_head_branch:
        raise ReleaseCandidateError("selected candidate pull request head branch is invalid")
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(candidate.artifact_digest):
        raise ReleaseCandidateError("selected candidate artifact digest is invalid")
    if not candidate.ci_artifact_name or not candidate.ci_artifact_expires_at:
        raise ReleaseCandidateError("selected candidate CI artifact identity is invalid")
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(candidate.ci_artifact_digest):
        raise ReleaseCandidateError("selected candidate CI artifact digest is invalid")
    return candidate


def _artifact_by_name(
    api: GitHubApi,
    run_id: int,
    artifact_name: str,
    *,
    allow_missing: bool,
) -> dict[str, object] | None:
    artifacts = api.pages(f"/repos/{api.repository}/actions/runs/{run_id}/artifacts", key="artifacts")
    matches = [
        _object(artifact, "workflow artifact")
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("name") == artifact_name
    ]
    if not matches and allow_missing:
        return None
    if len(matches) != 1:
        raise ReleaseCandidateError(
            f"artifact {artifact_name!r} must appear exactly once in run {run_id}; found {len(matches)}"
        )
    artifact = matches[0]
    if artifact.get("expired") is not False:
        raise ReleaseCandidateError(f"artifact {artifact_name!r} is expired or has unknown expiry state")
    digest = _string(artifact.get("digest"), "artifact digest")
    if not ARTIFACT_DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseCandidateError("artifact digest must be a GitHub SHA-256 digest")
    return artifact


def resolve_release_candidate(
    api: GitHubApi,
    release_commit_sha: str,
    release_version: str,
    current_run_id: int,
    decision_artifact_name: str,
    decision_directory: Path,
    output: Path,
) -> tuple[ReleaseCandidate, bool]:
    existing = _artifact_by_name(
        api,
        current_run_id,
        decision_artifact_name,
        allow_missing=True,
    )
    if existing is None:
        candidate = select_release_candidate(api, release_commit_sha, release_version)
        output.write_text(json.dumps(asdict(candidate), indent=2) + "\n", encoding="utf-8")
        return candidate, False

    download_verified_artifact(
        api,
        _integer(existing.get("id"), "decision artifact id"),
        _string(existing.get("digest"), "decision artifact digest"),
        decision_directory,
        DECISION_ARTIFACT_FILES,
    )
    stored_path = decision_directory / "selected-candidate.json"
    candidate = _load_candidate(stored_path)
    if (
        candidate.repository != api.repository
        or candidate.release_commit_sha != release_commit_sha
        or candidate.release_version != release_version
    ):
        raise ReleaseCandidateError("saved promotion decision does not match this release request")
    promotion_path = decision_directory / "release-promotion.json"
    try:
        promotion = json.loads(promotion_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"could not read saved promotion record: {exc}") from exc
    if not isinstance(promotion, dict):
        raise ReleaseCandidateError("saved promotion record must be an object")
    installer_sha256 = _string(promotion.get("installer_sha256"), "saved installer SHA-256")
    with tempfile.TemporaryDirectory() as temp_dir:
        expected = Path(temp_dir) / "release-promotion.json"
        write_promotion_record(expected, stored_path, installer_sha256)
        verify_promotion_record(promotion_path, expected)
    shutil.copyfile(stored_path, output)
    return candidate, True


def download_named_run_artifact(
    api: GitHubApi,
    run_id: int,
    artifact_name: str,
    destination: Path,
    expected_files: Sequence[str],
) -> None:
    artifact = _artifact_by_name(api, run_id, artifact_name, allow_missing=False)
    assert artifact is not None
    download_verified_artifact(
        api,
        _integer(artifact.get("id"), "artifact id"),
        _string(artifact.get("digest"), "artifact digest"),
        destination,
        expected_files,
    )


def verify_preparation_binding(candidate_path: Path, preparation_path: Path) -> None:
    candidate = _load_candidate(candidate_path)
    try:
        preparation = json.loads(preparation_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"could not read preparation identity: {exc}") from exc
    if not isinstance(preparation, dict):
        raise ReleaseCandidateError("release preparation must be an object")
    expected = {
        "repository": candidate.repository,
        "event_name": "pull_request",
        "pull_request_number": candidate.pull_request_number,
        "pull_request_head_sha": candidate.pull_request_head_sha,
        "pull_request_base_sha": candidate.pull_request_base_sha,
        "pull_request_head_branch": candidate.pull_request_head_branch,
        "workflow_path": candidate.workflow_path,
        "workflow_run_id": candidate.workflow_run_id,
        "workflow_run_attempt": candidate.artifact_workflow_run_attempt,
    }
    producer = preparation.get("producer")
    if not isinstance(producer, dict):
        raise ReleaseCandidateError("release preparation has no producer identity")
    if producer != expected:
        raise ReleaseCandidateError("release preparation producer does not match the selected workflow run")


def write_ci_validation_identity(
    path: Path,
    repository: str,
    pull_request_number: int,
    pull_request_head_sha: str,
    pull_request_base_sha: str,
    pull_request_head_branch: str,
    source_sha: str,
    source_tree: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
) -> None:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ReleaseCandidateError("CI validation repository is invalid")
    record = {
        "schema_version": 1,
        "repository": repository,
        "event_name": "pull_request",
        "pull_request_number": _integer(pull_request_number, "CI pull request number"),
        "pull_request_head_sha": _sha(pull_request_head_sha, "CI pull request head SHA"),
        "pull_request_base_sha": _sha(pull_request_base_sha, "CI pull request base SHA"),
        "pull_request_head_branch": _string(pull_request_head_branch, "CI pull request head branch"),
        "source_sha": _sha(source_sha, "CI source SHA"),
        "source_tree": _sha(source_tree, "CI source tree"),
        "workflow_path": CI_WORKFLOW_PATH,
        "workflow_run_id": _integer(workflow_run_id, "CI workflow run id"),
        "workflow_run_attempt": _integer(workflow_run_attempt, "CI workflow run attempt"),
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_ci_validation_binding(candidate_path: Path, identity_path: Path) -> None:
    candidate = _load_candidate(candidate_path)
    try:
        actual = json.loads(identity_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"could not read CI validation identity: {exc}") from exc
    expected = {
        "schema_version": 1,
        "repository": candidate.repository,
        "event_name": "pull_request",
        "pull_request_number": candidate.pull_request_number,
        "pull_request_head_sha": candidate.pull_request_head_sha,
        "pull_request_base_sha": candidate.pull_request_base_sha,
        "pull_request_head_branch": candidate.pull_request_head_branch,
        "source_sha": candidate.ci_candidate_source_sha,
        "source_tree": candidate.ci_candidate_source_tree,
        "workflow_path": CI_WORKFLOW_PATH,
        "workflow_run_id": candidate.ci_workflow_run_id,
        "workflow_run_attempt": candidate.ci_artifact_workflow_run_attempt,
    }
    if actual != expected:
        raise ReleaseCandidateError("CI validation identity does not match the selected source")


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
        "pull_request_head_branch": candidate.get("pull_request_head_branch"),
        "pull_request_base_sha": candidate.get("pull_request_base_sha"),
        "workflow_path": candidate.get("workflow_path"),
        "workflow_run_id": candidate.get("workflow_run_id"),
        "workflow_run_attempt": candidate.get("workflow_run_attempt"),
        "artifact_workflow_run_attempt": candidate.get("artifact_workflow_run_attempt"),
        "installer_smoke_workflow_run_attempt": candidate.get("installer_smoke_workflow_run_attempt"),
        "ci_workflow_run_id": candidate.get("ci_workflow_run_id"),
        "ci_workflow_run_attempt": candidate.get("ci_workflow_run_attempt"),
        "ci_artifact_workflow_run_attempt": candidate.get("ci_artifact_workflow_run_attempt"),
        "ci_candidate_source_sha": candidate.get("ci_candidate_source_sha"),
        "ci_candidate_source_tree": candidate.get("ci_candidate_source_tree"),
        "ci_artifact_id": candidate.get("ci_artifact_id"),
        "ci_artifact_name": candidate.get("ci_artifact_name"),
        "ci_artifact_digest": candidate.get("ci_artifact_digest"),
        "ci_artifact_expires_at": candidate.get("ci_artifact_expires_at"),
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
        "ci_candidate_source_sha",
        "ci_candidate_source_tree",
        "pull_request_head_sha",
        "pull_request_head_branch",
        "pull_request_base_sha",
        "workflow_path",
        "artifact_name",
        "artifact_digest",
        "artifact_expires_at",
        "ci_artifact_name",
        "ci_artifact_digest",
        "ci_artifact_expires_at",
    )
    if any(not isinstance(record[key], str) or not record[key] for key in required_strings):
        raise ReleaseCandidateError("selected candidate is missing required identity fields")
    required_integers = (
        "pull_request_number",
        "workflow_run_id",
        "workflow_run_attempt",
        "artifact_workflow_run_attempt",
        "installer_smoke_workflow_run_attempt",
        "ci_workflow_run_id",
        "ci_workflow_run_attempt",
        "ci_artifact_workflow_run_attempt",
        "ci_artifact_id",
        "artifact_id",
    )
    for key in required_integers:
        _integer(record[key], f"selected candidate {key}")
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
    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--repository", required=True)
    resolve.add_argument("--release-commit-sha", required=True)
    resolve.add_argument("--release-version", required=True)
    resolve.add_argument("--current-run-id", type=int, required=True)
    resolve.add_argument("--decision-artifact-name", required=True)
    resolve.add_argument("--decision-directory", type=Path, required=True)
    resolve.add_argument("--output", type=Path, required=True)
    resolve.add_argument("--github-output", type=Path, required=True)
    download = subparsers.add_parser("download-artifact")
    download.add_argument("--repository", required=True)
    download.add_argument("--artifact-id", type=int, required=True)
    download.add_argument("--artifact-digest", required=True)
    download.add_argument("--destination", type=Path, required=True)
    download.add_argument("--expected-file", action="append", required=True)
    named = subparsers.add_parser("download-run-artifact")
    named.add_argument("--repository", required=True)
    named.add_argument("--run-id", type=int, required=True)
    named.add_argument("--artifact-name", required=True)
    named.add_argument("--destination", type=Path, required=True)
    named.add_argument("--expected-file", action="append", required=True)
    preparation = subparsers.add_parser("verify-preparation")
    preparation.add_argument("--candidate", type=Path, required=True)
    preparation.add_argument("--preparation", type=Path, required=True)
    ci_record = subparsers.add_parser("write-ci-validation")
    ci_record.add_argument("--repository", required=True)
    ci_record.add_argument("--pull-request-number", type=int, required=True)
    ci_record.add_argument("--pull-request-head-sha", required=True)
    ci_record.add_argument("--pull-request-base-sha", required=True)
    ci_record.add_argument("--pull-request-head-branch", required=True)
    ci_record.add_argument("--source-sha", required=True)
    ci_record.add_argument("--source-tree", required=True)
    ci_record.add_argument("--workflow-run-id", type=int, required=True)
    ci_record.add_argument("--workflow-run-attempt", type=int, required=True)
    ci_record.add_argument("--output", type=Path, required=True)
    ci_verify = subparsers.add_parser("verify-ci-validation")
    ci_verify.add_argument("--candidate", type=Path, required=True)
    ci_verify.add_argument("--identity", type=Path, required=True)
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
        if args.command in {"select", "resolve", "download-artifact", "download-run-artifact"}:
            api = GitHubApi(
                args.repository,
                os.environ.get("GH_TOKEN", ""),
                os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            )
            if args.command == "select":
                candidate = select_release_candidate(api, args.release_commit_sha, args.release_version)
                args.output.write_text(json.dumps(asdict(candidate), indent=2) + "\n", encoding="utf-8")
                write_github_outputs(args.github_output, candidate)
            elif args.command == "resolve":
                candidate, reused = resolve_release_candidate(
                    api,
                    args.release_commit_sha,
                    args.release_version,
                    args.current_run_id,
                    args.decision_artifact_name,
                    args.decision_directory,
                    args.output,
                )
                write_github_outputs(args.github_output, candidate)
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write(f"decision_reused={str(reused).lower()}\n")
            elif args.command == "download-artifact":
                download_verified_artifact(
                    api,
                    args.artifact_id,
                    args.artifact_digest,
                    args.destination,
                    args.expected_file,
                )
            else:
                download_named_run_artifact(
                    api,
                    args.run_id,
                    args.artifact_name,
                    args.destination,
                    args.expected_file,
                )
        elif args.command == "verify-preparation":
            verify_preparation_binding(args.candidate, args.preparation)
        elif args.command == "write-ci-validation":
            write_ci_validation_identity(
                args.output,
                args.repository,
                args.pull_request_number,
                args.pull_request_head_sha,
                args.pull_request_base_sha,
                args.pull_request_head_branch,
                args.source_sha,
                args.source_tree,
                args.workflow_run_id,
                args.workflow_run_attempt,
            )
        elif args.command == "verify-ci-validation":
            verify_ci_validation_binding(args.candidate, args.identity)
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
