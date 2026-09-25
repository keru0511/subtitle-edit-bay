from __future__ import annotations

import argparse
import gc
import json
import os
import tempfile
from pathlib import Path

from .ctc_alignment import blank_aware_alignment
from .transcription_profile import DEFAULT_VAD_OFFSET, DEFAULT_VAD_ONSET, first_pass_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="初回認識の生成設定を適用してWhisperXを実行します。")
    parser.add_argument("audio")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute_type", default="int8")
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--output_format", choices=["json"], default="json")
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


def _release_memory(device: str) -> None:
    gc.collect()
    if device.startswith("cuda"):
        import torch

        torch.cuda.empty_cache()


def run(args: argparse.Namespace) -> Path:
    # 軽量なCLI・単体テストでは、モデルとGPUライブラリをロードしない。
    import whisperx

    if not 0 <= args.vad_offset <= args.vad_onset <= 1:
        raise ValueError("VADのしきい値は 0 <= offset <= onset <= 1 にしてください。")
    if not 1 <= args.chunk_size <= 30 or args.beam_size < 1 or args.batch_size < 1:
        raise ValueError("音声区間は1〜30秒、探索数とバッチ数は1以上にしてください。")
    if not 1 <= args.repetition_penalty <= 2 or args.no_repeat_ngram_size < 0:
        raise ValueError("反復ペナルティは1〜2、反復禁止の長さは0以上にしてください。")
    if args.alignment_backend != "ctc-blank-v1":
        raise ValueError("未対応の時刻合わせ方式です。")
    if args.diarize and not os.environ.get("HF_TOKEN", "").strip():
        raise ValueError("話者分離にはHF_TOKENが必要です。")

    asr_options = {
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
        result = model.transcribe(audio, batch_size=args.batch_size, chunk_size=args.chunk_size, print_progress=True)
    finally:
        del model
        _release_memory(args.device)

    language = result["language"]
    if result["segments"]:
        align_model, metadata = whisperx.load_align_model(language_code=language, device=args.device)
        try:
            # 切り詰めた音声ではなく元の音声と絶対時刻を渡し、語単位で時刻を合わせる。
            from whisperx import alignment

            with blank_aware_alignment(alignment):
                result = whisperx.align(
                    result["segments"],
                    align_model,
                    metadata,
                    audio,
                    args.device,
                    interpolate_method=args.interpolate_method,
                    print_progress=True,
                )
        finally:
            del align_model
            _release_memory(args.device)
    result["language"] = language
    if args.diarize and result["segments"]:
        from whisperx.diarize import DiarizationPipeline, assign_word_speakers

        diarizer = DiarizationPipeline(token=os.environ["HF_TOKEN"], device=args.device)
        try:
            speakers = diarizer(audio, min_speakers=args.min_speakers, max_speakers=args.max_speakers)
            result = assign_word_speakers(speakers, result)
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
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
