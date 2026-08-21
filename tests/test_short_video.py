from __future__ import annotations

import unittest

from src.short_video import build_short_video_filter_complex
from src.short_video_schema import (
    ShortVideo,
    ShortVideoBgm,
    ShortVideoClip,
    ShortVideoError,
    ShortVideoOutput,
    ShortVideoTransition,
)


class BuildShortVideoFilterComplexTests(unittest.TestCase):
    def test_empty_clips_raises(self) -> None:
        short = ShortVideo(enabled=True, clips=[])
        with self.assertRaises(ShortVideoError):
            build_short_video_filter_complex(short)

    def test_single_clip_cover_with_audio(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.5, fit="cover")],
            transition=ShortVideoTransition(type="crossfade", duration=0.5),
        )
        fc = build_short_video_filter_complex(short, has_audio=True, ass_path=None)
        self.assertIn("[0:v:0]trim=start=0.000:end=2.500", fc)
        self.assertIn(
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            fc,
        )
        self.assertIn("[0:a:0]atrim", fc)
        self.assertIn("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo", fc)
        self.assertIn("[v_final]", fc)
        self.assertIn("[aout]", fc)
        self.assertIn("[sa0]anull[aout]", fc)

    def test_single_clip_no_audio(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=1.0, end=3.5, fit="cover")],
        )
        fc = build_short_video_filter_complex(short, has_audio=False, ass_path=None)
        self.assertNotIn("[0:a:0]", fc)
        self.assertNotIn("[aout]", fc)
        self.assertIn("[v_final]", fc)

    def test_contain_fit_uses_pad_with_background(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0, fit="contain", background_color="FF0000")],
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn(
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920",
            fc,
        )
        self.assertIn("pad=1080:1920:(ow-iw)/2:(oh-ih)/2:FF0000", fc)

    def test_blur_fit_uses_boxblur_and_overlay(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0, fit="blur")],
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn("split[orig][fill]", fc)
        self.assertIn("boxblur=40:40", fc)
        self.assertIn("[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto", fc)

    def test_two_clips_crossfade(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[
                ShortVideoClip(start=0.0, end=2.5, fit="cover"),
                ShortVideoClip(start=5.0, end=7.5, fit="cover"),
            ],
            transition=ShortVideoTransition(type="crossfade", duration=0.5),
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn(
            "[sv0][sv1]xfade=transition=fade:duration=0.500:offset=2.000",
            fc,
        )
        self.assertIn("[sa0][sa1]acrossfade=d=0.500:c1=tri:c2=tri", fc)

    def test_three_clips_offsets_are_cumulative(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[
                ShortVideoClip(start=0.0, end=2.0),
                ShortVideoClip(start=10.0, end=12.0),
                ShortVideoClip(start=20.0, end=22.0),
            ],
            transition=ShortVideoTransition(type="crossfade", duration=0.5),
        )
        fc = build_short_video_filter_complex(short)
        # First transition offset is first duration - transition
        self.assertIn("xfade=transition=fade:duration=0.500:offset=1.500", fc)
        # Second transition offset is (2 + 2 - 0.5) - 0.5 = 3.0
        self.assertIn("xfade=transition=fade:duration=0.500:offset=3.000", fc)

    def test_cut_transition_uses_concat(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[
                ShortVideoClip(start=0.0, end=2.0),
                ShortVideoClip(start=10.0, end=12.0),
            ],
            transition=ShortVideoTransition(type="cut", duration=0.0),
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn("[sv0][sv1]concat=n=2:v=1:a=0[v_concat]", fc)
        self.assertIn("[sa0][sa1]concat=n=2:v=0:a=1[a_concat]", fc)
        self.assertIn("[v_concat]format=yuv420p[v_final]", fc)
        self.assertIn("[a_concat]anull[aout]", fc)

    def test_zero_duration_transition_uses_concat(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[
                ShortVideoClip(start=0.0, end=2.0),
                ShortVideoClip(start=10.0, end=12.0),
            ],
            transition=ShortVideoTransition(type="crossfade", duration=0.0),
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn("concat=n=2:v=1:a=0", fc)

    def test_ass_burn_appended_at_end(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0)],
        )
        fc = build_short_video_filter_complex(short, ass_path="/tmp/short.ass")
        self.assertIn("ass='/tmp/short.ass',format=yuv420p[v_final]", fc)

    def test_clip_fit_overrides_global(self) -> None:
        short = ShortVideo(
            enabled=True,
            global_fit="cover",
            clips=[ShortVideoClip(start=0.0, end=2.0, fit="contain")],
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn("force_original_aspect_ratio=decrease", fc)

    def test_global_fit_is_used_when_clip_fit_is_missing(self) -> None:
        for fit in ("cover", "contain", "blur"):
            with self.subTest(fit=fit):
                short = ShortVideo(
                    enabled=True,
                    global_fit=fit,
                    clips=[ShortVideoClip(start=0.0, end=2.0)],
                )
                fc = build_short_video_filter_complex(short)
                if fit == "cover":
                    self.assertIn("force_original_aspect_ratio=increase", fc)
                elif fit == "contain":
                    self.assertIn("force_original_aspect_ratio=decrease", fc)
                else:
                    self.assertIn("boxblur=40:40", fc)

    def test_global_background_used_when_clip_missing(self) -> None:
        short = ShortVideo(
            enabled=True,
            global_background_color="00FF00",
            clips=[ShortVideoClip(start=0.0, end=2.0, fit="contain", background_color="")],
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn("pad=1080:1920:(ow-iw)/2:(oh-ih)/2:00FF00", fc)

    def test_clip_background_overrides_global_background(self) -> None:
        short = ShortVideo(
            enabled=True,
            global_fit="contain",
            global_background_color="00FF00",
            clips=[
                ShortVideoClip(
                    start=0.0,
                    end=2.0,
                    fit="contain",
                    background_color="0000FF",
                )
            ],
        )
        fc = build_short_video_filter_complex(short)
        self.assertIn("pad=1080:1920:(ow-iw)/2:(oh-ih)/2:0000FF", fc)
        self.assertNotIn("pad=1080:1920:(ow-iw)/2:(oh-ih)/2:00FF00", fc)

    def test_ass_path_colons_escaped(self) -> None:
        short = ShortVideo(
            enabled=True,
            clips=[ShortVideoClip(start=0.0, end=2.0)],
        )
        fc = build_short_video_filter_complex(short, ass_path="C:/temp/short.ass")
        self.assertIn("ass='C\\:/temp/short.ass'", fc)

    def test_bgm_with_main_audio_is_mixed(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0)],
            bgm=ShortVideoBgm(
                path="/tmp/bgm.mp3",
                in_point=0.0,
                out_point=0.0,
                start=0.0,
                volume=0.4,
            ),
        )
        fc = build_short_video_filter_complex(short, has_audio=True, include_bgm=True)
        self.assertIn("[1:a:0]", fc)
        self.assertIn("aloop=loop=-1:size=2147483647", fc)
        self.assertIn("atrim=0:2", fc)
        self.assertIn("adelay=0:all=1", fc)
        self.assertIn("volume=0.4", fc)
        self.assertIn("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bgm]", fc)
        self.assertIn(
            "[sa0][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0:weights='1 1'[aout]",
            fc,
        )

    def test_bgm_trimmed_and_delayed(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=5.0)],
            bgm=ShortVideoBgm(
                path="/tmp/bgm.mp3",
                in_point=1.0,
                out_point=4.0,
                start=2.0,
                volume=0.5,
            ),
        )
        fc = build_short_video_filter_complex(short, has_audio=True, include_bgm=True)
        self.assertIn("atrim=start=1.000:end=4.000,asetpts=PTS-STARTPTS,", fc)
        self.assertIn("atrim=0:3", fc)
        self.assertIn("adelay=2000:all=1", fc)
        self.assertIn("volume=0.5", fc)

    def test_bgm_without_main_audio_is_output(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0)],
            bgm=ShortVideoBgm(
                path="/tmp/bgm.mp3",
                in_point=0.0,
                out_point=0.0,
                start=0.0,
                volume=0.3,
            ),
        )
        fc = build_short_video_filter_complex(short, has_audio=False, include_bgm=True)
        self.assertIn("[1:a:0]", fc)
        self.assertNotIn("[sa0]", fc)
        self.assertIn("[bgm]anull[aout]", fc)

    def test_bgm_not_included_when_disabled(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0)],
            bgm=ShortVideoBgm(
                path="/tmp/bgm.mp3",
                in_point=0.0,
                out_point=0.0,
                start=0.0,
                volume=0.3,
            ),
        )
        fc = build_short_video_filter_complex(short, has_audio=True, include_bgm=False)
        self.assertNotIn("[1:a:0]", fc)
        self.assertIn("[sa0]anull[aout]", fc)

    def test_bgm_start_after_total_duration_is_ignored(self) -> None:
        short = ShortVideo(
            enabled=True,
            output=ShortVideoOutput(width=1080, height=1920, fps=30),
            clips=[ShortVideoClip(start=0.0, end=2.0)],
            bgm=ShortVideoBgm(
                path="/tmp/bgm.mp3",
                in_point=0.0,
                out_point=0.0,
                start=2.0,
                volume=0.3,
            ),
        )
        fc = build_short_video_filter_complex(short, has_audio=True, include_bgm=True)
        self.assertNotIn("[1:a:0]", fc)
        self.assertIn("[sa0]anull[aout]", fc)


if __name__ == "__main__":
    unittest.main()
