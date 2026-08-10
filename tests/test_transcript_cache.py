from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.transcript_cache import (
    build_transcript_cache_fingerprint,
    read_transcript_cache_metadata,
    stable_payload_hash,
    transcript_cache_is_valid,
    transcript_cache_metadata_path,
    write_transcript_cache_metadata,
)


class TranscriptCacheTests(unittest.TestCase):
    def test_fingerprint_changes_with_asr_context_and_dictionary_inputs(self) -> None:
        base = build_transcript_cache_fingerprint(
            model="large-v3",
            device="cuda",
            compute_type="float16",
            language="ja",
            vad_onset=0.35,
            vad_offset=0.2,
            initial_prompt="ゲームタイトル: Splatoon 3",
            hotwords=("ナワバリバトル",),
            dictionary_hash=stable_payload_hash({"terms": ["ナワバリバトル"]}),
            game_title="Splatoon 3",
        )

        changed_prompt = build_transcript_cache_fingerprint(
            model="large-v3",
            device="cuda",
            compute_type="float16",
            language="ja",
            vad_onset=0.35,
            vad_offset=0.2,
            initial_prompt="ゲームタイトル: Splatoon 3 DLC",
            hotwords=("ナワバリバトル",),
            dictionary_hash=stable_payload_hash({"terms": ["ナワバリバトル"]}),
            game_title="Splatoon 3",
        )
        changed_hotwords = build_transcript_cache_fingerprint(
            model="large-v3",
            device="cuda",
            compute_type="float16",
            language="ja",
            vad_onset=0.35,
            vad_offset=0.2,
            initial_prompt="ゲームタイトル: Splatoon 3",
            hotwords=("ガチエリア",),
            dictionary_hash=stable_payload_hash({"terms": ["ナワバリバトル"]}),
            game_title="Splatoon 3",
        )
        changed_vad = build_transcript_cache_fingerprint(
            model="large-v3",
            device="cuda",
            compute_type="float16",
            language="ja",
            vad_onset=0.4,
            vad_offset=0.2,
            initial_prompt="ゲームタイトル: Splatoon 3",
            hotwords=("ナワバリバトル",),
            dictionary_hash=stable_payload_hash({"terms": ["ナワバリバトル"]}),
            game_title="Splatoon 3",
        )

        self.assertNotEqual(base, changed_prompt)
        self.assertNotEqual(base, changed_hotwords)
        self.assertNotEqual(base, changed_vad)

    def test_cache_validity_preserves_legacy_mode_when_no_fingerprint_is_expected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "1-alice.json"
            transcript.write_text("{}", encoding="utf-8")

            self.assertTrue(transcript_cache_is_valid(transcript))
            self.assertFalse(transcript_cache_is_valid(transcript.with_name("missing.json")))

    def test_cache_requires_matching_metadata_when_fingerprint_is_expected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "1-alice.json"
            transcript.write_text("{}", encoding="utf-8")
            expected = build_transcript_cache_fingerprint(
                model="large-v3",
                device="cpu",
                compute_type="int8",
                language="ja",
                vad_onset=0.35,
                vad_offset=0.2,
            )

            self.assertFalse(transcript_cache_is_valid(transcript, expected_fingerprint=expected))
            metadata_path = write_transcript_cache_metadata(
                transcript,
                fingerprint=expected,
                settings={"model": "large-v3"},
            )

            self.assertEqual(metadata_path, transcript_cache_metadata_path(transcript))
            self.assertTrue(transcript_cache_is_valid(transcript, expected_fingerprint=expected))
            self.assertEqual(read_transcript_cache_metadata(transcript)["settings"]["model"], "large-v3")
            self.assertFalse(transcript_cache_is_valid(transcript, expected_fingerprint="different"))

    def test_invalid_metadata_is_treated_as_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            transcript = Path(temp_dir) / "1-alice.json"
            transcript.write_text("{}", encoding="utf-8")
            transcript_cache_metadata_path(transcript).write_text("not json", encoding="utf-8")

            self.assertIsNone(read_transcript_cache_metadata(transcript))
            self.assertFalse(transcript_cache_is_valid(transcript, expected_fingerprint="expected"))


if __name__ == "__main__":
    unittest.main()
