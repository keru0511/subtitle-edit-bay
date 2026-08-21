import unittest

from src.short_video_schema import (
    ShortVideo,
    ShortVideoBgm,
    ShortVideoClip,
    ShortVideoError,
    ShortVideoOutput,
    ShortVideoTransition,
)


class ShortVideoSchemaTests(unittest.TestCase):
    def test_default_short_video_from_empty_json(self) -> None:
        short_video = ShortVideo.from_json({})
        self.assertFalse(short_video.enabled)
        self.assertEqual(short_video.output.width, 1080)
        self.assertEqual(short_video.output.height, 1920)
        self.assertEqual(short_video.output.fps, 30)
        self.assertEqual(short_video.global_fit, "cover")
        self.assertEqual(short_video.global_background_color, "000000")
        self.assertAlmostEqual(short_video.subtitle_scale_percent, 150.0)
        self.assertEqual(short_video.transition.type, "crossfade")
        self.assertAlmostEqual(short_video.transition.duration, 0.5)
        self.assertEqual(short_video.bgm.path, "")
        self.assertEqual(short_video.clips, [])

    def test_short_video_round_trip(self) -> None:
        payload = {
            "enabled": True,
            "output": {"width": 720, "height": 1280, "fps": 60},
            "global_fit": "contain",
            "global_background_color": "ffffff",
            "subtitle_scale_percent": 200.0,
            "transition": {"type": "fade", "duration": 1.0},
            "bgm": {"path": "/tmp/bgm.mp3", "in": 5.0, "out": 65.0, "start": 0.5, "volume": 0.5},
            "clips": [
                {"segment_id": "seg-1", "start": 1.0, "end": 3.5, "fit": "blur", "background_color": "111111"}
            ],
        }
        short_video = ShortVideo.from_json(payload)
        restored = ShortVideo.from_json(short_video.to_json())
        self.assertEqual(restored.to_json(), short_video.to_json())

    def test_clip_inheritance_remains_distinguishable_after_round_trip(self) -> None:
        short_video = ShortVideo.from_json(
            {
                "global_fit": "contain",
                "global_background_color": "FF0000",
                "clips": [{"segment_id": "inherited", "start": 0.0, "end": 1.0}],
            }
        )

        self.assertIsNone(short_video.clips[0].fit)
        self.assertIsNone(short_video.clips[0].background_color)
        serialized = short_video.to_json()
        self.assertNotIn("fit", serialized["clips"][0])
        self.assertNotIn("background_color", serialized["clips"][0])

        restored = ShortVideo.from_json(serialized)
        self.assertIsNone(restored.clips[0].fit)
        self.assertIsNone(restored.clips[0].background_color)

    def test_legacy_clip_values_preserve_existing_rendering_by_default(self) -> None:
        legacy_payload = {
            "enabled": True,
            "global_fit": "contain",
            "global_background_color": "FF0000",
            "clips": [
                {
                    "segment_id": "legacy",
                    "start": 0.0,
                    "end": 1.0,
                    "fit": "cover",
                    "background_color": "000000",
                }
            ],
        }

        short_video = ShortVideo.from_json(legacy_payload)

        self.assertEqual(short_video.clips[0].fit, "cover")
        self.assertEqual(short_video.clips[0].background_color, "000000")
        self.assertEqual(short_video.to_json()["schema_version"], 2)

    def test_legacy_inheritance_migration_requires_explicit_opt_in(self) -> None:
        legacy_payload = {
            "global_fit": "contain",
            "global_background_color": "FF0000",
            "clips": [
                {
                    "segment_id": "legacy",
                    "start": 0.0,
                    "end": 1.0,
                    "fit": "cover",
                    "background_color": "000000",
                }
            ],
        }

        short_video = ShortVideo.from_json(
            legacy_payload,
            migrate_legacy_defaults=True,
        )

        self.assertIsNone(short_video.clips[0].fit)
        self.assertIsNone(short_video.clips[0].background_color)

    def test_schema_version_two_preserves_explicit_default_overrides(self) -> None:
        short_video = ShortVideo.from_json(
            {
                "schema_version": 2,
                "global_fit": "contain",
                "global_background_color": "FF0000",
                "clips": [
                    {
                        "segment_id": "explicit",
                        "start": 0.0,
                        "end": 1.0,
                        "fit": "cover",
                        "background_color": "000000",
                    }
                ],
            }
        )

        self.assertEqual(short_video.clips[0].fit, "cover")
        self.assertEqual(short_video.clips[0].background_color, "000000")

    def test_bgm_uses_in_out_keys(self) -> None:
        bgm = ShortVideoBgm.from_json({"in": 10.0, "out": 20.0, "volume": 0.8})
        self.assertAlmostEqual(bgm.in_point, 10.0)
        self.assertAlmostEqual(bgm.out_point, 20.0)
        self.assertAlmostEqual(bgm.volume, 0.8)
        self.assertEqual(bgm.to_json()["in"], 10.0)
        self.assertEqual(bgm.to_json()["out"], 20.0)

    def test_clip_end_below_start_normalizes(self) -> None:
        clip = ShortVideoClip.from_json({"segment_id": "seg-1", "start": 5.0, "end": 2.0})
        self.assertAlmostEqual(clip.end, 5.0)

    def test_invalid_fit_raises(self) -> None:
        with self.assertRaisesRegex(ShortVideoError, "fit"):
            ShortVideo.from_json({"global_fit": "stretch"})
        with self.assertRaisesRegex(ShortVideoError, "fit"):
            ShortVideoClip.from_json({"segment_id": "seg-1", "fit": "zoom"})

    def test_invalid_transition_type_raises(self) -> None:
        with self.assertRaisesRegex(ShortVideoError, "transition.type"):
            ShortVideo.from_json({"transition": {"type": "wipe"}})

    def test_output_dimensions_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ShortVideoError, "positive"):
            ShortVideo.from_json({"output": {"width": 0, "height": 1920, "fps": 30}})
        with self.assertRaisesRegex(ShortVideoError, "positive"):
            ShortVideo.from_json({"output": {"width": 1080, "height": -1, "fps": 30}})

    def test_clip_list_must_be_objects(self) -> None:
        with self.assertRaisesRegex(ShortVideoError, "clips\\[1\\]"):
            ShortVideo.from_json({"clips": [{}, "not-an-object"]})

    def test_bgm_boundaries_clamp(self) -> None:
        bgm = ShortVideoBgm.from_json({"in": -5.0, "out": -10.0, "start": -1.0, "volume": 2.0})
        self.assertAlmostEqual(bgm.in_point, 0.0)
        self.assertAlmostEqual(bgm.out_point, 0.0)
        self.assertAlmostEqual(bgm.start, 0.0)
        self.assertAlmostEqual(bgm.volume, 1.0)


if __name__ == "__main__":
    unittest.main()
