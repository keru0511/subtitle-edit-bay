from __future__ import annotations

# 初回の認識で用いる設定。変更時はキャッシュにも同じ値を反映する。
DEFAULT_VAD_ONSET = 0.5
DEFAULT_VAD_OFFSET = 0.363


def first_pass_profile() -> dict[str, str | int | float]:
    return {
        "version": "first-pass-v2",
        "vad_method": "pyannote",
        "chunk_size": 15,
        "max_speech_gap": 1.0,
        "beam_size": 10,
        "repetition_penalty": 1.1,
        "no_repeat_ngram_size": 0,
        "batch_size": 8,
        "interpolate_method": "nearest",
    }
