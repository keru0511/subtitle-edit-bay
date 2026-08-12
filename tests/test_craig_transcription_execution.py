import unittest
from pathlib import Path
from unittest.mock import patch

from src.craig_transcription_execution import (
    CraigTranscriptionHint,
    resolve_craig_transcription_hint,
    transcribe_craig_audio_batch_with_cache,
    transcribe_craig_audio_file_with_cache,
)
from src.transcription_execution import TranscriptionExecutionResult


class CraigTranscriptionExecutionTests(unittest.TestCase):
    def test_resolves_hint_by_audio_name(self) -> None:
        hint = CraigTranscriptionHint(initial_prompt="game", hotwords=("weapon",))

        resolved = resolve_craig_transcription_hint(
            Path("/tmp/craig/001-alice.flac"),
            {"001-alice.flac": hint},
        )

        self.assertIs(resolved, hint)

    def test_resolves_hint_by_absolute_path(self) -> None:
        audio_path = Path("speaker.wav")
        hint = CraigTranscriptionHint(cache_fingerprint="fingerprint")

        resolved = resolve_craig_transcription_hint(
            audio_path,
            {str(audio_path.resolve()): hint},
        )

        self.assertIs(resolved, hint)

    def test_uses_empty_hint_when_no_mapping_matches(self) -> None:
        resolved = resolve_craig_transcription_hint("unknown.wav", {"other.wav": CraigTranscriptionHint(initial_prompt="x")})

        self.assertEqual(resolved.initial_prompt, "")
        self.assertEqual(resolved.hotwords, ())
        self.assertIsNone(resolved.cache_fingerprint)

    def test_passes_hint_and_cache_data_to_low_level_runner(self) -> None:
        result = TranscriptionExecutionResult(transcript_path=Path("out/audio.json"), cache_hit=False)
        hint = CraigTranscriptionHint(
            initial_prompt="Game title: Splatoon 3",
            hotwords=("ナワバリバトル", "スプラシューター"),
            cache_fingerprint="abc123",
            cache_settings={"model": "large-v3"},
        )

        with patch("src.craig_transcription_execution.transcribe_audio_with_cache", return_value=result) as transcribe:
            returned = transcribe_craig_audio_file_with_cache(
                "audio.flac",
                "transcripts",
                model="large-v3",
                device="cuda",
                compute_type="float16",
                language="ja",
                vad_onset=0.3,
                vad_offset=0.1,
                skip_existing_transcripts=True,
                hint=hint,
            )

        self.assertIs(returned, result)
        transcribe.assert_called_once_with(
            "audio.flac",
            "transcripts",
            model="large-v3",
            device="cuda",
            compute_type="float16",
            language="ja",
            vad_onset=0.3,
            vad_offset=0.1,
            initial_prompt="Game title: Splatoon 3",
            hotwords=("ナワバリバトル", "スプラシューター"),
            skip_existing=True,
            cache_fingerprint="abc123",
            cache_settings={"model": "large-v3"},
        )

    def test_legacy_call_omits_cache_fingerprint(self) -> None:
        result = TranscriptionExecutionResult(transcript_path=Path("out/audio.json"), cache_hit=True)

        with patch("src.craig_transcription_execution.transcribe_audio_with_cache", return_value=result) as transcribe:
            returned = transcribe_craig_audio_file_with_cache("audio.flac", "transcripts")

        self.assertTrue(returned.cache_hit)
        kwargs = transcribe.call_args.kwargs
        self.assertEqual(kwargs["initial_prompt"], "")
        self.assertEqual(kwargs["hotwords"], ())
        self.assertIsNone(kwargs["cache_fingerprint"])
        self.assertIsNone(kwargs["cache_settings"])

    def test_batch_preserves_transcript_map_and_result_order(self) -> None:
        audio_files = [Path("1-alice.flac"), Path("2-bob.flac")]
        first = TranscriptionExecutionResult(transcript_path=Path("transcripts/1-alice.json"), cache_hit=True)
        second = TranscriptionExecutionResult(transcript_path=Path("transcripts/2-bob.json"), cache_hit=False)

        with patch(
            "src.craig_transcription_execution.transcribe_audio_with_cache",
            side_effect=[first, second],
        ):
            batch = transcribe_craig_audio_batch_with_cache(
                audio_files,
                "transcripts",
            )

        self.assertEqual(
            batch.transcript_map,
            {
                str(audio_files[0].resolve()): str(first.transcript_path.resolve()),
                str(audio_files[1].resolve()): str(second.transcript_path.resolve()),
            },
        )
        self.assertEqual([result.audio_path for result in batch.results], audio_files)
        self.assertEqual([result.cache_hit for result in batch.results], [True, False])

    def test_batch_resolves_per_file_and_default_hints(self) -> None:
        audio_files = [Path("1-alice.flac"), Path("2-bob.flac")]
        alice_hint = CraigTranscriptionHint(initial_prompt="Alice prompt", cache_fingerprint="alice")
        default_hint = CraigTranscriptionHint(initial_prompt="Default prompt", cache_fingerprint="default")
        first = TranscriptionExecutionResult(transcript_path=Path("transcripts/1-alice.json"), cache_hit=False)
        second = TranscriptionExecutionResult(transcript_path=Path("transcripts/2-bob.json"), cache_hit=False)

        with patch(
            "src.craig_transcription_execution.transcribe_audio_with_cache",
            side_effect=[first, second],
        ) as transcribe:
            transcribe_craig_audio_batch_with_cache(
                audio_files,
                "transcripts",
                hints_by_audio={"1-alice.flac": alice_hint},
                default_hint=default_hint,
            )

        calls = transcribe.call_args_list
        self.assertEqual(calls[0].kwargs["initial_prompt"], "Alice prompt")
        self.assertEqual(calls[0].kwargs["cache_fingerprint"], "alice")
        self.assertEqual(calls[1].kwargs["initial_prompt"], "Default prompt")
        self.assertEqual(calls[1].kwargs["cache_fingerprint"], "default")


if __name__ == "__main__":
    unittest.main()
