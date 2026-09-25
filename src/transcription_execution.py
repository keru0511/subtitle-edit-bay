from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping, Sequence

from .transcription_profile import DEFAULT_VAD_ONSET, DEFAULT_VAD_OFFSET
from .transcribe import (
    build_whisperx_command,
    expected_log_path,
    expected_transcript_path,
    run_command_with_utf8_log,
)
from .transcript_cache import (
    stable_payload_hash,
    transcript_cache_is_valid,
    transcript_cache_metadata_path,
    write_transcript_cache_metadata,
)
from .transcription_profile import first_pass_profile


@dataclass(frozen=True)
class TranscriptionExecutionResult:
    transcript_path: Path
    cache_hit: bool
    cache_metadata_path: Path | None = None


def transcribe_audio_with_cache(
    audio_path: str,
    output_dir: str,
    *,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    language: str | None = "ja",
    vad_onset: float | None = DEFAULT_VAD_ONSET,
    vad_offset: float | None = DEFAULT_VAD_OFFSET,
    initial_prompt: str | None = None,
    hotwords: Sequence[str] | str | None = None,
    skip_existing: bool = True,
    cache_fingerprint: str | None = None,
    cache_settings: Mapping[str, Any] | None = None,
) -> TranscriptionExecutionResult:
    """実際の認識設定・入力音声に一致する結果だけ再利用する。"""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    transcript_path = expected_transcript_path(audio_path, str(output))

    whisperx_command = build_whisperx_command(
        audio_path,
        str(output),
        model=model,
        device=device,
        compute_type=compute_type,
        language=language,
        vad_onset=vad_onset,
        vad_offset=vad_offset,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
    )
    source = Path(audio_path).resolve()
    try:
        stat = source.stat()
        source_identity = {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except FileNotFoundError:
        source_identity = {"path": str(source), "missing": True}
    try:
        whisperx_version = version("whisperx")
    except PackageNotFoundError:
        whisperx_version = "unavailable"
    effective_settings = {
        "profile": first_pass_profile(),
        "whisperx_version": whisperx_version,
        "source": source_identity,
        # コマンドのプロンプト等を平文でキャッシュに複製しない。
        "command_hash": stable_payload_hash(whisperx_command[2:]),
        "context_fingerprint": cache_fingerprint,
    }
    effective_fingerprint = stable_payload_hash(effective_settings)
    if skip_existing and transcript_cache_is_valid(transcript_path, expected_fingerprint=effective_fingerprint):
        return TranscriptionExecutionResult(transcript_path=transcript_path, cache_hit=True)

    # 強制再実行が失敗しても、前回のメタデータで成功扱いに戻さない。
    transcript_cache_metadata_path(transcript_path).unlink(missing_ok=True)
    run_command_with_utf8_log(whisperx_command, str(expected_log_path(audio_path, str(output))))
    if not transcript_path.is_file():
        raise FileNotFoundError(f"文字起こし結果が生成されませんでした: {transcript_path}")
    metadata_path = write_transcript_cache_metadata(
        transcript_path,
        fingerprint=effective_fingerprint,
        settings={**dict(cache_settings or {}), "execution": effective_settings},
    )
    return TranscriptionExecutionResult(
        transcript_path=transcript_path,
        cache_hit=False,
        cache_metadata_path=metadata_path,
    )
