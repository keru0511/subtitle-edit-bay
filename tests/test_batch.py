from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.batch import (
    derive_export_paths,
    derive_merged_export_paths,
    iter_video_files,
    process_video,
)


class BatchTests(unittest.TestCase):
    def test_iter_video_files_filters_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "b.mp4").write_text("b", encoding="utf-8")
            (root / "a.mkv").write_text("a", encoding="utf-8")
            (root / "op.mp4").write_text("op", encoding="utf-8")
            (root / "readme.txt").write_text("txt", encoding="utf-8")
            files = iter_video_files(temp_dir)
            self.assertEqual([p.name for p in files], ["a.mkv", "b.mp4"])

    def test_derive_export_paths(self) -> None:
        work_dir, final = derive_export_paths("/tmp/video.mp4", "/export", "0:a:1")
        self.assertEqual(work_dir.name, "video")
        self.assertEqual(final.name, "video.0_a_1.subtitled.mp4")

    def test_derive_merged_export_paths(self) -> None:
        work_dir, final = derive_merged_export_paths("/tmp/video.mp4", "/export")
        self.assertEqual(work_dir.name, "video")
        self.assertEqual(final.name, "video.merged.subtitled.mp4")

    def test_process_video_orchestrates_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "game.mp4"
            video.write_text("x", encoding="utf-8")
            merged_ass = Path(temp_dir) / "game.merged.ass"
            merged_ass.write_text("[Script Info]\n", encoding="utf-8")
            main_subtitled = Path(temp_dir) / "game.main.subtitled.mp4"
            final = Path(temp_dir) / "game.merged.subtitled.mp4"

            with mock.patch("src.batch.probe_media_duration", return_value=0.0), \
                 mock.patch("src.batch.run_media_to_merged_ass", return_value=(Path(temp_dir) / "game.json", merged_ass, Path(temp_dir) / "game.filtered.json")), \
                 mock.patch("src.batch.run_ffmpeg_burn", return_value=main_subtitled), \
                 mock.patch("src.batch.assemble_video", return_value=final):
                result = process_video(
                    str(video),
                    str(temp_dir),
                    ["0:a:1"],
                    model="large-v3",
                    device="cpu",
                    compute_type="int8",
                    width=1920,
                    height=1080,
                    diarize_tracks=set(),
                    min_speakers=None,
                    max_speakers=None,
                    language="ja",
                    vad_onset=0.35,
                    vad_offset=0.2,
                    op_file=None,
                    ed_file=None,
                    audio_normalize=True,
                    video_codec="libx264",
                    audio_codec="aac",
                    output_audio_track="0:a:0",
                    nvenc_preset="p5",
                    nvenc_cq=18,
                    x264_crf=18,
                    track_color_map=None,
                    subtitle_font_size=50,
                    subtitle_max_gap_seconds=0.32,
                    subtitle_end_padding_seconds=0.08,
                    subtitle_min_duration_seconds=0.35,
                )
            self.assertEqual(result, final)


if __name__ == "__main__":
    unittest.main()
