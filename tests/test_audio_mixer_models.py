from __future__ import annotations

import unittest

from src.data_boundary import is_object_dict, is_object_sequence

from src.audio_mixer import (
    AUDIO_SOURCE_ID_FIELD,
    AudioMixError,
    active_audio_mix_channels,
    build_audio_mix_filter,
    is_opaque_audio_channel_id,
    path_free_audio_mix_channels,
    reconcile_audio_mix,
    reset_audio_mix,
    update_audio_mix_channel,
    validate_audio_channel_changes,
    video_track_entries,
)


def _dict(value: object) -> dict[object, object]:
    if not is_object_dict(value):
        raise AssertionError("辞書を保持する必要があります")
    return value


def _channels(value: object) -> list[dict[object, object]]:
    channels = _dict(value)["channels"]
    if not is_object_sequence(channels):
        raise AssertionError("チャンネル配列を保持する必要があります")
    return [_dict(channel) for channel in channels]


class AudioMixerModelTests(unittest.TestCase):
    def _project(self) -> dict[object, object]:
        return {
            "render_settings": {"output_audio_track": "0:a:1"},
            "audio_sources": [
                {"name": "alice", "track_key": "craig:alice", "path": "C:/audio/1-alice.flac"},
                {"name": "bob", "track_key": "craig:bob", "path": "C:/audio/2-bob.flac"},
            ],
        }

    def test_reconcile_creates_independent_video_and_external_channels(self) -> None:
        project = self._project()

        audio_mix = reconcile_audio_mix(
            project,
            [
                {"selector": "0:a:0", "label": "game"},
                {"selector": "0:a:1", "label": "voice"},
            ],
        )

        self.assertEqual(len(audio_mix["channels"]), 4)
        actual_values = [channel["enabled"] for channel in audio_mix["channels"]]
        expected_values = [False, True, False, False]
        self.assertEqual(actual_values, expected_values)
        self.assertFalse(audio_mix["customized"])
        self.assertTrue(all(is_opaque_audio_channel_id(channel["id"]) for channel in audio_mix["channels"]))

    def test_legacy_channel_identity_migrates_without_losing_controls(self) -> None:
        source_path = r"C:\Users\Alice\private\voice.wav"
        project: dict[object, object] = {
            "audio_sources": [{"path": source_path}],
            "audio_mix": {
                "customized": True,
                "channels": [
                    {
                        "id": f"external:{source_path}",
                        "kind": "external",
                        "label": source_path,
                        "path": source_path,
                        "enabled": True,
                        "muted": True,
                        "solo": False,
                        "volume_percent": 42,
                    }
                ],
            },
        }

        audio_mix = reconcile_audio_mix(project, video_tracks=[])
        channel = audio_mix["channels"][0]

        self.assertTrue(is_opaque_audio_channel_id(channel["id"]))
        sources = project["audio_sources"]
        if not is_object_sequence(sources):
            self.fail("音声ソース配列を保持する必要があります")
        self.assertEqual(_dict(sources[0])[AUDIO_SOURCE_ID_FIELD], channel["id"])
        self.assertTrue(channel["muted"])
        self.assertEqual(channel["volume_percent"], 42.0)
        self.assertNotIn("Users", str(channel["id"]))
        self.assertNotIn(source_path, str(path_free_audio_mix_channels(audio_mix["channels"])))

    def test_canonical_channel_update_rejects_invalid_changes(self) -> None:
        project = self._project()
        audio_mix = reconcile_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])
        channel_id = audio_mix["channels"][0]["id"]
        if not isinstance(channel_id, str):
            self.fail("チャンネルIDは文字列である必要があります")

        updated = update_audio_mix_channel(audio_mix, channel_id, {"enabled": True, "volume_percent": 125})

        self.assertTrue(_channels(updated)[0]["enabled"])
        self.assertEqual(_channels(updated)[0]["volume_percent"], 125.0)
        self.assertEqual(audio_mix["channels"][0]["volume_percent"], 100.0)
        for changes in ({"volume_percent": 201}, {"muted": "yes"}, {"method": "delete"}):
            with self.subTest(changes=changes):
                with self.assertRaises(AudioMixError):
                    update_audio_mix_channel(audio_mix, channel_id, changes)
        with self.assertRaises(AudioMixError):
            update_audio_mix_channel(audio_mix, "external:C:/private/voice.wav", {"muted": True})

    def test_duplicate_track_keys_receive_stable_distinct_ids(self) -> None:
        project: dict[object, object] = {
            "audio_sources": [
                {"track_key": "craig:alice", "path": "one.flac"},
                {"track_key": "craig:alice", "path": "two.flac"},
            ],
            "render_settings": {},
        }

        first = reconcile_audio_mix(project, video_tracks=[])
        first_ids = [channel["id"] for channel in first["channels"]]
        second = reconcile_audio_mix(project, video_tracks=[])

        self.assertEqual(len(set(first_ids)), 2)
        actual_values = [channel["id"] for channel in second["channels"]]
        self.assertEqual(actual_values, first_ids)

    def test_reconcile_preserves_channel_controls_and_clamps_volume(self) -> None:
        project = self._project()
        reconcile_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])
        external = _channels(project["audio_mix"])[1]
        external.update({"enabled": True, "muted": True, "solo": True, "volume_percent": 999})
        _dict(project["audio_mix"])["customized"] = True

        audio_mix = reconcile_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])

        self.assertEqual(audio_mix["channels"][1]["volume_percent"], 200.0)
        self.assertTrue(audio_mix["channels"][1]["muted"])
        self.assertTrue(audio_mix["customized"])

    def test_solo_excludes_other_enabled_channels(self) -> None:
        audio_mix = {
            "channels": [
                {"kind": "video", "selector": "0:a:0", "enabled": True, "muted": False, "solo": False},
                {"kind": "external", "path": "voice.flac", "enabled": True, "muted": False, "solo": True},
            ]
        }

        active = active_audio_mix_channels(audio_mix)

        actual_values = [channel["path"] for channel in active]
        expected_values = ["voice.flac"]
        self.assertEqual(actual_values, expected_values)

    def test_filter_adds_external_input_sync_volume_and_loudness(self) -> None:
        audio_mix = {
            "channels": [
                {
                    "kind": "video",
                    "selector": "0:a:1",
                    "enabled": True,
                    "muted": False,
                    "solo": False,
                    "volume_percent": 80,
                },
                {
                    "kind": "external",
                    "path": "voice.flac",
                    "enabled": True,
                    "muted": False,
                    "solo": False,
                    "volume_percent": 125,
                },
            ]
        }

        input_args, filter_graph = build_audio_mix_filter(
            audio_mix,
            offset_seconds=0.25,
            post_filter="loudnorm=I=-16:LRA=11:TP=-1.5",
        )

        expected_values = ["-i", "voice.flac"]
        self.assertEqual(input_args, expected_values)
        self.assertIn("[0:a:1]", filter_graph)
        self.assertIn("[1:a:0]", filter_graph)
        self.assertIn("volume=0.8000", filter_graph)
        self.assertIn("volume=1.2500", filter_graph)
        self.assertIn("adelay=250:all=1", filter_graph)
        self.assertIn("amix=inputs=2", filter_graph)
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5,volume=1.0000", filter_graph)
        self.assertIn("alimiter=limit=0.841395", filter_graph)
        self.assertIn("level=disabled:latency=enabled,apad[mixed_audio]", filter_graph)

    def test_filter_supports_negative_offset_and_silent_output(self) -> None:
        external = {
            "channels": [
                {
                    "kind": "external",
                    "path": "voice.flac",
                    "enabled": True,
                    "muted": False,
                    "solo": False,
                    "volume_percent": 100,
                }
            ]
        }
        _, negative_graph = build_audio_mix_filter(external, offset_seconds=-0.375)
        _, silent_graph = build_audio_mix_filter({"channels": []})

        self.assertIn("atrim=start=0.375,asetpts=PTS-STARTPTS", negative_graph)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", silent_graph)

    def test_reconcile_prefers_external_when_no_video_tracks_are_available(self) -> None:
        project = self._project()
        audio_mix = reconcile_audio_mix(project, video_tracks=[])

        actual_values = [channel["kind"] for channel in audio_mix["channels"]]
        expected_values = ["external", "external"]
        self.assertEqual(actual_values, expected_values)
        self.assertTrue(audio_mix["channels"][0]["enabled"])
        self.assertFalse(audio_mix["channels"][1]["enabled"])

    def test_reconcile_does_not_invent_video_channel_for_video_only_project(self) -> None:
        project: dict[object, object] = {"audio_sources": [], "render_settings": {}}

        audio_mix = reconcile_audio_mix(project, video_tracks=[])

        self.assertFalse(audio_mix["channels"])

    def test_reconcile_without_video_tracks_and_no_explicit_video_track_list_prefers_external(self) -> None:
        project = self._project()
        del project["render_settings"]

        audio_mix = reconcile_audio_mix(project)

        actual_values = [channel["kind"] for channel in audio_mix["channels"]]
        expected_values = ["external", "external"]
        self.assertEqual(actual_values, expected_values)
        self.assertTrue(audio_mix["channels"][0]["enabled"])
        self.assertFalse(audio_mix["channels"][1]["enabled"])

    def test_reset_restores_legacy_default(self) -> None:
        project = self._project()
        reconcile_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])
        _dict(project["audio_mix"])["customized"] = True

        audio_mix = reset_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])

        self.assertFalse(audio_mix["customized"])
        self.assertTrue(audio_mix["channels"][0]["enabled"])
        self.assertFalse(audio_mix["channels"][1]["enabled"])

    def test_reconciliation_keeps_project_and_source_references(self) -> None:
        source: dict[str, object] = {"path": "voice.flac", "track_key": "stable"}
        project: dict[str, object] = {"audio_sources": [source], "render_settings": {}}
        mixed = reconcile_audio_mix(project, [])
        self.assertIs(project["audio_mix"], mixed)
        self.assertEqual(source[AUDIO_SOURCE_ID_FIELD], mixed["channels"][0]["id"])
        self.assertEqual(reconcile_audio_mix(project, [])["channels"][0]["id"], source[AUDIO_SOURCE_ID_FIELD])

    def test_update_preserves_extensions_and_does_not_mutate_original(self) -> None:
        mixed = reconcile_audio_mix({}, [{"selector": "0:a:0", "label": "game"}])
        channel = mixed["channels"][0]
        nested = ["keep"]
        channel["future"] = nested
        identifier = str(channel["id"])
        updated = update_audio_mix_channel(mixed, identifier, {"volume_percent": 75.5, "solo": True})
        updated_channel = _channels(updated)[0]
        self.assertEqual(updated_channel["volume_percent"], 75.5)
        self.assertTrue(updated_channel["solo"])
        self.assertFalse(channel["solo"])
        self.assertEqual(channel["volume_percent"], 100.0)
        self.assertFalse(mixed["customized"])
        self.assertTrue(updated["customized"])
        self.assertIsNot(updated_channel["future"], nested)
        nested.append("changed")
        expected = ["keep"]
        self.assertEqual(updated_channel["future"], expected)

    def test_invalid_change_values_are_rejected_without_partial_update(self) -> None:
        mixed = reconcile_audio_mix({}, [{"selector": "0:a:0"}])
        identifier = str(mixed["channels"][0]["id"])
        invalid: tuple[object, ...] = (
            None,
            [],
            {},
            {1: True},
            {"volume_percent": True},
            {"volume_percent": "50"},
            {"volume_percent": float("nan")},
            {"volume_percent": float("inf")},
            {"volume_percent": -0.1},
            {"solo": 1},
            {"enabled": True, "muted": "false"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(AudioMixError):
                    update_audio_mix_channel(mixed, identifier, changes)
        self.assertFalse(mixed["customized"])
        self.assertFalse(mixed["channels"][0]["muted"])
        for value in (0, 200, 75.5):
            self.assertEqual(validate_audio_channel_changes({"volume_percent": value})["volume_percent"], float(value))

    def test_invalid_containers_and_duplicate_ids_raise_domain_errors(self) -> None:
        invalid_roots: tuple[object, ...] = (None, [], "invalid")
        for value in invalid_roots:
            with self.subTest(value=value):
                with self.assertRaises(AudioMixError):
                    reconcile_audio_mix(value)
                with self.assertRaises(AudioMixError):
                    reset_audio_mix(value)
                with self.assertRaises(AudioMixError):
                    active_audio_mix_channels(value)
        with self.assertRaisesRegex(AudioMixError, "audio_sources"):
            reconcile_audio_mix({"audio_sources": None})
        with self.assertRaisesRegex(AudioMixError, "render_settings"):
            reconcile_audio_mix({"render_settings": []})
        with self.assertRaisesRegex(AudioMixError, "video track"):
            reconcile_audio_mix({}, [None])
        mixed = reconcile_audio_mix({}, [{"selector": "0:a:0"}])
        channel = mixed["channels"][0]
        with self.assertRaisesRegex(AudioMixError, "ambiguous"):
            update_audio_mix_channel({"channels": [channel, channel]}, str(channel["id"]), {"muted": True})
        with self.assertRaisesRegex(AudioMixError, "must be an array"):
            update_audio_mix_channel({"channels": None}, str(channel["id"]), {"muted": True})

    def test_volume_fallback_and_path_free_view_keep_existing_rules(self) -> None:
        mixed = reconcile_audio_mix({}, [{"selector": "0:a:0"}])
        channel = mixed["channels"][0]
        channel["label"] = "/private/voice.wav"
        cases: tuple[tuple[object, float], ...] = (
            (None, 100.0),
            ("invalid", 100.0),
            (float("nan"), 100.0),
            (float("inf"), 100.0),
            ("250", 200.0),
            ("-1", 0.0),
            ("75.5", 75.5),
        )
        for value, expected in cases:
            channel["volume_percent"] = value
            with self.subTest(value=value):
                view = path_free_audio_mix_channels([None, channel])
                self.assertEqual(view[0]["volume_percent"], expected)
                self.assertEqual(view[0]["label"], "動画音声 2")
                self.assertNotIn("path", view[0])
        with self.assertRaisesRegex(ValueError, "unique"):
            path_free_audio_mix_channels([channel, channel])

    def test_video_track_labels_handle_untrusted_tags(self) -> None:
        entries = video_track_entries(
            [
                {"tags": {"title": " 音声 "}, "codec_name": "aac", "channels": 2},
                {"tags": [], "codec_name": "opus", "channels": 1},
            ]
        )
        self.assertEqual(entries[0]["label"], "0:a:0  音声")
        self.assertEqual(entries[1]["label"], "0:a:1  opus / 1ch")
        with self.assertRaisesRegex(AudioMixError, "audio stream"):
            video_track_entries([None])


if __name__ == "__main__":
    unittest.main()
