import unittest

from src.transcription_context import TranscriptionContext
from src.transcription_dictionary import DictionaryTerm, TranscriptionDictionary
from src.transcription_hint_plan import (
    TranscriptionAsrSettings,
    build_craig_transcription_hint_plan,
    confirmed_dictionary_hash,
)


class TranscriptionHintPlanTests(unittest.TestCase):
    def test_builds_confirmed_dictionary_hints_and_cache_settings(self) -> None:
        context = TranscriptionContext(
            game_title="Splatoon 3",
            game_notes="フェス",
            creator_terms=("Oz", "イカロール"),
            dictionary_confirmed=True,
        )
        dictionary = TranscriptionDictionary(
            game_title="Splatoon 3",
            terms=(
                DictionaryTerm("スプラシューター", aliases=("スシ",), enabled=True),
                DictionaryTerm("未使用ブキ", enabled=False),
            ),
        )
        settings = TranscriptionAsrSettings(
            model="large-v3",
            device="cuda",
            compute_type="float16",
            language="ja",
            vad_onset=0.35,
            vad_offset=0.2,
            whisperx_version="1.0.0",
        )

        plan = build_craig_transcription_hint_plan(context, dictionary, asr_settings=settings)

        self.assertIn("Splatoon 3", plan.hint.initial_prompt)
        self.assertIn("Oz", plan.hint.hotwords)
        self.assertIn("スプラシューター", plan.hint.hotwords)
        self.assertIn("スシ", plan.hint.hotwords)
        self.assertNotIn("未使用ブキ", plan.hint.hotwords)
        self.assertTrue(plan.cache_fingerprint)
        self.assertEqual(plan.hint.cache_fingerprint, plan.cache_fingerprint)
        self.assertEqual(plan.hint.cache_settings, plan.cache_settings)
        self.assertEqual(plan.cache_settings["asr"]["device"], "cuda")
        self.assertEqual(plan.cache_settings["dictionary_hash"], plan.dictionary_hash)

    def test_web_dictionary_terms_are_included_in_plan_prompt_and_cache(self) -> None:
        context = TranscriptionContext(
            web_dictionary_enabled=True,
            web_dictionary_terms=("Ink", "Bomba"),
        )

        plan = build_craig_transcription_hint_plan(context, asr_settings=TranscriptionAsrSettings())

        self.assertIn("ゲーム内用語: Ink, Bomba", plan.hint.initial_prompt)
        self.assertIn("Ink", plan.hint.hotwords)
        self.assertIn("Bomba", plan.hint.hotwords)
        self.assertEqual(plan.dictionary_hash, "")
        self.assertEqual(plan.cache_settings["transcription_context"]["web_dictionary_terms"], ["Ink", "Bomba"])

    def test_web_dictionary_terms_are_ignored_when_disabled(self) -> None:
        context = TranscriptionContext(
            web_dictionary_enabled=False,
            web_dictionary_terms=("Ink",),
        )

        plan = build_craig_transcription_hint_plan(context)

        self.assertNotIn("Ink", plan.hint.hotwords)
        self.assertNotIn("ゲーム内用語", plan.hint.initial_prompt)

    def test_unconfirmed_dictionary_is_inert(self) -> None:
        context = TranscriptionContext(
            game_title="Splatoon 3",
            creator_terms=("Oz",),
            dictionary_confirmed=False,
        )
        dictionary = TranscriptionDictionary(
            game_title="Splatoon 3",
            terms=(DictionaryTerm("スプラシューター", enabled=True),),
        )

        plan = build_craig_transcription_hint_plan(context, dictionary)

        self.assertEqual(confirmed_dictionary_hash(context, dictionary), "")
        self.assertEqual(plan.dictionary_hash, "")
        self.assertIn("Oz", plan.hint.hotwords)
        self.assertNotIn("スプラシューター", plan.hint.hotwords)

    def test_fingerprint_changes_when_asr_settings_change(self) -> None:
        context = TranscriptionContext(game_title="Game", dictionary_confirmed=False)

        cpu_plan = build_craig_transcription_hint_plan(
            context,
            asr_settings=TranscriptionAsrSettings(device="cpu", compute_type="int8"),
        )
        cuda_plan = build_craig_transcription_hint_plan(
            context,
            asr_settings=TranscriptionAsrSettings(device="cuda", compute_type="float16"),
        )

        self.assertNotEqual(cpu_plan.cache_fingerprint, cuda_plan.cache_fingerprint)

    def test_fingerprint_changes_when_confirmed_dictionary_changes(self) -> None:
        context = TranscriptionContext(game_title="Game", dictionary_confirmed=True)
        first_dictionary = TranscriptionDictionary(terms=(DictionaryTerm("alpha", enabled=True),))
        second_dictionary = TranscriptionDictionary(terms=(DictionaryTerm("beta", enabled=True),))

        first_plan = build_craig_transcription_hint_plan(context, first_dictionary)
        second_plan = build_craig_transcription_hint_plan(context, second_dictionary)

        self.assertNotEqual(first_plan.dictionary_hash, second_plan.dictionary_hash)
        self.assertNotEqual(first_plan.cache_fingerprint, second_plan.cache_fingerprint)


if __name__ == "__main__":
    unittest.main()
