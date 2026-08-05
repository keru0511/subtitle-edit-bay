from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

def probe_audio_streams(input_path: str) -> list[dict[str, object]]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index,codec_name,channels:stream_tags=language,title",
        "-of",
        "json",
        input_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    payload = json.loads(result.stdout or "{}")
    return payload.get("streams", [])


def build_extract_audio_command(input_path: str, output_path: str, audio_track: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-map",
        audio_track,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        output_path,
    ]


def validate_hf_token(diarize: bool) -> None:
    if diarize and not os.environ.get("HF_TOKEN", "").strip():
        raise SystemExit("Diarization requires the HF_TOKEN environment variable. Omit --diarize when it is not needed.")


def build_whisperx_command(
    audio_path: str,
    output_dir: str,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
    diarize: bool = False,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    language: str | None = "ja",
    vad_onset: float | None = None,
    vad_offset: float | None = None,
) -> list[str]:
    validate_hf_token(diarize)
    command = [
        sys.executable,
        "-m",
        "whisperx",
        audio_path,
        "--model",
        model,
        "--device",
        device,
        "--compute_type",
        compute_type,
        "--output_dir",
        output_dir,
        "--output_format",
        "json",
    ]
    if language:
        command.extend(["--language", language])
    if vad_onset is not None:
        command.extend(["--vad_onset", str(vad_onset)])
    if vad_offset is not None:
        command.extend(["--vad_offset", str(vad_offset)])
    if diarize:
        command.append("--diarize")
        if min_speakers is not None:
            command.extend(["--min_speakers", str(min_speakers)])
        if max_speakers is not None:
            command.extend(["--max_speakers", str(max_speakers)])
    return command


def expected_transcript_path(audio_path: str, output_dir: str) -> Path:
    return Path(output_dir) / f"{Path(audio_path).stem}.json"


def expected_log_path(audio_path: str, output_dir: str) -> Path:
    return Path(output_dir) / f"{Path(audio_path).stem}.whisperx.log"


def run_command_with_utf8_log(command: list[str], log_path: str) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=environment,
        )
    except OSError as error:
        path.write_text(f"Failed to start process: {error}\n", encoding="utf-8")
        raise

    with path.open("w", encoding="utf-8") as log_file:
        if process.stdout is not None:
            for line in process.stdout:
                print(line, end="", flush=True)
                log_file.write(line)
                log_file.flush()
            process.stdout.close()
        return_code = process.wait()
        if return_code:
            exit_message = f"\nProcess exited with code {return_code}.\n"
            print(exit_message, end="", flush=True)
            log_file.write(exit_message)
            log_file.flush()
            raise subprocess.CalledProcessError(return_code, command)


def print_streams(streams: list[dict[str, object]]) -> None:
    if not streams:
        print("No audio streams found.")
        return

    for order, stream in enumerate(streams):
        tags = stream.get("tags", {}) or {}
        stream_spec = f"0:a:{order}"
        details = [
            f"map={stream_spec}",
            f"ffmpeg_index={stream.get('index', '?')}",
            f"codec={stream.get('codec_name', '?')}",
            f"channels={stream.get('channels', '?')}",
        ]
        if tags.get("language"):
            details.append(f"language={tags['language']}")
        if tags.get("title"):
            details.append(f"title={tags['title']}")
        print(" | ".join(details))


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe MKV audio tracks and run WhisperX safely.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe", help="List audio tracks in a media file.")
    probe_parser.add_argument("--input", required=True, help="Input media path.")

    extract_parser = subparsers.add_parser("extract", help="Extract one audio track as WAV.")
    extract_parser.add_argument("--input", required=True, help="Input media path.")
    extract_parser.add_argument("--audio-track", default="0:a:0", help="Track selector such as 0:a:0.")
    extract_parser.add_argument("--output", required=True, help="Output WAV path.")
    extract_parser.add_argument("--run", action="store_true", help="Execute instead of printing.")

    run_parser = subparsers.add_parser("run", help="Run WhisperX on a chosen audio track.")
    run_parser.add_argument("--input", required=True, help="Input media path.")
    run_parser.add_argument("--audio-track", default="0:a:0", help="Track selector such as 0:a:0.")
    run_parser.add_argument("--output-dir", required=True, help="Directory for extracted audio and transcript JSON.")
    run_parser.add_argument("--model", default="large-v3", help="WhisperX model name.")
    run_parser.add_argument("--device", default="cpu", help="WhisperX device, e.g. cpu or cuda.")
    run_parser.add_argument("--compute-type", default="int8", help="WhisperX compute type.")
    run_parser.add_argument("--diarize", action="store_true", help="Enable diarization for this track.")
    run_parser.add_argument("--min-speakers", type=int, help="Minimum speaker count for diarization.")
    run_parser.add_argument("--max-speakers", type=int, help="Maximum speaker count for diarization.")
    run_parser.add_argument("--language", default="ja", help="Language code passed to WhisperX.")
    run_parser.add_argument("--vad-onset", type=float, default=0.35, help="VAD onset threshold passed to WhisperX.")
    run_parser.add_argument("--vad-offset", type=float, default=0.2, help="VAD offset threshold passed to WhisperX.")
    run_parser.add_argument("--run", action="store_true", help="Execute instead of printing.")

    args = parser.parse_args()

    if args.command == "probe":
        print_streams(probe_audio_streams(args.input))
        return

    if args.command == "extract":
        command = build_extract_audio_command(args.input, args.output, args.audio_track)
        if args.run:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(command, check=True)
        else:
            print(" ".join(command))
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted_audio = output_dir / f"{Path(args.input).stem}.{args.audio_track.replace(':', '_')}.wav"
    extract_command = build_extract_audio_command(args.input, str(extracted_audio), args.audio_track)
    whisperx_command = build_whisperx_command(
        str(extracted_audio),
        str(output_dir),
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        diarize=args.diarize,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        language=args.language,
        vad_onset=args.vad_onset,
        vad_offset=args.vad_offset,
    )
    log_path = expected_log_path(str(extracted_audio), str(output_dir))

    if args.run:
        subprocess.run(extract_command, check=True)
        run_command_with_utf8_log(whisperx_command, str(log_path))
        print(expected_transcript_path(str(extracted_audio), str(output_dir)))
        print(log_path)
        return

    print("Extract command:")
    print(" ".join(extract_command))
    print()
    print("WhisperX command:")
    print(" ".join(whisperx_command))
    print()
    print(f"Expected transcript: {expected_transcript_path(str(extracted_audio), str(output_dir))}")
    print(f"WhisperX log: {log_path}")


if __name__ == "__main__":
    main()
