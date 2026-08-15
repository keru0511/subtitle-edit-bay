from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from src.color_config import normalize_rgb_color, save_speaker_color
from src.runtime_config import (
    load_command_runtime_config,
    load_runtime_config,
    resolve_bool_option,
    resolve_list_option,
    resolve_option,
)
from src.subtitle_project import (
    SubtitleProject,
    SubtitleProjectError,
    create_project,
    derive_ass_path,
    derive_project_path,
    derive_render_path,
    load_project,
    project_from_transcript,
    validate_project,
)
from src.transcription_context import (
    TranscriptionContextError,
    normalize_transcription_context,
    transcription_context_from_mapping,
)
from src.transcription_context_config import (
    TranscriptionContextConfigError,
    load_transcription_context_file,
    normalized_transcription_context_from_runtime_config,
    resolve_transcription_context_file_path,
    transcription_context_from_runtime_config,
)
from src.transcription_dictionary import (
    TranscriptionDictionary,
    TranscriptionDictionaryError,
    enabled_dictionary_terms,
    load_transcription_dictionary,
    normalize_dictionary_term,
    transcription_dictionary_from_mapping,
)


class RuntimeConfigValidationTests(unittest.TestCase):
    def test_load_runtime_config_returns_empty_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(load_runtime_config(Path(temp_dir) / "missing.json"), {})

    def test_load_runtime_config_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_runtime_config(path)

    def test_load_command_runtime_config_skips_non_object_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text('{"shared": [1], "render": "not-a-dict"}', encoding="utf-8")
            self.assertEqual(load_command_runtime_config("any", path), {})

    def test_resolve_option_prioritizes_value_then_config_then_default(self) -> None:
        self.assertEqual(resolve_option("first", {"key": "second"}, "key", "third"), "first")
        self.assertEqual(resolve_option(None, {"key": "second"}, "key", "third"), "second")
        self.assertEqual(resolve_option(None, {}, "key", "third"), "third")

    def test_resolve_list_option_returns_default_and_empty(self) -> None:
        self.assertEqual(resolve_list_option(None, {}, "key", ["a"]), ["a"])
        self.assertEqual(resolve_list_option(None, {}, "key"), [])

    def test_resolve_list_option_casts_strings(self) -> None:
        self.assertEqual(resolve_list_option(["x"], {}, "key"), ["x"])

    def test_resolve_list_option_rejects_non_list(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_list_option("oops", {}, "key")

    def test_resolve_bool_option_rejects_non_bool(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_bool_option(None, {"flag": "yes"}, "flag", False)


class ColorConfigValidationTests(unittest.TestCase):
    def test_normalize_rgb_color_accepts_six_digit_hash(self) -> None:
        self.assertEqual(normalize_rgb_color("#AABBCC"), "#AABBCC")

    def test_normalize_rgb_color_accepts_eight_digit_alpha(self) -> None:
        self.assertEqual(normalize_rgb_color("#FFAABBCC"), "#AABBCC")

    def test_normalize_rgb_color_accepts_plain_hex(self) -> None:
        self.assertEqual(normalize_rgb_color("aabbcc"), "#AABBCC")

    def test_normalize_rgb_color_rejects_invalid(self) -> None:
        for invalid in ["blue", "GGG", "12", "12345G", "#GGGGGG"]:
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    normalize_rgb_color(invalid)

    def test_save_speaker_color_rejects_non_object_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "colors.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                save_speaker_color(path, file_name="clip", speaker_name="", color="#AABBCC")

    def test_save_speaker_color_rejects_broken_files_or_speakers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "colors.json"
            path.write_text('{"files": "wrong", "speakers": {}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                save_speaker_color(path, file_name="clip", speaker_name="", color="#AABBCC")

    def test_save_speaker_color_requires_file_or_speaker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "colors.json"
            with self.assertRaises(ValueError):
                save_speaker_color(path, file_name="", speaker_name="", color="#AABBCC")


class TranscriptionContextValidationTests(unittest.TestCase):
    def test_clean_text_handles_none_and_invalid(self) -> None:
        self.assertEqual(transcription_context_from_mapping({"game_title": None}).game_title, "")
        with self.assertRaises(TranscriptionContextError):
            transcription_context_from_mapping({"game_title": 123})

    def test_creator_terms_none_and_invalid(self) -> None:
        self.assertEqual(transcription_context_from_mapping({"creator_terms": None}).creator_terms, ())
        with self.assertRaises(TranscriptionContextError):
            transcription_context_from_mapping({"creator_terms": "oops"})

    def test_web_dictionary_terms_truncates_and_deduplicates(self) -> None:
        context = transcription_context_from_mapping({
            "web_dictionary_terms": ["a", "A", "b", "", None],
        })
        self.assertEqual(context.web_dictionary_terms, ("a", "b"))

    def test_payload_must_be_object(self) -> None:
        with self.assertRaises(TranscriptionContextError):
            transcription_context_from_mapping(["not", "an", "object"])

    def test_normalize_transcription_context_round_trip(self) -> None:
        data = {
            "game_title": "Splatoon",
            "creator_terms": ["スプラ"],
        }
        normalized = normalize_transcription_context(data)
        self.assertEqual(normalized["game_title"], "Splatoon")
        self.assertEqual(normalized["creator_terms"], ["スプラ"])


class TranscriptionContextConfigValidationTests(unittest.TestCase):
    def test_context_from_value_accepts_none_and_instance(self) -> None:
        from src.transcription_context import TranscriptionContext
        from src.transcription_context_config import _context_from_value

        self.assertEqual(_context_from_value(None).game_title, "")
        context = TranscriptionContext(game_title="test")
        self.assertIs(_context_from_value(context), context)

    def test_resolve_transcription_context_file_path_rejects_empty(self) -> None:
        with self.assertRaises(TranscriptionContextConfigError):
            resolve_transcription_context_file_path("")
        with self.assertRaises(TranscriptionContextConfigError):
            resolve_transcription_context_file_path(None)

    def test_resolve_transcription_context_file_path_relative_to_base(self) -> None:
        path = resolve_transcription_context_file_path("context.json", base_dir="/tmp")
        self.assertEqual(path, Path("/tmp/context.json"))

    def test_load_transcription_context_file_not_found(self) -> None:
        with self.assertRaises(TranscriptionContextConfigError):
            load_transcription_context_file("/tmp/does-not-exist.json")

    def test_load_transcription_context_file_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ctx.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(TranscriptionContextConfigError):
                load_transcription_context_file(str(path))

    def test_load_transcription_context_file_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ctx.json"
            path.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(TranscriptionContextConfigError):
                load_transcription_context_file(str(path))

    def test_transcription_context_from_runtime_config_cli_file_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ctx_path = Path(temp_dir) / "ctx.json"
            ctx_path.write_text('{"game_title": "cli"}', encoding="utf-8")
            result = transcription_context_from_runtime_config(
                {"transcription_context_file": ctx_path, "transcription_context": {"game_title": "cfg"}},
                cli_context_file=str(ctx_path),
            )
            self.assertEqual(result.game_title, "cli")

    def test_runtime_config_must_be_object(self) -> None:
        with self.assertRaises(TranscriptionContextConfigError):
            transcription_context_from_runtime_config([1, 2])


class TranscriptionDictionaryValidationTests(unittest.TestCase):
    def test_normalize_dictionary_term_valid(self) -> None:
        term = normalize_dictionary_term({"term": "ナワバリ"}, 0)
        self.assertEqual(term.term, "ナワバリ")
        self.assertTrue(term.enabled)

    def test_normalize_dictionary_term_rejects_non_object(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term("term", 0)

    def test_normalize_dictionary_term_rejects_missing_term(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"aliases": ["x"]}, 0)

    def test_clean_text_required(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"term": 123}, 0)

    def test_clean_text_list_rejects_non_list(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"term": "x", "aliases": "alias"}, 0)

    def test_clean_bool_rejects_non_bool(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"term": "x", "enabled": "yes"}, 0)

    def test_clean_score_rejects_invalid(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"term": "x", "score": "high"}, 0)
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"term": "x", "score": math.inf}, 0)

    def test_normalize_source_rejects_non_object(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            normalize_dictionary_term({"term": "x", "sources": ["url"]}, 0)

    def test_dictionary_root_must_be_object(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            transcription_dictionary_from_mapping("nope")

    def test_terms_must_be_array(self) -> None:
        with self.assertRaises(TranscriptionDictionaryError):
            transcription_dictionary_from_mapping({"terms": "single"})

    def test_load_transcription_dictionary_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dict.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(TranscriptionDictionaryError):
                load_transcription_dictionary(path)

    def test_load_transcription_dictionary_rejects_non_object_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dict.json"
            path.write_text("[1]", encoding="utf-8")
            with self.assertRaises(TranscriptionDictionaryError):
                load_transcription_dictionary(path)

    def test_enabled_dictionary_terms_and_aliases(self) -> None:
        dictionary = TranscriptionDictionary(
            game_title="test",
            terms=(
                normalize_dictionary_term({"term": "A", "aliases": ["a", "A"]}, 0),
                normalize_dictionary_term({"term": "B", "enabled": False}, 1),
            ),
        )
        self.assertEqual(enabled_dictionary_terms(dictionary), ["A", "a"])
        self.assertEqual(enabled_dictionary_terms(dictionary, include_aliases=False), ["A"])


class SubtitleProjectValidationTests(unittest.TestCase):
    def test_derive_project_path(self) -> None:
        path = derive_project_path("/tmp/game.mp4", "/out")
        self.assertEqual(path.name, "game.subtitle-project.json")

    def test_derive_ass_and_render_paths(self) -> None:
        project_path = Path("/tmp/game.subtitle-project.json")
        self.assertEqual(derive_ass_path(project_path).name, "game.edited.ass")
        self.assertEqual(derive_render_path(project_path).name, "game.edited.subtitled.mp4")

    def test_finite_number_rejects_invalid_and_infinite(self) -> None:
        from src.subtitle_project import _finite_number
        self.assertEqual(_finite_number(3.5, "field"), 3.5)
        with self.assertRaises(SubtitleProjectError):
            _finite_number("abc", "field")
        with self.assertRaises(SubtitleProjectError):
            _finite_number(math.inf, "field")

    def test_subtitle_line_count_rejects_invalid(self) -> None:
        from src.subtitle_project import _subtitle_line_count
        self.assertEqual(_subtitle_line_count("1"), "1")
        self.assertEqual(_subtitle_line_count("auto"), "auto")
        with self.assertRaises(SubtitleProjectError):
            _subtitle_line_count("5")
        with self.assertRaises(SubtitleProjectError):
            _subtitle_line_count("abc")

    def test_project_must_be_object(self) -> None:
        with self.assertRaises(SubtitleProjectError):
            validate_project("not a dict")

    def test_project_requires_video_path(self) -> None:
        with self.assertRaises(SubtitleProjectError):
            validate_project({"segments": []})

    def test_project_rejects_duplicate_segment_ids(self) -> None:
        with self.assertRaises(SubtitleProjectError):
            validate_project({
                "video": {"path": "/tmp/game.mp4"},
                "segments": [
                    {"id": "same", "start": 0, "end": 1, "text": "a"},
                    {"id": "same", "start": 2, "end": 3, "text": "b"},
                ],
            })

    def test_project_rejects_invalid_outline_settings(self) -> None:
        with self.assertRaises(SubtitleProjectError):
            validate_project({
                "video": {"path": "/tmp/game.mp4"},
                "segments": [],
                "subtitle_settings": {"outline_thickness": 100},
            })

    def test_project_rejects_unsupported_schema_version(self) -> None:
        with self.assertRaises(SubtitleProjectError):
            validate_project({
                "schema_version": 999,
                "video": {"path": "/tmp/game.mp4"},
                "segments": [],
            })

    def test_create_project_round_trip(self) -> None:
        project = create_project(
            video_path="/tmp/game.mp4",
            output_dir="/tmp/out",
            segments=[{"start": 0, "end": 1, "text": "hi", "speaker": "Oz"}],
        )
        self.assertEqual(project["project_type"], "subtitle-edit-project")
        self.assertEqual(len(project["segments"]), 1)

    def test_project_from_transcript_requires_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "transcript.json"
            path.write_text('{"segments": []}', encoding="utf-8")
            project = project_from_transcript(path, video_path="/tmp/game.mp4")
            self.assertEqual(project["segments"], [])

    def test_project_from_transcript_rejects_bad_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "transcript.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                project_from_transcript(path, video_path="/tmp/game.mp4")

    def test_project_from_transcript_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "transcript.json"
            path.write_text('"string"', encoding="utf-8")
            with self.assertRaises(SubtitleProjectError):
                project_from_transcript(path, video_path="/tmp/game.mp4")

    def test_subtitle_project_from_json_round_trip(self) -> None:
        project = create_project(
            video_path="/tmp/game.mp4",
            output_dir="/tmp/out",
            segments=[{"start": 0, "end": 1, "text": "hi"}],
        )
        model = SubtitleProject.from_json(project)
        self.assertEqual(model.project_type, "subtitle-edit-project")
        self.assertEqual(model.to_json()["project_type"], "subtitle-edit-project")

    def test_load_project_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "game.subtitle-project.json"
            project = create_project(
                video_path="/tmp/game.mp4",
                output_dir=str(temp_dir),
                segments=[{"start": 0, "end": 1, "text": "hi"}],
            )
            path.write_text(json.dumps(project), encoding="utf-8")
            loaded = load_project(path)
            self.assertEqual(loaded["project_type"], "subtitle-edit-project")


if __name__ == "__main__":
    unittest.main()
