"""Local YouTube post-package generation without upload or account access."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


PACKAGE_SCHEMA_VERSION = 1
PLATFORM_RULES_VERSION = 1


class YouTubePackageError(ValueError):
    """Raised when a post package is incomplete or unsafe to write."""


@dataclass(frozen=True)
class Chapter:
    id: str
    start: float
    title: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise YouTubePackageError(f"{field} must be numeric") from exc
    if result < 0:
        raise YouTubePackageError(f"{field} must not be negative")
    return result


def _chapter(value: Chapter | Mapping[str, Any], index: int) -> Chapter:
    if isinstance(value, Chapter):
        chapter = value
    elif isinstance(value, Mapping):
        chapter = Chapter(
            id=str(value.get("id", f"chapter-{index + 1}")),
            start=_number(value.get("start"), "chapter start"),
            title=str(value.get("title", "")).strip(),
        )
    else:
        raise YouTubePackageError(f"chapter {index} must be an object")
    if not chapter.id or not chapter.title:
        raise YouTubePackageError(f"chapter {index} needs an id and title")
    if len(chapter.title) > 100:
        raise YouTubePackageError(f"chapter {chapter.id} title is too long")
    return chapter


def validate_chapters(
    chapters: Iterable[Chapter | Mapping[str, Any]], *, duration: float | None = None
) -> list[Chapter]:
    normalized = [_chapter(value, index) for index, value in enumerate(chapters)]
    seen_ids: set[str] = set()
    seen_starts: set[float] = set()
    for chapter in normalized:
        if chapter.id in seen_ids:
            raise YouTubePackageError(f"duplicate chapter id: {chapter.id}")
        if chapter.start in seen_starts:
            raise YouTubePackageError(f"duplicate chapter time: {chapter.start}")
        if duration is not None and chapter.start >= _number(duration, "duration"):
            raise YouTubePackageError(f"chapter starts after duration: {chapter.id}")
        seen_ids.add(chapter.id)
        seen_starts.add(chapter.start)
    return sorted(normalized, key=lambda item: (item.start, item.id))


def add_chapter(chapters: Sequence[Chapter | Mapping[str, Any]], chapter: Chapter | Mapping[str, Any]) -> list[Chapter]:
    return validate_chapters([*chapters, chapter])


def rename_chapter(chapters: Sequence[Chapter | Mapping[str, Any]], chapter_id: str, title: str) -> list[Chapter]:
    updated = []
    found = False
    for index, value in enumerate(chapters):
        chapter = _chapter(value, index)
        if chapter.id == chapter_id:
            chapter = Chapter(chapter.id, chapter.start, title.strip())
            found = True
        updated.append(chapter)
    if not found:
        raise YouTubePackageError(f"unknown chapter: {chapter_id}")
    return validate_chapters(updated)


def adjust_chapter(chapters: Sequence[Chapter | Mapping[str, Any]], chapter_id: str, start: float) -> list[Chapter]:
    updated = []
    found = False
    for index, value in enumerate(chapters):
        chapter = _chapter(value, index)
        if chapter.id == chapter_id:
            chapter = Chapter(chapter.id, _number(start, "chapter start"), chapter.title)
            found = True
        updated.append(chapter)
    if not found:
        raise YouTubePackageError(f"unknown chapter: {chapter_id}")
    return validate_chapters(updated)


def delete_chapter(chapters: Sequence[Chapter | Mapping[str, Any]], chapter_id: str) -> list[Chapter]:
    updated = [_chapter(value, index) for index, value in enumerate(chapters) if _chapter(value, index).id != chapter_id]
    if len(updated) == len(chapters):
        raise YouTubePackageError(f"unknown chapter: {chapter_id}")
    return validate_chapters(updated)


def _format_chapter_time(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"


def _settings_fingerprint(settings: Mapping[str, Any]) -> str:
    encoded = json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_chapters(project: Mapping[str, Any], chapters: Iterable[Chapter | Mapping[str, Any]] | None = None) -> list[Chapter]:
    values = chapters if chapters is not None else project.get("chapters", [])
    duration = project.get("duration")
    return validate_chapters(values, duration=float(duration) if duration is not None else None)


def build_post_package(
    project: Mapping[str, Any],
    *,
    chapters: Iterable[Chapter | Mapping[str, Any]] | None = None,
    thumbnail_candidates: Sequence[Mapping[str, Any]] = (),
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(project, Mapping):
        raise YouTubePackageError("project must be an object")
    settings_copy = copy.deepcopy(dict(settings or {}))
    youtube_text = project.get("youtube_text", {})
    if not isinstance(youtube_text, Mapping):
        youtube_text = {}
    chapter_values = build_chapters(project, chapters)
    title = str(youtube_text.get("title", project.get("title", ""))).strip()
    description = str(youtube_text.get("description", project.get("description", "")))
    pinned_comment = str(youtube_text.get("pinned_comment", project.get("pinned_comment", "")))
    keywords = [str(value).strip() for value in youtube_text.get("keywords", project.get("keywords", [])) if str(value).strip()]
    revision = project.get("revision")
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "platform": "youtube",
        "platform_rules_version": PLATFORM_RULES_VERSION,
        "revision": revision,
        "settings_fingerprint": _settings_fingerprint(settings_copy),
        "title": title,
        "description": description,
        "pinned_comment": pinned_comment,
        "keywords": keywords,
        "chapters": [chapter.to_json() for chapter in chapter_values],
        "chapters_text": "\n".join(f"{_format_chapter_time(chapter.start)} {chapter.title}" for chapter in chapter_values),
        "thumbnail_candidates": copy.deepcopy(list(thumbnail_candidates)),
        "upload": {"enabled": False, "account": None},
    }


def is_stale(package: Mapping[str, Any], current_revision: Any, settings: Mapping[str, Any] | None = None) -> bool:
    if package.get("revision") != current_revision:
        return True
    if settings is not None and package.get("settings_fingerprint") != _settings_fingerprint(settings):
        return True
    return False


def _write_package_files(directory: Path, package: Mapping[str, Any]) -> None:
    (directory / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "title.txt").write_text(str(package["title"]) + "\n", encoding="utf-8")
    (directory / "description.txt").write_text(str(package["description"]), encoding="utf-8")
    (directory / "pinned-comment.txt").write_text(str(package["pinned_comment"]), encoding="utf-8")
    (directory / "keywords.txt").write_text(",".join(package["keywords"]) + "\n", encoding="utf-8")
    (directory / "chapters.txt").write_text(str(package["chapters_text"]) + ("\n" if package["chapters_text"] else ""), encoding="utf-8")
    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "revision": package.get("revision"),
        "settings_fingerprint": package.get("settings_fingerprint"),
        "files": ["package.json", "title.txt", "description.txt", "pinned-comment.txt", "keywords.txt", "chapters.txt"],
        "upload_performed": False,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_post_package(
    project: Mapping[str, Any],
    destination: str | os.PathLike[str],
    *,
    chapters: Iterable[Chapter | Mapping[str, Any]] | None = None,
    thumbnail_candidates: Sequence[Mapping[str, Any]] = (),
    settings: Mapping[str, Any] | None = None,
    overwrite: bool = False,
) -> Path:
    path = Path(destination)
    if path.exists() and not overwrite:
        raise YouTubePackageError(f"package directory already exists: {path}")
    package = build_post_package(project, chapters=chapters, thumbnail_candidates=thumbnail_candidates, settings=settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    backup: Path | None = None
    existing_moved = False
    try:
        _write_package_files(temporary, package)
        if path.exists():
            descriptor, backup_name = tempfile.mkstemp(prefix=f".{path.name}.backup-", dir=path.parent)
            os.close(descriptor)
            os.unlink(backup_name)
            backup = Path(backup_name)
            os.replace(path, backup)
            existing_moved = True
        try:
            os.replace(temporary, path)
        except Exception:
            if path.exists():
                _remove_package_path(path)
            if existing_moved and backup is not None and backup.exists():
                os.replace(backup, path)
                existing_moved = False
            raise
        if existing_moved and backup is not None:
            _remove_package_path(backup)
            existing_moved = False
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if existing_moved and backup is not None and backup.exists() and not path.exists():
            os.replace(backup, path)
        raise
    return path


def _remove_package_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
