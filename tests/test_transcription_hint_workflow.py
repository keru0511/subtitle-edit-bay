from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.transcription_hint_plan import TranscriptionAsrSettings
from src.transcription_hint_workflow import (
    TranscriptionHintWorkflowError,
    build_craig_hint_plan_from_context,
    load_confirmed_transcription_dictionary,
    resolve_confirmed_dictionary_path,
)


def write_dictionary(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "game_title": "Test Game",
                "terms": [
                    {
                        "term": "スプラッシュボム",
                        "aliases": ["スプボム"],
                        "type_hint": "item",
                        "enabled": True,
                    },
                    {
                        "term": "未使用語",
                        "enabled": False,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TranscriptionHintWorkflowTests(unittest.TestCase):
    def test_unconfirmed_dictionary_path_is_inert(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            context = {
                "game_title": "Test Game",
                "creator_terms": ["作成者語"],
                "dictionary_path": "missing.json",
                "dictionary_confirmed": False,
            }

            self.assertIsNone(resolve_confirmed_dictionary_path(context, base_dir=tmp_path))
            self.assertIsNone(load_confirmed_transcription_dictionary(context, base_dir=tmp_path))

            plan = build_craig_hint_plan_from_context(context, base_dir=tmp_path)

            self.assertIn("作成者語", plan.hint.hotwords)
            self.assertNotIn("スプラッシュボム", plan.hint.hotwords)
            self.assertEqual(plan.dictionary_hash, "")
            self.assertEqual(plan.cache_settings["dictionary_hash"], "")

    def test_web_dictionary_terms_are_applied_by_workflow(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            context = {
                "web_dictionary_enabled": True,
                "web_dictionary_terms": ["Ink", "Bomba"],
            }

            plan = build_craig_hint_plan_from_context(context, base_dir=tmp_path)

            self.assertIn("Ink", plan.hint.hotwords)
            self.assertIn("ゲーム内用語", plan.hint.initial_prompt)

    def test_web_dictionary_disabled_is_not_used(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            context = {
                "web_dictionary_enabled": False,
                "web_dictionary_terms": ["Ink"],
            }

            plan = build_craig_hint_plan_from_context(context, base_dir=tmp_path)

            self.assertNotIn("Ink", plan.hint.hotwords)
            self.assertNotIn("ゲーム内用語", plan.hint.initial_prompt)

    def test_confirmed_relative_dictionary_loads_and_affects_hints(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            dictionary_path = tmp_path / "dictionary.json"
            write_dictionary(dictionary_path)
            context = {
                "game_title": "Test Game",
                "dictionary_path": "dictionary.json",
                "dictionary_confirmed": True,
            }

            resolved = resolve_confirmed_dictionary_path(context, base_dir=tmp_path)
            self.assertEqual(resolved, dictionary_path)

            plan = build_craig_hint_plan_from_context(context, base_dir=tmp_path)

            self.assertIn("スプラッシュボム", plan.hint.hotwords)
            self.assertIn("スプボム", plan.hint.hotwords)
            self.assertNotIn("未使用語", plan.hint.hotwords)
            self.assertTrue(plan.dictionary_hash)
            self.assertEqual(plan.cache_settings["dictionary_hash"], plan.dictionary_hash)

    def test_confirmed_missing_dictionary_raises(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            context = {
                "game_title": "Test Game",
                "dictionary_path": "missing.json",
                "dictionary_confirmed": True,
            }

            with self.assertRaisesRegex(TranscriptionHintWorkflowError, "confirmed transcription dictionary"):
                build_craig_hint_plan_from_context(context, base_dir=tmp_path)

    def test_asr_settings_flow_into_workflow_cache_settings(self) -> None:
        with TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            dictionary_path = tmp_path / "dictionary.json"
            write_dictionary(dictionary_path)
            context = {
                "game_title": "Test Game",
                "dictionary_path": "dictionary.json",
                "dictionary_confirmed": True,
            }
            settings = TranscriptionAsrSettings(
                model="large-v3",
                device="cuda",
                compute_type="float16",
                language="ja",
                vad_onset=0.25,
                vad_offset=0.15,
                whisperx_version="test-version",
            )

            plan = build_craig_hint_plan_from_context(
                context,
                base_dir=tmp_path,
                asr_settings=settings,
            )

            self.assertEqual(plan.cache_settings["asr"], settings.to_cache_settings())
            self.assertEqual(plan.hint.cache_settings, plan.cache_settings)
            self.assertEqual(plan.hint.cache_fingerprint, plan.cache_fingerprint)


if __name__ == "__main__":
    unittest.main()
