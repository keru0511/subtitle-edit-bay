from __future__ import annotations

import json
import os
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_MEMORY_CHARS = 50_000
DEFAULT_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 14

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)([^\s,;}\]]+)"),
    re.compile(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|authorization)"
        r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;}\]]+)"
    ),
)
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\"'<>|?*]*?\.[A-Za-z0-9]{1,12}(?![\w])"
)
_WINDOWS_PATH_TOKEN_PATTERN = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"']+")
_UNIX_PATH_PATTERN = re.compile(
    r"(?<![\w])/(?:[^\r\n\"'<>|?*]+/)*[^\r\n\"'<>|?*]*?\.[A-Za-z0-9]{1,12}(?![\w])"
)
_UNIX_PATH_TOKEN_PATTERN = re.compile(r"(?<![\w])/(?:[^\s\"']+/)+[^\s\"']+")


@dataclass(frozen=True)
class ProcessDiagnosticSnapshot:
    occurred_at: str
    job: str
    component: str
    stage: str
    status: str
    outcome: str
    exit_code: int | None
    process_error: str = ""
    log_text: str = ""
    related_log_tail: str = ""
    runtime: Mapping[str, object] | None = None


def redact_text(value: object, *, paths: bool = False) -> str:
    """Remove credentials from log text, optionally masking local paths."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    if paths:
        text = _WINDOWS_PATH_PATTERN.sub("<local-path>", text)
        text = _WINDOWS_PATH_TOKEN_PATTERN.sub("<local-path>", text)
        text = _UNIX_PATH_PATTERN.sub("<local-path>", text)
        text = _UNIX_PATH_TOKEN_PATTERN.sub("<local-path>", text)
    return text


def default_log_directory(workspace_root: str | Path) -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Subtitle Edit Bay" / "logs"
    return Path(workspace_root) / ".local" / "logs"


class ApplicationLogger:
    """Bounded in-memory log plus best-effort structured session file."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        log_directory: str | Path | None = None,
        application_info: Mapping[str, object] | None = None,
        max_memory_chars: int = DEFAULT_MEMORY_CHARS,
        max_file_bytes: int = DEFAULT_FILE_BYTES,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self.log_directory = Path(log_directory) if log_directory else default_log_directory(workspace_root)
        self.application_info = dict(application_info or {})
        self.max_memory_chars = max(1_000, int(max_memory_chars))
        self.max_file_bytes = max(1_024, int(max_file_bytes))
        self.retention_days = max(1, int(retention_days))
        self._memory: deque[str] = deque()
        self._memory_chars = 0
        self._write_error = ""
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        self.session_id = f"{stamp}-{os.getpid()}"
        self.log_path = self.log_directory / f"session-{self.session_id}.jsonl"
        self._ensure_directory()
        self._cleanup_old_logs()

    @property
    def write_error(self) -> str:
        return self._write_error

    @property
    def text(self) -> str:
        return "".join(self._memory)

    def clear_memory(self) -> None:
        self._memory.clear()
        self._memory_chars = 0

    def append(
        self,
        message: object,
        *,
        severity: str = "INFO",
        component: str = "gui",
        job: str = "",
        stage: str = "",
        process_id: int | None = None,
        exit_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        safe_message = redact_text(message)
        record: dict[str, Any] = {
            "timestamp": timestamp,
            "severity": str(severity).upper(),
            "component": str(component),
            "job": str(job),
            "stage": str(stage),
            "message": safe_message,
        }
        if process_id:
            record["process_id"] = int(process_id)
        if exit_code is not None:
            record["exit_code"] = int(exit_code)
        if retryable is not None:
            record["retryable"] = bool(retryable)

        display_prefix = f"{timestamp} [{record['severity']}] [{record['component']}]"
        if job:
            display_prefix += f" [{job}]"
        display = f"{display_prefix} {safe_message}\n"
        self._memory.append(display)
        self._memory_chars += len(display)
        while self._memory and self._memory_chars > self.max_memory_chars:
            self._memory_chars -= len(self._memory.popleft())

        self._write_record(record)

    def diagnostic_text(
        self,
        *,
        status: str = "",
        stage: str = "",
        exit_code: int | None = None,
        runtime: Mapping[str, object] | None = None,
        snapshot: ProcessDiagnosticSnapshot | None = None,
    ) -> str:
        occurred_at = datetime.now().astimezone().isoformat(timespec="seconds")
        job = ""
        component = ""
        outcome = ""
        process_error = ""
        log_text = self.text
        related_log_tail = ""
        if snapshot is not None:
            occurred_at = snapshot.occurred_at
            job = snapshot.job
            component = snapshot.component
            status = snapshot.status
            stage = snapshot.stage
            outcome = snapshot.outcome
            exit_code = snapshot.exit_code
            process_error = snapshot.process_error
            log_text = snapshot.log_text
            related_log_tail = snapshot.related_log_tail
            runtime = snapshot.runtime

        info_lines = [
            "Subtitle Edit Bay 診断情報",
            f"発生日時: {occurred_at}",
            f"version: {redact_text(self.application_info.get('version', 'unknown'))}",
            f"配布形態: {redact_text(self.application_info.get('distribution', 'unknown'))}",
        ]
        if job:
            info_lines.append(f"job: {redact_text(job)}")
        if component:
            info_lines.append(f"component: {redact_text(component)}")
        info_lines.extend(
            [
                f"工程: {redact_text(stage)}",
                f"status: {redact_text(status)}",
            ]
        )
        if outcome:
            outcome_labels = {
                "cancelled": "キャンセル (cancelled)",
                "failed": "異常終了 (failed)",
            }
            info_lines.append(f"結果: {outcome_labels.get(outcome, redact_text(outcome))}")
        info_lines.append(f"終了コード: {exit_code if exit_code is not None else 'なし'}")
        if process_error:
            info_lines.append(f"QProcessエラー: {redact_text(process_error, paths=True)}")
        for key, value in (runtime or {}).items():
            info_lines.append(f"{redact_text(key)}: {redact_text(value, paths=True)}")
        info_lines.extend(
            [
                "完全ログ: <local-path>",
                "直近の処理ログ:",
                redact_text(log_text, paths=True).rstrip(),
            ]
        )
        if related_log_tail:
            info_lines.extend(
                [
                    "関連ログ末尾:",
                    redact_text(related_log_tail, paths=True).rstrip(),
                ]
            )
        return "\n".join(info_lines).rstrip() + "\n"

    def _ensure_directory(self) -> None:
        try:
            self.log_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._write_error = str(error)

    def _write_record(self, record: Mapping[str, Any]) -> None:
        try:
            self._ensure_directory()
            if self.log_path.exists() and self.log_path.stat().st_size >= self.max_file_bytes:
                rotated = self.log_path.with_suffix(".1.jsonl")
                try:
                    rotated.unlink(missing_ok=True)
                    self.log_path.replace(rotated)
                except OSError:
                    pass
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._write_error = ""
        except OSError as error:
            self._write_error = str(error)

    def _cleanup_old_logs(self) -> None:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            for path in self.log_directory.glob("session-*.jsonl"):
                modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                if modified < cutoff:
                    path.unlink(missing_ok=True)
        except OSError as error:
            self._write_error = str(error)

