from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.release_contract import ReleaseContractError, release_version_from_tag


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RELEASES_PER_PAGE = 100
MAX_RELEASE_PAGES = 1000


class ReleaseStateError(RuntimeError):
    """Raised when a tag or GitHub Release state cannot be established safely."""


@dataclass(frozen=True)
class GitHubReleaseState:
    draft: bool
    published: bool


def publication_action(state: GitHubReleaseState | None) -> str:
    if state is None:
        return "create"
    if state.published:
        return "reuse"
    if state.draft:
        return "publish-draft"
    raise ReleaseStateError("existing GitHub Release is neither a draft nor published")


def git_tag_exists(remote: str, release_version: str) -> bool:
    release_version_from_tag(release_version)
    completed = subprocess.run(
        ("git", "ls-remote", "--exit-code", remote, f"refs/tags/{release_version}"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 2:
        return False
    detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
    raise ReleaseStateError(f"could not query release tag {release_version}: {detail}")


def github_release_state(
    repository: str,
    release_version: str,
    token: str,
    *,
    api_url: str = "https://api.github.com",
) -> GitHubReleaseState | None:
    release_version_from_tag(release_version)
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ReleaseStateError(f"invalid GitHub repository: {repository}")
    if not token:
        raise ReleaseStateError("GH_TOKEN is required to query GitHub Releases")
    api_root = api_url.rstrip("/")
    quoted_tag = urllib.parse.quote(release_version, safe="")
    published_url = f"{api_root}/repos/{repository}/releases/tags/{quoted_tag}"

    published = _request_github_json(published_url, token, allow_not_found=True)
    if published is not None:
        return _parse_release(published, release_version)

    # The tag endpoint returns published releases only. An authenticated list is
    # required to distinguish a remaining draft from a genuinely unused tag.
    for page in range(1, MAX_RELEASE_PAGES + 1):
        query = urllib.parse.urlencode({"per_page": RELEASES_PER_PAGE, "page": page})
        releases_url = f"{api_root}/repos/{repository}/releases?{query}"
        payload = _request_github_json(releases_url, token, allow_not_found=False)
        if not isinstance(payload, list):
            raise ReleaseStateError("GitHub Releases list response must be an array")
        matches = [
            release for release in payload if isinstance(release, dict) and release.get("tag_name") == release_version
        ]
        if len(matches) > 1:
            raise ReleaseStateError(f"multiple GitHub Releases use tag {release_version}")
        if matches:
            return _parse_release(matches[0], release_version)
        if len(payload) < RELEASES_PER_PAGE:
            return None
    raise ReleaseStateError(f"GitHub Releases pagination exceeded {MAX_RELEASE_PAGES} pages")


def _request_github_json(url: str, token: str, *, allow_not_found: bool) -> object | None:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "subtitle-edit-bay-release-state",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and allow_not_found:
            return None
        raise ReleaseStateError(f"GitHub Release query failed with HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ReleaseStateError(f"GitHub Release query failed: {url}: {exc}") from exc


def _parse_release(payload: object, release_version: str) -> GitHubReleaseState:
    if not isinstance(payload, dict):
        raise ReleaseStateError("GitHub Release response must be an object")
    if payload.get("tag_name") != release_version:
        raise ReleaseStateError("GitHub Release response returned an unexpected tag")
    draft = payload.get("draft")
    published_at = payload.get("published_at")
    if not isinstance(draft, bool):
        raise ReleaseStateError("GitHub Release response has no valid draft state")
    if published_at is not None and not isinstance(published_at, str):
        raise ReleaseStateError("GitHub Release response has an invalid published_at value")
    return GitHubReleaseState(draft=draft, published=not draft and bool(published_at))


def write_github_outputs(path: Path, state: GitHubReleaseState | None) -> None:
    values = {
        "action": publication_action(state),
        "exists": str(state is not None).lower(),
        "draft": str(state.draft if state else False).lower(),
        "published": str(state.published if state else False).lower(),
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query immutable release state without hiding lookup failures.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    available = subparsers.add_parser("ensure-available")
    available.add_argument("--remote", default="origin")
    available.add_argument("--repository", required=True)
    available.add_argument("--release-version", required=True)

    inspect = subparsers.add_parser("inspect-release")
    inspect.add_argument("--repository", required=True)
    inspect.add_argument("--release-version", required=True)
    inspect.add_argument("--github-output", type=Path, required=True)

    published = subparsers.add_parser("assert-published")
    published.add_argument("--repository", required=True)
    published.add_argument("--release-version", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    try:
        if args.command == "ensure-available":
            if git_tag_exists(args.remote, args.release_version):
                raise ReleaseStateError(f"release tag already exists: {args.release_version}")
            if github_release_state(args.repository, args.release_version, token, api_url=api_url) is not None:
                raise ReleaseStateError(f"GitHub Release already exists: {args.release_version}")
        elif args.command == "inspect-release":
            state = github_release_state(args.repository, args.release_version, token, api_url=api_url)
            write_github_outputs(args.github_output, state)
        else:
            state = github_release_state(args.repository, args.release_version, token, api_url=api_url)
            if state is None:
                raise ReleaseStateError(f"GitHub Release does not exist: {args.release_version}")
            if not state.published:
                status = "draft" if state.draft else "not published"
                raise ReleaseStateError(f"GitHub Release is still {status}: {args.release_version}")
    except (ReleaseStateError, ReleaseContractError) as exc:
        print(f"Release state error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
