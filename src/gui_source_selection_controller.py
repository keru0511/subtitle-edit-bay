from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .color_config import normalize_rgb_color, save_speaker_color
from .gui_source_state import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    SourceSelection,
    build_speaker_entries_from_files,
)
from .media_probe import probe_media_stream_types
from .transcribe import probe_audio_streams


@dataclass(frozen=True)
class SourceSelectionUpdate:
    """Result of a source selection mutation.

    The Qt facade uses the change flags to emit the existing signals and to
    reset alignment state.  Keeping that dispatch outside this controller
    makes the source-selection rules testable without starting Qt.
    """

    accepted: bool
    reason: str = ""
    previous: SourceSelection | None = None
    current: SourceSelection | None = None
    video_changed: bool = False
    media_changed: bool = False
    output_changed: bool = False


@dataclass(frozen=True)
class SourceDropResult:
    """Validated files and updates produced by a drag-and-drop operation."""

    accepted: bool
    reason: str = ""
    updates: tuple[SourceSelectionUpdate, ...] = ()
    skipped_videos: int = 0
    ignored_count: int = 0


@dataclass(frozen=True)
class SpeakerColorUpdate:
    accepted: bool
    reason: str = ""
    speaker: dict[str, str] | None = None


class SourceSelectionController:
    """Own GUI media-source selection and derived source lists.

    This class deliberately has no Qt dependency.  It owns the mutable
    ``SourceSelection``, speaker-source entries, and probed video tracks while
    the Qt backend remains responsible for QML properties, signals, status
    messages, file dialogs, and project/editor side effects.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        color_config_path: str | Path | None = None,
        media_stream_probe: Callable[[str], set[str]] = probe_media_stream_types,
        audio_stream_probe: Callable[[str], Iterable[dict[str, Any]]] = probe_audio_streams,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.color_config_path = (
            Path(color_config_path)
            if color_config_path is not None
            else self.workspace_root / "assets" / "speaker_colors.json"
        )
        self._media_stream_probe = media_stream_probe
        self._audio_stream_probe = audio_stream_probe
        self._source_selection = SourceSelection()
        self._speakers: list[dict[str, str]] = []
        self._audio_tracks = self.default_audio_tracks()

    @staticmethod
    def default_audio_tracks() -> list[dict[str, str]]:
        return [{"selector": "", "label": "自動検出（推奨）"}]

    @property
    def source_selection(self) -> SourceSelection:
        return self._source_selection

    @property
    def speakers(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._speakers]

    @property
    def mutable_speakers(self) -> list[dict[str, str]]:
        """Return the owned list for backend-only project color reconciliation."""

        return self._speakers

    @property
    def audio_tracks(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._audio_tracks]

    def set_speakers(self, speakers: Iterable[dict[str, str]]) -> None:
        """Set the derived speaker list for legacy test/integration callers."""

        self._speakers = [dict(item) for item in speakers]

    def set_audio_tracks(self, tracks: Iterable[dict[str, str]]) -> None:
        """Set probed tracks for the audio-mixer and contract tests."""

        self._audio_tracks = [dict(item) for item in tracks]

    def set_selection(self, selection: SourceSelection) -> SourceSelectionUpdate:
        previous = self._source_selection
        video_changed = previous.video != selection.video
        media_changed = video_changed or previous.audio_files != selection.audio_files
        output_changed = previous.output_dir != selection.output_dir
        self._source_selection = selection
        if media_changed:
            self._speakers = build_speaker_entries_from_files(
                selection.audio_files,
                self.color_config_path,
            )
        return SourceSelectionUpdate(
            accepted=True,
            previous=previous,
            current=selection,
            video_changed=video_changed,
            media_changed=media_changed,
            output_changed=output_changed,
        )

    def reset(self) -> SourceSelectionUpdate:
        update = self.set_selection(SourceSelection())
        self._speakers = []
        self._audio_tracks = self.default_audio_tracks()
        return update

    @staticmethod
    def _failure(reason: str) -> SourceSelectionUpdate:
        return SourceSelectionUpdate(accepted=False, reason=reason)

    def validate_media_file(
        self,
        source: str | Path,
        required_streams: set[str],
        label: str,
        *,
        ffprobe_available: bool = True,
    ) -> tuple[bool, str]:
        candidate = Path(source)
        if candidate.suffix.lower() not in VIDEO_EXTENSIONS | AUDIO_EXTENSIONS:
            return False, f"{label} の拡張子が対応していません"
        if not ffprobe_available:
            return True, ""
        try:
            detected = self._media_stream_probe(str(candidate))
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            return False, f"{label} の検証に失敗しました: {error}"
        if not required_streams.intersection(detected):
            return False, f"{label} は指定された種類のメディアではありません"
        return True, ""

    def set_video_file(
        self,
        path: str | Path,
        *,
        ffprobe_available: bool = True,
        validator: Callable[[Path, set[str], str], tuple[bool, str]] | None = None,
    ) -> SourceSelectionUpdate:
        video = Path(path)
        if not video.is_file():
            return self._failure("対応する動画ファイルを指定してください")
        valid, reason = (
            validator(video, {"video"}, "動画ファイル")
            if validator is not None
            else self.validate_media_file(
                video,
                {"video"},
                "動画ファイル",
                ffprobe_available=ffprobe_available,
            )
        )
        if not valid:
            return self._failure(reason or "対応する動画ファイルを指定してください")
        return self.set_selection(replace(self._source_selection, video=str(video.resolve())))

    def set_audio_files(
        self,
        paths: Iterable[str | Path],
        append: bool,
        *,
        ffprobe_available: bool = True,
        validator: Callable[[Path, set[str], str], tuple[bool, str]] | None = None,
    ) -> SourceSelectionUpdate:
        valid_files: list[str] = []
        for value in paths:
            audio = Path(value)
            if not audio.is_file():
                continue
            valid, _reason = (
                validator(audio, {"audio"}, "音声ファイル")
                if validator is not None
                else self.validate_media_file(
                    audio,
                    {"audio"},
                    "音声ファイル",
                    ffprobe_available=ffprobe_available,
                )
            )
            if valid:
                valid_files.append(str(audio.resolve()))
        if not valid_files:
            return self._failure("対応する話者音声ファイルを指定してください")

        existing = list(self._source_selection.audio_files) if append else []
        combined = sorted(
            dict.fromkeys([*existing, *valid_files]),
            key=lambda path: (Path(path).name.casefold(), path.casefold()),
        )
        return self.set_selection(replace(self._source_selection, audio_files=tuple(combined)))

    def set_output_directory(self, path: str | Path) -> SourceSelectionUpdate:
        output = Path(path)
        if not output.is_dir():
            return self._failure("存在する出力フォルダを指定してください")
        return self.set_selection(replace(self._source_selection, output_dir=str(output.resolve())))

    def import_dropped_source_files(
        self,
        values: Iterable[str | Path],
        *,
        ffprobe_available: bool = True,
        validator: Callable[[Path, set[str], str], tuple[bool, str]] | None = None,
    ) -> SourceDropResult:
        video_files: list[str] = []
        audio_files: list[str] = []
        ignored_count = 0
        for value in values:
            source = Path(value)
            if not source.is_file():
                ignored_count += 1
                continue
            resolved = str(source.resolve())
            video_valid, _video_reason = (
                validator(source, {"video"}, "動画ファイル")
                if validator is not None
                else self.validate_media_file(
                    source,
                    {"video"},
                    "動画ファイル",
                    ffprobe_available=ffprobe_available,
                )
            )
            if video_valid:
                video_files.append(resolved)
                continue
            audio_valid, _audio_reason = (
                validator(source, {"audio"}, "音声ファイル")
                if validator is not None
                else self.validate_media_file(
                    source,
                    {"audio"},
                    "音声ファイル",
                    ffprobe_available=ffprobe_available,
                )
            )
            if audio_valid:
                audio_files.append(resolved)
            else:
                ignored_count += 1

        if not video_files and not audio_files:
            return SourceDropResult(
                accepted=False,
                reason="対応する動画または話者音声をドロップしてください",
                ignored_count=ignored_count,
            )

        updates: list[SourceSelectionUpdate] = []
        if video_files:
            updates.append(
                self.set_selection(replace(self._source_selection, video=video_files[0]))
            )
        if audio_files:
            update = self.set_audio_files(
                audio_files,
                True,
                ffprobe_available=ffprobe_available,
                validator=validator,
            )
            if update.accepted:
                updates.append(update)

        return SourceDropResult(
            accepted=True,
            updates=tuple(updates),
            skipped_videos=max(0, len(video_files) - 1),
            ignored_count=ignored_count,
        )

    def remove_audio_file(self, index: int) -> SourceSelectionUpdate | None:
        audio_files = list(self._source_selection.audio_files)
        if not 0 <= index < len(audio_files):
            return None
        audio_files.pop(index)
        return self.set_selection(replace(self._source_selection, audio_files=tuple(audio_files)))

    def clear_audio_files(self) -> SourceSelectionUpdate:
        return self.set_selection(replace(self._source_selection, audio_files=()))

    def probe_audio_tracks(
        self,
        video_path: str,
        *,
        ffprobe_available: bool = True,
    ) -> tuple[list[dict[str, str]], str]:
        tracks = self.default_audio_tracks()
        error_message = ""
        if ffprobe_available and video_path and Path(video_path).is_file():
            try:
                for audio_index, stream in enumerate(self._audio_stream_probe(video_path)):
                    selector = f"0:a:{audio_index}"
                    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
                    title = str(tags.get("title", "")).strip()
                    codec = str(stream.get("codec_name", "audio"))
                    channels = stream.get("channels", "?")
                    detail = title or f"{codec} / {channels}ch"
                    tracks.append({"selector": selector, "label": f"{selector}  {detail}"})
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                error_message = f"動画音声トラックを取得できません: {error}"
        self._audio_tracks = tracks
        return self.audio_tracks, error_message

    def update_speaker_color(self, index: int, color: str) -> SpeakerColorUpdate:
        if not 0 <= index < len(self._speakers):
            return SpeakerColorUpdate(False)
        speaker = self._speakers[index]
        try:
            normalized = normalize_rgb_color(color)
            save_speaker_color(
                self.color_config_path,
                file_name=speaker.get("file_name", ""),
                speaker_name=speaker.get("name", ""),
                color=normalized,
            )
        except (OSError, ValueError, TypeError) as error:
            return SpeakerColorUpdate(False, f"話者色を保存できません: {error}")
        updated = {**speaker, "color": normalized}
        self._speakers[index] = updated
        return SpeakerColorUpdate(True, speaker=updated)


__all__ = [
    "SourceDropResult",
    "SourceSelectionController",
    "SourceSelectionUpdate",
    "SpeakerColorUpdate",
]
