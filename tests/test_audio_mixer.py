from __future__ import annotations

import unittest

from src.audio_mixer import (
    active_audio_mix_channels,
    build_audio_mix_filter,
    reconcile_audio_mix,
    reset_audio_mix,
)


class AudioMixerTests(unittest.TestCase):
    def _project(self) -> dict:
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
        self.assertEqual(
            [channel["enabled"] for channel in audio_mix["channels"]],
            [False, True, False, False],
        )
        self.assertFalse(audio_mix["customized"])

    def test_reconcile_preserves_channel_controls_and_clamps_volume(self) -> None:
        project = self._project()
        reconcile_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])
        external = project["audio_mix"]["channels"][1]
        external.update({"enabled": True, "muted": True, "solo": True, "volume_percent": 999})
        project["audio_mix"]["customized"] = True

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

        self.assertEqual([channel["path"] for channel in active], ["voice.flac"])

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

        self.assertEqual(input_args, ["-i", "voice.flac"])
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
            "channels": [{
                "kind": "external",
                "path": "voice.flac",
                "enabled": True,
                "muted": False,
                "solo": False,
                "volume_percent": 100,
            }]
        }
        _, negative_graph = build_audio_mix_filter(external, offset_seconds=-0.375)
        _, silent_graph = build_audio_mix_filter({"channels": []})

        self.assertIn("atrim=start=0.375,asetpts=PTS-STARTPTS", negative_graph)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", silent_graph)

    def test_reconcile_prefers_external_when_no_video_tracks_are_available(self) -> None:
        project = self._project()
        audio_mix = reconcile_audio_mix(project, video_tracks=[])

        self.assertEqual(
            [channel["kind"] for channel in audio_mix["channels"]],
            ["external", "external"],
        )
        self.assertTrue(audio_mix["channels"][0]["enabled"])
        self.assertFalse(audio_mix["channels"][1]["enabled"])

    def test_reconcile_without_video_tracks_and_no_explicit_video_track_list_prefers_external(self) -> None:
        project = self._project()
        del project["render_settings"]

        audio_mix = reconcile_audio_mix(project)

        self.assertEqual(
            [channel["kind"] for channel in audio_mix["channels"]],
            ["external", "external"],
        )
        self.assertTrue(audio_mix["channels"][0]["enabled"])
        self.assertFalse(audio_mix["channels"][1]["enabled"])

    def test_reset_restores_legacy_default(self) -> None:
        project = self._project()
        reconcile_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])
        project["audio_mix"]["customized"] = True

        audio_mix = reset_audio_mix(project, [{"selector": "0:a:0", "label": "game"}])

        self.assertFalse(audio_mix["customized"])
        self.assertTrue(audio_mix["channels"][0]["enabled"])
        self.assertFalse(audio_mix["channels"][1]["enabled"])


if __name__ == "__main__":
    unittest.main()
