import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.craig_pipeline import CraigTranscriptionBatch
from src.craig_transcription_execution import CraigTranscriptionHint
from src.subtitle_workflow_transcription import (
    build_default_workflow_transcription_hint,
    build_workflow_asr_settings,
    transcribe_craig_audio_files_for_workflow,
)


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


if __name__ == "__main__":
    unittest.main()
