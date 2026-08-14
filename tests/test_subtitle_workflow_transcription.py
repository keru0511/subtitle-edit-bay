import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.craig_pipeline import CraigTranscriptionBatch
from src.craig_transcription_execution import CraigTranscriptionHint
from src.subtitle_project import load_project
from src.subtitle_workflow_transcription import (
    build_default_workflow_transcription_hint,
    build_workflow_asr_settings,
    transcribe_craig_audio_files_for_workflow,
    transcribe_to_project_with_context,
)
from src.transcription_context import TranscriptionContext


class SubtitleWorkflowTranscriptionTests(unittest.TestCase):
    def test_empty_context_preserves_legacy_cache_behavior(self) -> None:
        with TemporaryDirectory() as temp_dir:
            transcript_dir = Path(temp_dir) / "transcripts"
            result = CraigTranscriptionBatch({}, [])
            with patch("src.subtitle_workflow_transcription.transcribe_craig_audio_files", return_value=result) as transcribe:
                returned = transcribe_craig_audio_files_for_workflow(
                    [Path("1-alice.flac")],
                    transcript_dir,
                    {"alice": "Oz"},
                    0.0,
                    transcription_context=None,
                )

        self.assertIs(returned, result)
        self.assertIsNone(transcribe.call_args.kwargs["default_transcription_hint"])

    def test_creator_context_builds_default_hint_and_cache_fingerprint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            hint = build_default_workflow_transcription_hint(
                {
                    "game_title": "Splatoon 3",
                    "creator_terms": ["ナワバリバトル"],
                },
                output_dir=temp_dir,
                asr_settings=build_workflow_asr_settings(device="cuda", compute_type="float16"),
            )

        self.assertIsInstance(hint, CraigTranscriptionHint)
        assert hint is not None
        self.assertIn("Splatoon 3", hint.initial_prompt)
        self.assertIn("ナワバリバトル", hint.hotwords)
        self.assertTrue(hint.cache_fingerprint)
        self.assertEqual(hint.cache_settings["asr"]["device"], "cuda")
        self.assertEqual(hint.cache_settings["asr"]["compute_type"], "float16")

    def test_confirmed_dictionary_is_resolved_relative_to_output_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / "dictionary.json").write_text(
                json.dumps(
                    {
                        "game_title": "Test Game",
                        "terms": [
                            {"term": "スプラッシュボム", "aliases": ["スプボム"], "enabled": True},
                            {"term": "未使用語", "enabled": False},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            hint = build_default_workflow_transcription_hint(
                {
                    "game_title": "Test Game",
                    "dictionary_path": "dictionary.json",
                    "dictionary_confirmed": True,
                },
                output_dir=output,
                asr_settings=build_workflow_asr_settings(),
            )

        assert hint is not None
        self.assertIn("スプラッシュボム", hint.hotwords)
        self.assertIn("スプボム", hint.hotwords)
        self.assertNotIn("未使用語", hint.hotwords)
        self.assertTrue(hint.cache_settings["dictionary_hash"])

    def test_web_dictionary_terms_enable_hint_generation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            hint = build_default_workflow_transcription_hint(
                {
                    "web_dictionary_enabled": True,
                    "web_dictionary_terms": ["Ink", "Bomba"],
                },
                output_dir=temp_dir,
                asr_settings=build_workflow_asr_settings(),
            )

        self.assertIsInstance(hint, CraigTranscriptionHint)
        assert hint is not None
        self.assertIn("ゲーム内用語", hint.initial_prompt)
        self.assertIn("Ink", hint.hotwords)

    def test_web_dictionary_terms_without_enable_do_not_build_hint(self) -> None:
        with TemporaryDirectory() as temp_dir:
            hint = build_default_workflow_transcription_hint(
                {
                    "web_dictionary_enabled": False,
                    "web_dictionary_terms": ["Ink", "Bomba"],
                },
                output_dir=temp_dir,
                asr_settings=build_workflow_asr_settings(),
            )

        self.assertIsNone(hint)

    def test_unconfirmed_dictionary_only_is_inert(self) -> None:
        with TemporaryDirectory() as temp_dir:
            hint = build_default_workflow_transcription_hint(
                {
                    "dictionary_path": "missing.json",
                    "dictionary_confirmed": False,
                },
                output_dir=temp_dir,
                asr_settings=build_workflow_asr_settings(),
            )

        self.assertIsNone(hint)

    def test_workflow_transcription_passes_default_hint_to_craig_pipeline(self) -> None:
        with TemporaryDirectory() as temp_dir:
            transcript_dir = Path(temp_dir) / "transcripts"
            result = CraigTranscriptionBatch({"audio": "transcript"}, [])
            with patch("src.subtitle_workflow_transcription.transcribe_craig_audio_files", return_value=result) as transcribe:
                returned = transcribe_craig_audio_files_for_workflow(
                    [Path("1-alice.flac")],
                    transcript_dir,
                    {"alice": "Oz"},
                    0.0,
                    transcription_context={"game_title": "Splatoon 3"},
                    model="large-v3",
                    device="cuda",
                    compute_type="float16",
                    language="ja",
                    vad_onset=0.25,
                    vad_offset=0.15,
                )

        self.assertIs(returned, result)
        hint = transcribe.call_args.kwargs["default_transcription_hint"]
        self.assertIsInstance(hint, CraigTranscriptionHint)
        self.assertEqual(transcribe.call_args.kwargs["device"], "cuda")
        self.assertEqual(transcribe.call_args.kwargs["compute_type"], "float16")

    def test_context_project_entrypoint_persists_context_and_passes_it_to_transcription(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_path = root / "game.mp4"
            audio_path = root / "1-alice.flac"
            video_path.write_bytes(b"video")
            audio_path.write_bytes(b"audio")
            segment = {
                "start": 0.0,
                "end": 1.0,
                "speaker": "Oz",
                "text": "hello",
                "layout_row": 0,
                "max_width": 24,
                "source_track": "craig:alice",
                "source_speaker": "alice",
                "source_file": audio_path.name,
            }
            batch = CraigTranscriptionBatch(
                {str(audio_path.resolve()): str((root / "transcripts" / "1-alice.json").resolve())},
                [segment],
            )
            with (
                patch("src.subtitle_workflow_transcription.resolve_alignment", return_value=("0:a:0", 0.25, 1.0)),
                patch("src.subtitle_workflow_transcription._build_waveforms", return_value=[]),
                patch("src.subtitle_workflow_transcription.refine_segments", return_value=([segment], [])),
                patch("src.subtitle_workflow_transcription.probe_media_duration", return_value=10.0),
                patch("src.subtitle_workflow_transcription.probe_audio_streams", return_value=[]),
                patch(
                    "src.subtitle_workflow_transcription.transcribe_craig_audio_files_for_workflow",
                    return_value=batch,
                ) as transcribe,
            ):
                project_path = transcribe_to_project_with_context(
                    video_path=str(video_path),
                    audio_files=[str(audio_path)],
                    output_dir=str(root / "export"),
                    transcription_context={
                        "game_title": "Splatoon 3",
                        "creator_terms": ["ナワバリバトル"],
                    },
                    subtitle_outline_color="#345678",
                    subtitle_outline_thickness=8,
                    overwrite_project=True,
                )

            project = load_project(project_path)

        self.assertEqual(project["transcription_context"]["game_title"], "Splatoon 3")
        self.assertEqual(project["transcription_context"]["creator_terms"], ["ナワバリバトル"])
        self.assertEqual(project["subtitle_settings"]["outline_color"], "#345678")
        self.assertEqual(project["subtitle_settings"]["outline_thickness"], 8)
        self.assertEqual(project["transcription"]["transcription_context"]["game_title"], "Splatoon 3")
        passed_context = transcribe.call_args.kwargs["transcription_context"]
        self.assertIsInstance(passed_context, TranscriptionContext)
        self.assertEqual(passed_context.game_title, "Splatoon 3")


if __name__ == "__main__":
    unittest.main()
