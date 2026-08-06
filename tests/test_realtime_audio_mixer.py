from __future__ import annotations

import struct
import unittest

import numpy as np
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat

from src.realtime_audio_mixer import (
    LIMITER_CEILING,
    TimelineAudioMixer,
    audio_buffer_to_float32,
    encode_float32,
)


class RealtimeAudioMixerTests(unittest.TestCase):
    def test_converts_qt_integer_pcm_to_float_frames(self) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(48_000)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.Int16)
        buffer = QAudioBuffer(
            struct.pack("<hhhh", 0, 16_384, -32_768, 8_192),
            audio_format,
        )

        samples = audio_buffer_to_float32(buffer)

        np.testing.assert_allclose(
            samples,
            np.array([[0.0, 0.5], [-1.0, 0.25]], dtype=np.float32),
        )

    def test_mix_gain_does_not_depend_on_active_channel_count(self) -> None:
        mixer = TimelineAudioMixer(sample_rate=48_000, channel_count=2)
        block = np.full((8, 2), 0.5, dtype=np.float32)
        mixer.set_channels({"a": 0.5, "b": 0.5}, {"a": 0.0, "b": 0.0})
        mixer.reset(0)
        mixer.push("a", 0, block)
        mixer.push("b", 0, block)

        together = mixer.mix(8)

        np.testing.assert_allclose(together, 0.5)
        mixer.set_channels({"a": 0.5, "b": 0.0}, {"a": 0.0, "b": 0.0})
        mixer.reset(0)
        mixer.push("a", 0, block)
        alone = mixer.mix(8)
        np.testing.assert_allclose(alone, 0.25)

    def test_aligns_channel_audio_using_project_offset(self) -> None:
        mixer = TimelineAudioMixer(sample_rate=10, channel_count=2)
        mixer.set_channels({"voice": 1.0}, {"voice": 0.2})
        mixer.reset(0)
        mixer.push("voice", 0, np.full((4, 2), 0.25, dtype=np.float32))

        mixed = mixer.mix(6)

        np.testing.assert_allclose(mixed[:2], 0.0)
        np.testing.assert_allclose(mixed[2:], 0.25)

    def test_inactive_channel_buffers_are_pruned_during_long_playback(self) -> None:
        mixer = TimelineAudioMixer(sample_rate=48_000, channel_count=2)
        mixer.set_channels({"muted": 0.0}, {"muted": 0.0})
        mixer.reset(0)
        block = np.ones((480, 2), dtype=np.float32)

        for index in range(100):
            start_frame = index * len(block)
            mixer.push("muted", start_frame, block)
            mixer.mix(len(block))

        self.assertLessEqual(len(mixer._chunks.get("muted", ())), 1)

    def test_rejects_delayed_buffer_from_before_a_backward_seek(self) -> None:
        mixer = TimelineAudioMixer(sample_rate=48_000, channel_count=2)
        mixer.set_channels({"voice": 1.0}, {"voice": 0.0})
        mixer.reset(0)

        mixer.push("voice", 96_000, np.ones((480, 2), dtype=np.float32))

        self.assertEqual(len(mixer._chunks.get("voice", ())), 0)

    def test_linked_limiter_caps_summed_master_peak(self) -> None:
        mixer = TimelineAudioMixer(sample_rate=48_000, channel_count=2)
        mixer.set_channels({"a": 1.0, "b": 1.0}, {"a": 0.0, "b": 0.0})
        mixer.reset(0)
        block = np.ones((480, 2), dtype=np.float32)
        mixer.push("a", 0, block)
        mixer.push("b", 0, block)

        mixed = mixer.mix(480)

        self.assertLessEqual(float(np.max(np.abs(mixed))), LIMITER_CEILING + 1e-6)
        self.assertGreater(mixer.limiter_reduction_db, 0.0)

    def test_encodes_float_master_for_qt_sink(self) -> None:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(48_000)
        audio_format.setChannelCount(2)
        audio_format.setSampleFormat(QAudioFormat.Int16)

        encoded = encode_float32(
            np.array([[-1.0, 0.0], [0.5, 1.0]], dtype=np.float32),
            audio_format,
        )

        self.assertEqual(struct.unpack("<hhhh", encoded), (-32767, 0, 16384, 32767))


if __name__ == "__main__":
    unittest.main()
