from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


QUEUE_SCHEMA_VERSION = 1
QUEUE_STATUSES = {"pending", "running", "success", "failed", "canceled", "interrupted", "stale"}


class ProcessingQueueError(RuntimeError):
    pass


@dataclass
class QueueStage:
    name: str
    status: str = "pending"
    progress: float = 0.0
    output_path: str = ""
    output_fingerprint: str = ""
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class QueueItem:
    item_id: str
    input_path: str
    project_path: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    input_fingerprint: str = ""
    status: str = "pending"
    stages: list[QueueStage] = field(default_factory=list)
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "input_path": self.input_path,
            "project_path": self.project_path,
            "settings": _safe_settings(self.settings),
            "input_fingerprint": self.input_fingerprint,
            "status": self.status,
            "stages": [stage.to_json() for stage in self.stages],
            "error": self.error,
        }


class ProcessingQueue:
    def __init__(self, path: str | Path, *, max_concurrency: int = 1) -> None:
        self.path = Path(path)
        self.max_concurrency = max(1, int(max_concurrency))
        self.items: list[QueueItem] = []
        self._cancel = threading.Event()
        self.load()

    def add(
        self,
        input_path: str | Path,
        *,
        project_path: str | Path = "",
        settings: Mapping[str, Any] | None = None,
        stages: Iterable[str] = ("transcribe", "render"),
    ) -> QueueItem:
        item = QueueItem(
            item_id=uuid.uuid4().hex,
            input_path=str(input_path),
            project_path=str(project_path),
            settings=dict(settings or {}),
            input_fingerprint=fingerprint_path(input_path),
            stages=[QueueStage(name=str(name)) for name in stages],
        )
        self.items.append(item)
        self.save()
        return item

    def remove(self, item_id: str) -> None:
        item = self.get(item_id)
        if item.status == "running":
            raise ProcessingQueueError("running queue item cannot be removed")
        self.items.remove(item)
        self.save()

    def reorder(self, item_id: str, new_index: int) -> None:
        item = self.get(item_id)
        self.items.remove(item)
        self.items.insert(max(0, min(len(self.items), int(new_index))), item)
        self.save()

    def get(self, item_id: str) -> QueueItem:
        for item in self.items:
            if item.item_id == item_id:
                return item
        raise ProcessingQueueError(f"queue item not found: {item_id}")

    def mark_interrupted_on_startup(self) -> list[QueueItem]:
        interrupted: list[QueueItem] = []
        for item in self.items:
            if item.status == "running":
                item.status = "interrupted"
                interrupted.append(item)
                for stage in item.stages:
                    if stage.status == "running":
                        stage.status = "interrupted"
        if interrupted:
            self.save()
        return interrupted

    def mark_stale(self, current_fingerprints: Mapping[str, str] | None = None) -> list[QueueItem]:
        stale: list[QueueItem] = []
        for item in self.items:
            current = (current_fingerprints or {}).get(item.item_id, fingerprint_path(item.input_path))
            if item.input_fingerprint and current != item.input_fingerprint and item.status not in {"running", "canceled"}:
                item.status = "stale"
                stale.append(item)
        if stale:
            self.save()
        return stale

    def cancel(self) -> None:
        self._cancel.set()

    def run_item(
        self,
        item_id: str,
        stage_runner: Callable[[QueueItem, QueueStage, Callable[[float], None], threading.Event], str | Path | None],
        *,
        output_validator: Callable[[Path], bool] | None = None,
        allow_overwrite: bool = False,
    ) -> QueueItem:
        item = self.get(item_id)
        if item.status == "stale":
            raise ProcessingQueueError("stale queue item requires requeue")
        self._cancel.clear()
        item.status = "running"
        item.error = ""
        self.save()
        try:
            for stage in item.stages:
                if stage.status == "success":
                    continue
                if self._cancel.is_set():
                    raise ProcessingQueueError("canceled")
                if stage.output_path and Path(stage.output_path).exists() and not allow_overwrite:
                    raise ProcessingQueueError(f"refusing to overwrite existing output: {stage.output_path}")
                stage.status = "running"
                stage.error = ""
                self.save()
                output = stage_runner(item, stage, lambda value: self._set_progress(item, stage, value), self._cancel)
                if output is not None:
                    output_path = Path(output)
                    if output_validator is not None and not output_validator(output_path):
                        raise ProcessingQueueError(f"output validation failed: {output_path}")
                    stage.output_path = str(output_path)
                    stage.output_fingerprint = fingerprint_path(output_path)
                stage.status = "success"
                stage.progress = 1.0
                self.save()
            item.status = "success"
        except ProcessingQueueError as error:
            item.status = "canceled" if str(error) == "canceled" else "failed"
            item.error = str(error)
            for stage in item.stages:
                if stage.status == "running":
                    stage.status = "canceled" if item.status == "canceled" else "failed"
                    stage.error = str(error)
        except Exception as error:
            item.status = "failed"
            item.error = str(error)
            for stage in item.stages:
                if stage.status == "running":
                    stage.status = "failed"
                    stage.error = str(error)
        self.save()
        return item

    def _set_progress(self, item: QueueItem, stage: QueueStage, value: float) -> None:
        stage.progress = max(0.0, min(1.0, float(value)))
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix="queue-", suffix=".tmp", delete=False)
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump({"schema_version": QUEUE_SCHEMA_VERSION, "max_concurrency": self.max_concurrency, "items": [item.to_json() for item in self.items]}, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def load(self) -> None:
        if not self.path.is_file():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != QUEUE_SCHEMA_VERSION:
            raise ProcessingQueueError("unsupported queue schema")
        self.max_concurrency = max(1, int(payload.get("max_concurrency", 1)))
        self.items = [
            QueueItem(
                item_id=str(item["item_id"]),
                input_path=str(item.get("input_path", "")),
                project_path=str(item.get("project_path", "")),
                settings=dict(item.get("settings", {})),
                input_fingerprint=str(item.get("input_fingerprint", "")),
                status=str(item.get("status", "pending")),
                stages=[QueueStage(**stage) for stage in item.get("stages", [])],
                error=str(item.get("error", "")),
            )
            for item in payload.get("items", [])
        ]


def fingerprint_path(path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_file():
        return "missing"
    stat = candidate.stat()
    return hashlib.sha256(f"{candidate.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()


def _safe_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in settings.items()
        if not any(secret in str(key).casefold() for secret in ("token", "password", "secret", "api_key", "authorization"))
    }
