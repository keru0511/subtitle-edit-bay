from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
import unicodedata
import wave
from array import array
from pathlib import Path


def sha256_file(path: Path) -> str:
    """長い録画でも全体をメモリに読み込まずに同一性を確認する。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    """表記の全半角・句読点・空白だけを揃える。反復文字は削除しない。"""
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", text).casefold()
        if not char.isspace() and unicodedata.category(char)[0] not in {"P", "Z"}
    )


def align_characters(reference: str, hypothesis: str) -> list[tuple[str, int | None, int | None]]:
    """文字編集距離と、時刻評価にも使う対応を求める。"""
    costs = [[0] * (len(hypothesis) + 1) for _ in range(len(reference) + 1)]
    for i in range(len(reference) + 1):
        costs[i][0] = i
    for j in range(len(hypothesis) + 1):
        costs[0][j] = j
    for i, expected in enumerate(reference, 1):
        for j, actual in enumerate(hypothesis, 1):
            costs[i][j] = min(costs[i - 1][j - 1] + (expected != actual), costs[i - 1][j] + 1, costs[i][j - 1] + 1)
    result = []
    i, j = len(reference), len(hypothesis)
    while i or j:
        if i and j and costs[i][j] == costs[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]):
            result.append(("equal" if reference[i - 1] == hypothesis[j - 1] else "substitute", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and costs[i][j] == costs[i - 1][j] + 1:
            result.append(("delete", i - 1, None))
            i -= 1
        else:
            result.append(("insert", None, j - 1))
            j -= 1
    return list(reversed(result))


def validated_time(item: dict, duration: float) -> tuple[float, float] | None:
    try:
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start <= end <= duration + 0.01:
        return None
    return start, end


def transcript_characters(payload: dict, duration: float) -> tuple[str, list[tuple[float, float] | None], int]:
    if not isinstance(payload.get("segments"), list):
        raise ValueError("文字起こしJSONにsegmentsがありません。")
    text_parts, timings = [], []
    invalid_segments = 0
    for segment in payload["segments"]:
        text = normalize_text(segment.get("text", ""))
        text_parts.append(text)
        if text and validated_time(segment, duration) is None:
            invalid_segments += 1
        word_text, word_times = "", []
        for word in segment.get("words", []):
            normalized = normalize_text(word.get("word", ""))
            word_text += normalized
            word_times.extend([validated_time(word, duration)] * len(normalized))
        segment_times = [None] * len(text)
        for operation, text_index, word_index in align_characters(text, word_text):
            if operation == "equal":
                segment_times[text_index] = word_times[word_index]
        timings.extend(segment_times)
    return "".join(text_parts), timings, invalid_segments


def canonical_spelling(text: str, timings: list, equivalents: dict[str, str]) -> tuple[str, list]:
    """固定素材で明示した表記だけを統一し、元の時間区間を保つ。"""
    result, result_times = [], []
    index = 0
    keys = sorted(equivalents, key=len, reverse=True)
    if any(not key or not equivalents[key] for key in keys):
        raise ValueError("表記揺れの定義に空文字は使えません。")
    while index < len(text):
        matched = next((key for key in keys if text.startswith(key, index)), None)
        if matched is None:
            result.append(text[index])
            result_times.append(timings[index])
            index += 1
            continue
        replacement = equivalents[matched]
        original_times = timings[index : index + len(matched)]
        timing = None
        if all(value is not None for value in original_times):
            timing = (min(value[0] for value in original_times), max(value[1] for value in original_times))
        result.append(replacement)
        result_times.extend([timing] * len(replacement))
        index += len(matched)
    return "".join(result), result_times


def reference_entries(manifest: dict) -> list[tuple[str, tuple[float, float]]]:
    """録音の配置区間、または実音声内で人手確認した発話区間を返す。"""
    entries = manifest.get("speech_windows", manifest["clips"])
    duration = float(manifest["duration"])
    result: list[tuple[str, tuple[float, float]]] = []
    previous_end = 0.0
    for entry in entries:
        start = float(entry["start"])
        end = float(entry["end"]) if "end" in entry else start + float(entry["duration"])
        text = normalize_text(entry.get("text", ""))
        if (
            not text
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not previous_end <= start < end <= duration
        ):
            raise ValueError("正解発話は本文と、音声内で重ならない有効な開始・終了時刻が必要です。")
        result.append((text, (start, end)))
        previous_end = end
    if not result:
        raise ValueError("正解発話が空です。")
    return result


def score_transcript(payload: dict, manifest: dict) -> dict:
    reference, reference_windows = "", []
    raw_reference = ""
    equivalents = manifest.get("equivalent_spellings", {})
    windows = []
    for text, window in reference_entries(manifest):
        raw_reference += text
        text, _ = canonical_spelling(text, [None] * len(text), equivalents)
        reference += text
        reference_windows.extend([window] * len(text))
        windows.append(window)
    raw_hypothesis, times, invalid_segments = transcript_characters(payload, manifest["duration"])
    raw_operations = align_characters(raw_reference, raw_hypothesis)
    hypothesis, times = canonical_spelling(raw_hypothesis, times, equivalents)
    operations = align_characters(reference, hypothesis)
    counts = {
        name: sum(operation == name for operation, _, _ in operations) for name in ["insert", "delete", "substitute"]
    }
    tolerance = manifest["limits"]["timing_tolerance_seconds"]

    def excess(timing, window):
        return max(0.0, window[0] - timing[0], timing[1] - window[1])

    outside = sum(
        timing is not None and all(excess(timing, window) > tolerance for window in windows) for timing in times
    )
    timing_errors, max_excess = 0, 0.0
    for operation, reference_index, hypothesis_index in operations:
        if operation != "equal" or times[hypothesis_index] is None:
            continue
        value = excess(times[hypothesis_index], reference_windows[reference_index])
        max_excess = max(max_excess, value)
        timing_errors += value > tolerance
    return {
        "reference": reference,
        "hypothesis": hypothesis,
        "raw_reference": raw_reference,
        "raw_hypothesis": raw_hypothesis,
        "raw_cer": sum(operation != "equal" for operation, _, _ in raw_operations) / len(raw_reference),
        "raw_deletions": sum(operation == "delete" for operation, _, _ in raw_operations),
        "reference_characters": len(reference),
        "hypothesis_characters": len(hypothesis),
        "insertions": counts["insert"],
        "deletions": counts["delete"],
        "substitutions": counts["substitute"],
        "cer": sum(counts.values()) / len(reference),
        "outside_speech_characters": outside,
        "untimed_characters": sum(timing is None for timing in times),
        "invalid_segments": invalid_segments,
        "timing_window_errors": timing_errors,
        "max_window_excess_seconds": max_excess,
    }


def quality_failures(baseline: dict, candidate: dict, limits: dict) -> list[str]:
    failures = []
    if candidate["cer"] > limits["max_cer"]:
        failures.append("文字誤り率が絶対上限を超えました。")
    if candidate["cer"] > baseline["cer"] + limits["max_cer_regression"] + 1e-9:
        failures.append("文字誤り率が比較対象より悪化しました。")
    for key in ["insertions", "deletions"]:
        if candidate[key] > baseline[key]:
            failures.append(f"{key}が比較対象より増加しました。")
    for key in ["outside_speech_characters", "untimed_characters", "timing_window_errors"]:
        if candidate[key] > limits[f"max_{key}"]:
            failures.append(f"{key}が許容値を超えました。")
    if candidate["invalid_segments"]:
        failures.append("不正な字幕時刻があります。")
    return failures


def prepare_audio(manifest_path: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rate = manifest["sample_rate"]
    samples = array("h", [0]) * round(manifest["duration"] * rate)
    for clip in manifest["clips"]:
        source = manifest_path.parent / clip["audio"]
        if sha256_file(source) != clip["sha256"]:
            raise ValueError(f"検証音声のハッシュが不一致です: {clip['id']}")
        decode_command = ["ffmpeg", "-v", "error"]
        if "source_start_seconds" in clip:
            source_start = float(clip["source_start_seconds"])
            if not math.isfinite(source_start) or source_start < 0:
                raise ValueError("素材の切り出し開始時刻が不正です。")
            # 入力前のシークで長い録画の先頭からのデコードを避ける。再エンコード時は正確にシークされる。
            decode_command.extend(["-ss", str(source_start)])
        decode_command.extend(["-i", str(source)])
        if clip.get("audio_stream"):
            decode_command.extend(["-map", str(clip["audio_stream"])])
        if "source_start_seconds" in clip:
            decode_command.extend(["-t", str(clip["duration"])])
        decode_command.extend(["-ac", "1", "-ar", str(rate), "-f", "s16le", "-"])
        decoded = subprocess.check_output(decode_command)
        part = array("h")
        part.frombytes(decoded)
        if sys.byteorder != "little":
            part.byteswap()
        start = round(clip["start"] * rate)
        if abs(len(part) / rate - clip["duration"]) > 0.01:
            raise ValueError("参照区間と音声の長さが一致しません。")
        if start < 0 or start + len(part) > len(samples):
            raise ValueError("参照区間が音声全体の範囲外です。")
        samples[start : start + len(part)] = part
    randomizer = random.Random(manifest.get("noise_seed", 0))
    for start, end in manifest.get("noise_intervals", []):
        if any(start < clip["start"] + clip["duration"] and end > clip["start"] for clip in manifest["clips"]):
            raise ValueError("負例の雑音が発話区間と重なっています。")
        for index in range(round(start * rate), round(end * rate)):
            samples[index] = randomizer.randint(-300, 300)
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return manifest


def run_revision(
    root: Path,
    audio: Path,
    output: Path,
    *,
    model: str = "large-v3",
    device: str = "cpu",
    compute_type: str = "int8",
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    # 比較対象の設定とコマンド生成を、そのcheckoutから読み込む。
    program = """import json,sys
