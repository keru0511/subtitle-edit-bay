from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.data_boundary import decode_json, is_object_mapping
from src.gui_settings_controller import SettingsController
from tests.typed_case import TypedTestCase


class SettingsControllerTests(TypedTestCase):
    def _base_config(self, root: Path) -> Path:
        path = root / "base-runtime.json"
        payload: dict[str, object] = {
            "shared": {"model": "base-model", "device": "cpu"},
            "craig_pipeline": {"video_codec": "libx264"},
        }
        path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        return path

    def test_missing_values_keep_existing_gui_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller = SettingsController(root, base_config_path=self._base_config(root))

            self.assertEqual(controller.settings["model"], "base-model")
            self.assertEqual(controller.settings["device"], "cpu")
            self.assertEqual(controller.settings["compute_type"], "float16")
            self.assertEqual(controller.settings["language"], "ja")
            self.assertEqual(controller.settings["video_codec"], "libx264")
            self.assertEqual(controller.settings["postprocess_workers"], 4)
            self.assertEqual(controller.transcription_context["game_title"], "")

    def test_save_and_reload_round_trip_uses_controller_as_settings_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_path = self._base_config(root)
            controller = SettingsController(root, base_config_path=base_path)
            speakers = [{"track_key": "craig:Alice", "color": "#ABCDEF"}]

            state, context_changed = controller.save_settings(
                {
                    "model": "small",
                    "device": "cuda",
                    "video_codec": "h264_nvenc",
                    "unknown_setting": "must not persist",
                    "video": "one-shot-input.mkv",
                    "transcription_context": {
                        "game_title": "  Game  ",
                        "creator_terms_text": "alpha\nalpha\nbeta",
                        "dictionary_path": "dictionaries/game.json",
                        "dictionary_confirmed": True,
                    },
                },
                speakers,
            )

            self.assertTrue(context_changed)
            self.assertEqual(state.settings["model"], "small")
            self.assertEqual(state.transcription_context["game_title"], "Game")
            payload = decode_json(controller.gui_config_path.read_text(encoding="utf-8"))
            if not is_object_mapping(payload):
                self.fail("設定ファイルのルートはオブジェクトである必要がある")
            shared = payload.get("shared")
            craig = payload.get("craig_pipeline")
            if not is_object_mapping(shared) or not is_object_mapping(craig):
                self.fail("設定ファイルのセクションはオブジェクトである必要がある")
            self.assertEqual(shared["model"], "small")
            self.assertEqual(craig["track_color"], ["craig:Alice=#ABCDEF"])
            self.assertNotIn("unknown_setting", shared)
            self.assertNotIn("video", craig)
            context = craig.get("transcription_context")
            if not is_object_mapping(context):
                self.fail("文字起こし設定はオブジェクトである必要がある")
            self.assertEqual(
                context["creator_terms"],
                ["alpha", "beta"],
            )

            reloaded = SettingsController(root, base_config_path=base_path)
            self.assertEqual(reloaded.settings["model"], "small")
            self.assertEqual(reloaded.settings["device"], "cuda")
            self.assertEqual(reloaded.transcription_context["game_title"], "Game")
            self.assertEqual(reloaded.transcription_context["creator_terms_text"], "alpha\nbeta")

    def test_invalid_context_is_rejected_before_state_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            controller = SettingsController(root, base_config_path=self._base_config(root))
            before_settings = dict(controller.settings)
            before_context = dict(controller.transcription_context)

            with self.assertRaises((TypeError, ValueError)):
                controller.save_settings(
                    {
                        "model": "small",
                        "transcription_context": {"dictionary_confirmed": "yes"},
                    },
                    [],
                )

            self.assertEqual(controller.settings, before_settings)
            self.assertEqual(controller.transcription_context, before_context)
            self.assertFalse(controller.gui_config_path.exists())


if __name__ == "__main__":
    unittest.main()
