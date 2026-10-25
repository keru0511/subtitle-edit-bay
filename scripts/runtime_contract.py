from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, Sequence, TypeGuard, cast


CONTRACT_PATH = Path("runtime/runtime-contract.json")
LOCK_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)")
VERSION_PREFIX = re.compile(r"^(?:ffmpeg|ffprobe) version (\d+)(?:\.|\s)")


def is_string_object_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    """セットアップ前にも単独で実行できるようJSON境界をこのスクリプト内で検証する。"""
    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value)


def is_object_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def decode_json(text: str) -> object:
    payload: object = json.loads(text)
    return payload


class RuntimeContractError(ValueError):
    """Raised when a release runtime contract is incomplete or violated."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not is_string_object_mapping(value):
        raise RuntimeContractError(f"{label} must be an object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeContractError(f"{label} must be a string")
    return value


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeContractError(f"{label} must be an integer")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not is_object_list(value):
        raise RuntimeContractError(f"{label} must be a string list")
    result: list[str] = []
    for item in value:
        result.append(_string(item, label))
    return result


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def load_contract(root: Path) -> Mapping[str, object]:
    path = root / CONTRACT_PATH
    try:
        payload = decode_json(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeContractError(f"could not read runtime contract {path}: {error}") from error
    if not is_string_object_mapping(payload) or payload.get("schema_version") != 1:
        raise RuntimeContractError("runtime contract schema_version must be 1")
    return payload


def parse_lock(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeContractError(f"could not read runtime lock {path}: {error}") from error
    packages: dict[str, str] = {}
    current_name = ""
    current_has_hash = False
    for line in lines:
        match = LOCK_REQUIREMENT.match(line)
        if match:
            if current_name and not current_has_hash:
                raise RuntimeContractError(f"locked package has no wheel hash: {current_name}")
            current_name = _normalized_name(match.group(1))
            if current_name in packages:
                raise RuntimeContractError(f"duplicate locked package: {current_name}")
            packages[current_name] = match.group(2)
            current_has_hash = "--hash=sha256:" in line
        elif current_name and "--hash=sha256:" in line:
            current_has_hash = True
    if current_name and not current_has_hash:
        raise RuntimeContractError(f"locked package has no wheel hash: {current_name}")
    if not packages:
        raise RuntimeContractError(f"runtime lock has no exact package pins: {path}")
    return packages


def lock_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile(contract: Mapping[str, object], name: str) -> Mapping[str, object]:
    profiles = contract.get("profiles")
    if not is_string_object_mapping(profiles) or not is_string_object_mapping(profiles.get(name)):
        raise RuntimeContractError(f"unknown runtime profile: {name}")
    return _mapping(profiles[name], f"runtime profile {name}")


def validate_contract(root: Path) -> dict[str, dict[str, str]]:
    contract = load_contract(root)
    python = contract.get("python")
    ffmpeg = contract.get("ffmpeg")
    imports = contract.get("critical_imports")
    if not is_string_object_mapping(python) or python.get("major_minor") != "3.10":
        raise RuntimeContractError("runtime contract must define the Python 3.10 policy")
    if not is_string_object_mapping(ffmpeg) or not all(
        isinstance(ffmpeg.get(key), int) for key in ("minimum_major", "maximum_major_exclusive")
    ):
        raise RuntimeContractError("runtime contract must define the FFmpeg supported major range")
    if not is_object_list(imports) or not imports or not all(isinstance(value, str) and value for value in imports):
        raise RuntimeContractError("runtime contract critical_imports must be a non-empty string list")

    resolved: dict[str, dict[str, str]] = {}
    for profile_name in ("cpu", "cu128"):
        profile = _profile(contract, profile_name)
        lock_name = profile.get("lock_file")
        if not isinstance(lock_name, str):
            raise RuntimeContractError(f"profile {profile_name} has no lock_file")
        lock_path = root / lock_name
        packages = parse_lock(lock_path)
        for required in ("budoux", "janome", "numpy", "pyside6", "whisperx", "torch", "torchvision", "torchaudio"):
            if required not in packages:
                raise RuntimeContractError(f"profile {profile_name} does not lock {required}")
        if packages["torch"] != str(profile.get("torch_version")):
            raise RuntimeContractError(f"profile {profile_name} torch pin does not match its contract")
        resolved[profile_name] = packages
    return resolved


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+(?:\.\d+)*)", value)
    if not match:
        raise RuntimeContractError(f"invalid version: {value!r}")
    matched: object = match.group(1)
    assert isinstance(matched, str)
    return tuple(int(part) for part in matched.split("."))


def verify_python(contract: Mapping[str, object]) -> None:
    policy = _mapping(contract.get("python"), "Python runtime policy")
    minimum = _string(policy.get("minimum"), "Python minimum version")
    maximum = _string(policy.get("maximum_exclusive"), "Python maximum version")
    actual = (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    if not (_version_tuple(minimum) <= actual < _version_tuple(maximum)):
        raise RuntimeContractError(f"Python {'.'.join(map(str, actual))} is outside [{minimum}, {maximum})")


def verify_tools(contract: Mapping[str, object]) -> dict[str, str]:
    policy = _mapping(contract.get("ffmpeg"), "FFmpeg runtime policy")
    versions = {name: _tool_version(name, policy) for name in ("ffmpeg", "ffprobe")}
    with tempfile.TemporaryDirectory(prefix="subtitle-edit-bay-runtime-probe-") as temp_dir:
        fixture = Path(temp_dir) / "probe.mkv"
        commands = (
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=16x16:d=0.1",
                "-frames:v",
                "1",
                "-c:v",
                "ffv1",
                "-y",
                str(fixture),
            ],
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(fixture),
            ],
        )
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                raise RuntimeContractError(f"media capability probe could not run: {error}") from error
            if result.returncode != 0:
                raise RuntimeContractError(
                    f"media capability probe failed for {command[0]}: {(result.stderr or '').strip()}"
                )
        try:
            probe = decode_json(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeContractError("ffprobe capability result was not valid JSON") from error
        if not is_string_object_mapping(probe) or not probe.get("streams"):
            raise RuntimeContractError("ffprobe capability probe found no video stream")
    return versions


def _tool_version(name: str, policy: Mapping[str, object]) -> str:
    try:
        result = subprocess.run(
            [name, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeContractError(f"{name} -version could not run: {error}") from error
    first_line = (result.stdout or result.stderr or "").splitlines()
    if result.returncode != 0 or not first_line:
        raise RuntimeContractError(f"{name} -version failed with exit code {result.returncode}")
    match = VERSION_PREFIX.match(first_line[0])
    if not match:
        raise RuntimeContractError(f"could not parse {name} version: {first_line[0]!r}")
    major = int(match.group(1))
    minimum_major = _integer(policy.get("minimum_major"), "FFmpeg minimum major")
    maximum_major = _integer(policy.get("maximum_major_exclusive"), "FFmpeg maximum major")
    if not minimum_major <= major < maximum_major:
        raise RuntimeContractError(f"{name} major version {major} is outside the supported range")
    return first_line[0]


class _TorchVersion(Protocol):
    cuda: str | None


class _TorchCuda(Protocol):
    def is_available(self) -> bool: ...

    def get_device_name(self, index: int) -> str: ...


class _TorchRuntime(Protocol):
    __version__: str
    cuda: _TorchCuda
    version: _TorchVersion


def verify_runtime(root: Path, profile_name: str, manifest_output: Path) -> dict[str, object]:
    contract = load_contract(root)
    locked_profiles = validate_contract(root)
    profile = _profile(contract, profile_name)
    verify_python(contract)
    lock_path = root / _string(profile.get("lock_file"), "runtime profile lock_file")
    installed: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        try:
            name = distribution.metadata["Name"]
        except KeyError:
            continue
        if name:
            installed[_normalized_name(name)] = distribution.version
    mismatches = [
        f"{name}: expected {version}, got {installed.get(name, 'not installed')}"
        for name, version in sorted(locked_profiles[profile_name].items())
        if installed.get(name) != version
    ]
    if mismatches:
        raise RuntimeContractError("installed packages do not match the runtime lock:\n- " + "\n- ".join(mismatches))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for module_name in _string_list(contract.get("critical_imports"), "runtime critical_imports"):
        try:
            importlib.import_module(module_name)
        except (ImportError, OSError, RuntimeError) as error:
            raise RuntimeContractError(f"critical import failed for {module_name}: {error}") from error

    torch_module: object = importlib.import_module("torch")
    torch = cast(_TorchRuntime, torch_module)
    cuda_available = bool(torch.cuda.is_available())
    if profile_name == "cu128" and not cuda_available:
        raise RuntimeContractError("cu128 profile is installed but torch.cuda.is_available() is false")
    tool_versions = verify_tools(contract)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "app_version": (
            (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").is_file() else "development"
        ),
        "setup_schema_version": _integer(contract.get("setup_schema_version"), "setup_schema_version"),
        "profile": profile_name,
        "python_version": sys.version.split()[0],
        "packages": dict(sorted(installed.items())),
        "torch_version": str(torch.__version__),
        "cuda_build": str(torch.version.cuda or "none"),
        "cuda_available": cuda_available,
        "gpu_name": str(torch.cuda.get_device_name(0)) if cuda_available else "",
        "ffmpeg_version": tool_versions["ffmpeg"],
        "ffprobe_version": tool_versions["ffprobe"],
        "media_capability_probe": "passed",
        "lock_file": _string(profile.get("lock_file"), "runtime profile lock_file"),
        "lock_sha256": lock_sha256(lock_path),
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


class RuntimeArguments(argparse.Namespace):
    command: str
    root: Path
    profile: str = "cpu"
    manifest_output: Path | None = None


def parse_args(argv: Sequence[str] | None = None) -> RuntimeArguments:
    parser = argparse.ArgumentParser(description="Validate and verify a release runtime contract.")
    parser.add_argument("command", choices=("validate", "verify-python", "verify-tools", "verify-runtime"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", choices=("cpu", "cu128"), default="cpu")
    parser.add_argument("--manifest-output", type=Path)
    args = RuntimeArguments()
    parser.parse_args(argv, namespace=args)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "validate":
            profiles = validate_contract(root)
            counts: dict[str, int] = {name: len(packages) for name, packages in profiles.items()}
            print(json.dumps(counts, sort_keys=True))
        elif args.command == "verify-python":
            verify_python(load_contract(root))
            print(sys.version.split()[0])
        elif args.command == "verify-tools":
            print(json.dumps(verify_tools(load_contract(root)), sort_keys=True))
        else:
            manifest_output = args.manifest_output
            if manifest_output is None:
                raise RuntimeContractError("--manifest-output is required for verify-runtime")
            print(json.dumps(verify_runtime(root, args.profile, manifest_output), ensure_ascii=False))
    except RuntimeContractError as error:
        print(f"runtime contract error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