from src.transcribe import build_whisperx_command
from src.runtime_settings import TranscriptionSettings
settings = TranscriptionSettings()
print(json.dumps(build_whisperx_command(sys.argv[1],sys.argv[2],model=sys.argv[3],device=sys.argv[4],compute_type=sys.argv[5],language='ja',vad_onset=settings.vad_onset,vad_offset=settings.vad_offset)))
"""
    environment = {**os.environ, "PYTHONPATH": str(root), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    command = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", program, str(audio), str(output), model, device, compute_type],
            cwd=root,
            env=environment,
            text=True,
            encoding="utf-8",
        )
    )
    (output / "command.json").write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")
    started = time.perf_counter()
    with (output / "recognition.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            command, cwd=root, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600
        )
    elapsed = time.perf_counter() - started
    transcript = json.loads((output / f"{audio.stem}.json").read_text(encoding="utf-8"))
    return {
        "transcript": transcript,
        "elapsed_seconds": elapsed,
        "command": command,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    }


def write_report(output: Path, report: dict) -> None:
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    manifest = report.get("manifest", {})
    speech_count = len(manifest.get("speech_windows", manifest.get("clips", [])))
    runtime = report.get("asr_runtime")
    runtime_description = (
        f"{runtime['model']} / {runtime['device']} {runtime['compute_type']} / 日本語"
        if runtime
        else "既存JSONの実行条件は未検証"
    )
    comparison = "baseline" in report and "candidate" in report
    gameplay = "speech_windows" in manifest
    if comparison:
        title = "実音声による初回文字起こし比較"
    elif report.get("scored_existing_json"):
        title = "既存の文字起こしJSONの採点"
    else:
        title = "実音声の文字起こし採点"
    lines = [
        f"# {title}",
        "",
        f"認識モデル: {runtime_description}。",
        f"録音{len(manifest.get('clips', []))}件、正解発話{speech_count}区間、音声{manifest.get('duration', '不明')}秒。",
        "",
    ]
    metrics = [
        "raw_cer",
        "raw_deletions",
        "cer",
        "insertions",
        "deletions",
        "substitutions",
        "outside_speech_characters",
        "untimed_characters",
        "timing_window_errors",
        "max_window_excess_seconds",
        "elapsed_seconds",
    ]
    if "baseline" in report and "candidate" in report:
        lines += ["| 指標 | 比較対象 | 変更後 |", "| --- | ---: | ---: |"]
        for metric in metrics:
            lines.append(f"| {metric} | {report['baseline'][metric]:.4f} | {report['candidate'][metric]:.4f} |")
        lines += ["", f"比較対象: `{report['baseline']['commit']}`", f"変更後: `{report['candidate']['commit']}`", ""]
    elif "baseline" in report or "candidate" in report:
        result = report.get("candidate", report.get("baseline", {}))
        lines += ["| 指標 | 認識結果 |", "| --- | ---: |"]
        for metric in metrics:
            if metric in result:
                lines.append(f"| {metric} | {result[metric]:.4f} |")
        lines.append("")
    lines += ["## 判定", ""] + (
        report.get("failures")
        or (
            [
                "この実況素材の定義した基準で回帰なし。未収録の場面への効果は未検証。"
                if gameplay
                else "この固定素材での回帰検査に合格。実際のゲーム実況における改善を証明するものではありません。"
            ]
            if comparison
            else [
                "既存の認識JSONを採点しました。推論・比較判定は実行していません。"
                if report.get("scored_existing_json")
                else "片側の実認識を完了。比較判定は集約ジョブで行います。"
            ]
        )
    )
    lines += [
        "",
        "raw_cerは表記差を含む値、cerは素材で明示した同等表記だけを統一した値です。",
        "時刻評価は人手の発話区間または既知の録音配置区間からの逸脱です。単語の正解開始・終了時刻に対する誤差ではありません。",
        "",
    ]
    text = "\n".join(lines)
    (output / "report.md").write_text(text, encoding="utf-8")
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
            handle.write(text)


def merge_reports(baseline: dict, candidate: dict) -> dict:
    """別ランナーの実認識結果を、同一条件を確認して比較する。"""
    for name, report in [("baseline", baseline), ("candidate", candidate)]:
        if report.get("failures") or name not in report:
            raise ValueError(f"{name}の実認識が完了していません")
    for key in ["schema_version", "manifest", "audio_sha256", "versions", "model_snapshots"]:
        if key not in baseline or key not in candidate or baseline[key] != candidate[key]:
            raise ValueError(f"比較条件が一致しません: {key}")
    if baseline.get("asr_runtime") != candidate.get("asr_runtime"):
        raise ValueError("比較条件が一致しません: asr_runtime")
    result = {**candidate, "baseline": baseline["baseline"]}
    result["execution_environments"] = {
        name: {key: report[key] for key in ["python", "platform"]}
        for name, report in [("baseline", baseline), ("candidate", candidate)]
    }
    result["failures"] = quality_failures(result["baseline"], result["candidate"], result["manifest"]["limits"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="実モデルの初回認識を2つのcheckoutで比較します。")
    parser.add_argument("--baseline-root", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--revision", choices=["baseline", "candidate"])
    parser.add_argument("--merge-reports", nargs=2, type=Path, metavar=("BASELINE", "CANDIDATE"))
    parser.add_argument("--score-json", type=Path, help="既存のWhisperX認識JSONをモデル起動なしで採点します。")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument(
        "--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "assets/asr_benchmark/manifest.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sum(bool(value) for value in [args.merge_reports, args.revision, args.score_json]) > 1:
        parser.error("--merge-reports、--revision、--score-jsonは同時指定できません")
    revisions = [("baseline", args.baseline_root), ("candidate", args.candidate_root)]
    if args.revision:
        revisions = [(name, root) for name, root in revisions if name == args.revision]
    if not args.merge_reports and not args.score_json and any(root is None for _, root in revisions):
        parser.error("認識対象のcheckoutを指定してください")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "python": sys.version, "platform": platform.platform(), "failures": []}
    try:
        if args.merge_reports:
            report = merge_reports(*(json.loads(path.read_text(encoding="utf-8")) for path in args.merge_reports))
            write_report(output, report)
            return 1 if report["failures"] else 0
        if args.score_json:
            manifest = json.loads(args.manifest.resolve().read_text(encoding="utf-8"))
            transcript = json.loads(args.score_json.read_text(encoding="utf-8"))
            report["manifest"] = manifest
            report["candidate"] = score_transcript(transcript, manifest)
            report["scored_existing_json"] = True
            report["transcript_sha256"] = sha256_file(args.score_json)
            write_report(output, report)
            return 0
        report["asr_runtime"] = {"model": args.model, "device": args.device, "compute_type": args.compute_type}
        report["versions"] = {
            name: importlib.metadata.version(name) for name in ["whisperx", "faster-whisper", "ctranslate2", "torch"]
        }
        audio = output / "japanese.wav"
        manifest = prepare_audio(args.manifest.resolve(), audio)
        report["manifest"] = manifest
        report["audio_sha256"] = sha256_file(audio)
        for name, root in revisions:
            print(f"{name}: {args.model}の実認識と時刻合わせを開始します。", flush=True)
            run = run_revision(
                root.resolve(),
                audio,
                output / name,
                model=args.model,
                device=args.device,
                compute_type=args.compute_type,
            )
            report[name] = {
                **score_transcript(run["transcript"], manifest),
                **{key: value for key, value in run.items() if key != "transcript"},
            }
        cache_root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface")))
        report["model_snapshots"] = sorted(
            str(path.relative_to(cache_root)) for path in cache_root.glob("hub/models--*/snapshots/*") if path.is_dir()
        )
        if not args.revision:
            report["failures"] = quality_failures(report["baseline"], report["candidate"], manifest["limits"])
    except Exception as error:
        report["failures"].append(f"実認識検証が完了しませんでした: {type(error).__name__}: {error}")
    write_report(output, report)
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
