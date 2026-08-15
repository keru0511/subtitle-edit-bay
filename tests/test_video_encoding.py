from __future__ import annotations

import unittest

from src.video_encoding import build_video_encoding_args


class VideoEncodingTests(unittest.TestCase):
    def test_build_video_encoding_args_libx264(self) -> None:
        args = build_video_encoding_args("libx264", x264_crf=23)
        self.assertEqual(args, ["-preset", "medium", "-crf", "23", "-profile:v", "high"])

    def test_build_video_encoding_args_h264_nvenc(self) -> None:
        args = build_video_encoding_args("h264_nvenc", nvenc_preset="p4", nvenc_cq=20)
        self.assertIn("-preset", args)
        self.assertIn("p4", args)
        self.assertIn("-cq", args)
        self.assertIn("20", args)
        self.assertIn("-profile:v", args)
        self.assertIn("high", args)

    def test_build_video_encoding_args_hevc_nvenc(self) -> None:
        args = build_video_encoding_args("hevc_nvenc", nvenc_preset="p5", nvenc_cq=21)
        self.assertIn("-preset", args)
        self.assertIn("-cq", args)
        self.assertIn("21", args)
        self.assertNotIn("-profile:v", args)

    def test_build_video_encoding_args_unknown_codec_returns_empty(self) -> None:
        self.assertEqual(build_video_encoding_args("libx265"), [])

    def test_build_video_encoding_args_rejects_invalid_nvenc_cq(self) -> None:
        with self.assertRaises(ValueError):
            build_video_encoding_args("h264_nvenc", nvenc_cq=52)
        with self.assertRaises(ValueError):
            build_video_encoding_args("h264_nvenc", nvenc_cq=-1)

    def test_build_video_encoding_args_rejects_invalid_x264_crf(self) -> None:
        with self.assertRaises(ValueError):
            build_video_encoding_args("libx264", x264_crf=52)
        with self.assertRaises(ValueError):
            build_video_encoding_args("libx264", x264_crf=-1)


if __name__ == "__main__":
    unittest.main()
