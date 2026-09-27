from __future__ import annotations

import argparse
import gc
import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Protocol, Sequence, cast

from .data_boundary import is_object_list, is_string_object_mapping
from .transcription_profile import DEFAULT_VAD_OFFSET, DEFAULT_VAD_ONSET, first_pass_profile


class RunnerArguments(argparse.Namespace):
    audio: str
    model: str = "large-v3"
    device: str = "cpu"
    compute_type: str = "int8"
    output_dir: str
    output_format: str = "json"
    language: str | None = None
    vad_onset: float = DEFAULT_VAD_ONSET
    vad_offset: float = DEFAULT_VAD_OFFSET
    initial_prompt: str | None = None
    hotwords: str | None = None
    diarize: bool = False
    min_speakers: int | None = None
    max_speakers: int | None = None
    vad_method: str = "pyannote"
    chunk_size: int = 15
    beam_size: int = 10
    repetition_penalty: float = 1.1
    no_repeat_ngram_size: int = 0
    batch_size: int = 8
    interpolate_method: str = "nearest"


class _WhisperXModel(Protocol):
    def transcribe(self, audio: object, *, batch_size: int, chunk_size: int, print_progress: bool) -> object: ...


class _WhisperXModule(Protocol):
    def load_audio(self, path: str) -> object: ...

    def load_model(
        self,
        model: str,
        *,
        device: str,
        compute_type: str,
        language: str | None,
        asr_options: Mapping[str, object],
        vad_method: str,
        vad_options: Mapping[str, object],
    ) -> _WhisperXModel: ...

    def load_align_model(self, *, language_code: str, device: str) -> tuple[object, object]: ...

    def align(
        self,
        segments: list[object],
        align_model: object,
        metadata: object,
        audio: object,
        device: str,
        *,
        interpolate_method: str,
        print_progress: bool,
    ) -> object: ...


class _Diarizer(Protocol):
    def __call__(self, audio: object, *, min_speakers: int | None, max_speakers: int | None) -> object: ...


class _DiarizationModule(Protocol):
    def DiarizationPipeline(self, *, token: str, device: str) -> _Diarizer: ...

    def assign_word_speakers(self, speakers: object, result: Mapping[str, object]) -> object: ...


class _CudaMemory(Protocol):
    def empty_cache(self) -> None: ...


class _TorchModule(Protocol):
    cuda: _CudaMemory


def _result(value: object) -> dict[str, object]:
    if not is_string_object_mapping(value):
        raise ValueError("WhisperX result must be an object")
    return dict(value)


def _segments(value: object) -> list[object]:
    if not is_object_list(value):
        raise ValueError("WhisperX segments must be an array")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="初回認識の生成設定を適用してWhisperXを実行します。")
    parser.add_argument("audio")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute_type", default="int8")
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--output_format", choices=("json",), default="json")
    parser.add_argument("--language", default=None)
    parser.add_argument("--vad_onset", type=float, default=DEFAULT_VAD_ONSET)
    parser.add_argument("--vad_offset", type=float, default=DEFAULT_VAD_OFFSET)
    parser.add_argument("--initial_prompt", default=None)
    parser.add_argument("--hotwords", default=None)
    parser.add_argument("--diarize", action="store_true")
    parser.add_argument("--min_speakers", type=int)
    parser.add_argument("--max_speakers", type=int)
    for key, value in first_pass_profile().items():
        if key != "version":
            parser.add_argument(f"--{key}", type=type(value), default=value)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> RunnerArguments:
    args = RunnerArguments()
    build_parser().parse_args(argv, namespace=args)
    return args


def _release_memory(device: str) -> None:
    gc.collect()
    if device.startswith("cuda"):
        torch_module: object = importlib.import_module("torch")
        torch = cast(_TorchModule, torch_module)
        torch.cuda.empty_cache()


def run(args: RunnerArguments) -> Path:
    # 軽量なCLI・単体テストでは、モデルとGPUライブラリをロードしない。
    whisperx_module: object = importlib.import_module("whisperx")
    whisperx = cast(_WhisperXModule, whisperx_module)

    if not 0 <= args.vad_offset <= args.vad_onset <= 1:
        raise ValueError("VADのしきい値は 0 <= offset <= onset <= 1 にしてください。")
    if not 1 <= args.chunk_size <= 30 or args.beam_size < 1 or args.batch_size < 1:
        raise ValueError("音声区間は1〜30秒、探索数とバッチ数は1以上にしてください。")
    if not 1 <= args.repetition_penalty <= 2 or args.no_repeat_ngram_size < 0:
        raise ValueError("反復ペナルティは1〜2、反復禁止の長さは0以上にしてください。")
    if args.diarize and not os.environ.get("HF_TOKEN", "").strip():
        raise ValueError("話者分離にはHF_TOKENが必要です。")

    asr_options: dict[str, object] = {
        "beam_size": args.beam_size,
        "repetition_penalty": args.repetition_penalty,
        # 本当に繰り返した発言を禁止しない。確率への緩いペナルティのみ適用する。
        "no_repeat_ngram_size": args.no_repeat_ngram_size,
        "condition_on_previous_text": False,
        "initial_prompt": args.initial_prompt,
        "hotwords": args.hotwords,
    }
    print(
        f"初回認識: beam={args.beam_size}, repetition_penalty={args.repetition_penalty}, "
        f"chunk={args.chunk_size}s, VAD={args.vad_onset}/{args.vad_offset}",
        flush=True,
    )
    audio = whisperx.load_audio(args.audio)
    model = whisperx.load_model(
        args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        asr_options=asr_options,
        vad_method=args.vad_method,
        vad_options={"chunk_size": args.chunk_size, "vad_onset": args.vad_onset, "vad_offset": args.vad_offset},
    )
    try:
        result = _result(
            model.transcribe(audio, batch_size=args.batch_size, chunk_size=args.chunk_size, print_progress=True)
        )
    finally:
        del model
        _release_memory(args.device)

    language = result.get("language")
    if not isinstance(language, str):
        raise ValueError("WhisperX language must be a string")
    segments = _segments(result.get("segments"))
    if segments:
        align_model, metadata = whisperx.load_align_model(language_code=language, device=args.device)
        try:
            # 切り詰めた音声ではなく元の音声と絶対時刻を渡し、語単位で時刻を合わせる。
            result = _result(
                whisperx.align(
                    segments,
                    align_model,
                    metadata,
                    audio,
                    args.device,
                    interpolate_method=args.interpolate_method,
                    print_progress=True,
                )
            )
        finally:
            del align_model
            _release_memory(args.device)
    result["language"] = language
    if args.diarize and _segments(result.get("segments")):
        diarization_module: object = importlib.import_module("whisperx.diarize")
        diarization = cast(_DiarizationModule, diarization_module)
        diarizer = diarization.DiarizationPipeline(token=os.environ["HF_TOKEN"], device=args.device)
        try:
            speakers = diarizer(audio, min_speakers=args.min_speakers, max_speakers=args.max_speakers)
            result = _result(diarization.assign_word_speakers(speakers, result))
        finally:
            del diarizer
            _release_memory(args.device)

    output = Path(args.output_dir) / f"{Path(args.audio).stem}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary_path = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return output


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
