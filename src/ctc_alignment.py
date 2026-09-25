"""CTCのblankを文字の持続時間へ混入させない時刻割り当て。"""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class AlignmentPoint:
    token_index: int
    time_index: int
    score: float


def ctc_path(emission, tokens: list[int], blank_id: int = 0) -> list[AlignmentPoint] | None:
    import numpy as np

    emission = np.asarray(emission)
    if emission.ndim != 2 or np.isnan(emission).any():
        raise ValueError("CTCの確率行列が不正です。")
    frames, labels = emission.shape
    if not 0 <= blank_id < labels or any(token == blank_id or not 0 <= token < labels for token in tokens):
        raise ValueError("CTCの文字IDが不正です。")
    repeats = sum(left == right for left, right in zip(tokens, tokens[1:]))
    if not tokens or frames < len(tokens) + repeats:
        return None
    # blank, 文字, blank, 文字...の状態列。連続する同じ文字はblankを経由する。
    states = np.full(2 * len(tokens) + 1, blank_id, dtype=np.int64)
    states[1::2] = tokens
    skip_allowed = np.zeros(len(states), dtype=bool)
    skip_allowed[2:] = (states[2:] != blank_id) & (states[2:] != states[:-2])
    scores = np.full(len(states), -np.inf, dtype=np.float64)
    scores[0] = 0.0
    parents = np.zeros((frames, len(states)), dtype=np.uint8)
    for time_index in range(frames):
        advance = np.concatenate(([-np.inf], scores[:-1]))
        skip = np.concatenate(([-np.inf, -np.inf], scores[:-2]))
        skip[~skip_allowed] = -np.inf
        choices = np.stack((scores, advance, skip))
        parents[time_index] = choices.argmax(axis=0)
        scores = choices.max(axis=0) + emission[time_index, states]
    state = len(states) - (1 if scores[-1] >= scores[-2] else 2)
    if not math.isfinite(float(scores[state])):
        return None
    path = []
    for time_index in range(frames - 1, -1, -1):
        if state % 2:
            path.append(AlignmentPoint(state // 2, time_index, math.exp(float(emission[time_index, states[state]]))))
        state -= int(parents[time_index, state])
    return list(reversed(path))


def blank_aware_backtrack(trellis, emission, tokens, blank_id=0):
    # WhisperXはCPUへdetachしたlog確率を渡す。モデル推論を追加しない。
    return ctc_path(emission.numpy(), tokens, blank_id)


_ALIGNMENT_LOCK = RLock()


@contextmanager
def blank_aware_alignment(module):
    """固定WhisperXの時刻合わせ中だけCTC経路を差し替え、例外時にも復元する。

    製品は専用CLIプロセスで実行する。このアダプターを使う呼び出しも直列化する。
    """
    with _ALIGNMENT_LOCK:
        original = module.backtrack
        module.backtrack = blank_aware_backtrack
        try:
            yield
        finally:
            module.backtrack = original
