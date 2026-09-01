from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Sequence


INSTALLER_NAME = "SubtitleEditBay-Setup.exe"
CHECKSUM_NAME = f"{INSTALLER_NAME}.sha256"
MANIFEST_NAME = f"{INSTALLER_NAME}.manifest.json"
REQUIRED_INSTALLED_FILES = {
    "VERSION",
    "scripts/launch.ps1",
    "scripts/apply_installer_update.ps1",
}
VERSION_PATTERN = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
TAG_PATTERN = re.compile(r"^v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
CHECKSUM_PATTERN = re.compile(rf"^([0-9a-fA-F]{{64}}) [ *]{re.escape(INSTALLER_NAME)}$")


class ReleaseContractError(ValueError):
    """Raised when release inputs or artifacts are inconsistent."""


def release_version_from_tag(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag.strip())
    if not match:
        raise ReleaseContractError(f"release tag must use vX.Y.Z without leading zeroes: {tag}")
    return match.group(1)


def validate_source_version(tag: str, version_file: Path) -> str:
    expected_version = release_version_from_tag(tag)
    try:
        stored_version = version_file.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ReleaseContractError(f"could not read version file {version_file}: {exc}") from exc
    normalized_version = stored_version.removeprefix("v")
    if not VERSION_PATTERN.fullmatch(normalized_version):
        raise ReleaseContractError(f"VERSION must use X.Y.Z or vX.Y.Z without leading zeroes: {stored_version}")
    if normalized_version != expected_version:
        raise ReleaseContractError(f"release tag {tag} does not match VERSION {stored_version}")
    return expected_version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseContractError(f"could not hash {path}: {exc}") from exc
    return digest.hexdigest()


def _read_checksum(path: Path) -> str:
    try:
        checksum_text = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise ReleaseContractError(f"could not read checksum {path}: {exc}") from exc
    match = CHECKSUM_PATTERN.fullmatch(checksum_text)
    if not match:
        raise ReleaseContractError(f"checksum must contain SHA-256 and {INSTALLER_NAME}: {path}")
    return match.group(1).lower()


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseContractError(f"release manifest must be an object: {path}")
    return payload


def verify_release_artifacts(directory: Path, expected_version: str) -> str:
    if not VERSION_PATTERN.fullmatch(expected_version):
        raise ReleaseContractError(f"expected version must use X.Y.Z without leading zeroes: {expected_version}")
    installer_path = directory / INSTALLER_NAME
    checksum_path = directory / CHECKSUM_NAME
    manifest_path = directory / MANIFEST_NAME
    missing = [path.name for path in (installer_path, checksum_path, manifest_path) if not path.is_file()]
    if missing:
        raise ReleaseContractError(f"release artifacts are missing: {', '.join(missing)}")

    expected_digest = _read_checksum(checksum_path)
    actual_digest = _sha256(installer_path)
    if actual_digest != expected_digest:
        raise ReleaseContractError(f"installer SHA-256 mismatch: expected {expected_digest}, got {actual_digest}")

    manifest = _read_manifest(manifest_path)
    expected_values = {
        "schema_version": 1,
        "package_type": "installer",
        "app_version": expected_version,
        "asset_name": INSTALLER_NAME,
        "sha256": actual_digest,
    }
    for key, expected_value in expected_values.items():
        if manifest.get(key) != expected_value:
            raise ReleaseContractError(
                f"manifest {key} mismatch: expected {expected_value!r}, got {manifest.get(key)!r}"
            )

    required_files = manifest.get("required_files")
    if not isinstance(required_files, list) or not all(isinstance(item, str) for item in required_files):
        raise ReleaseContractError("manifest required_files must be a list of strings")
    missing_required_files = sorted(REQUIRED_INSTALLED_FILES - set(required_files))
    if missing_required_files:
        raise ReleaseContractError("manifest required_files is incomplete: " + ", ".join(missing_required_files))
    return actual_digest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate release source metadata and generated installer artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    source_parser = subparsers.add_parser(
        "validate-source",
        help="Require the release tag and VERSION file to match.",
    )
    source_parser.add_argument("--tag", required=True)
    source_parser.add_argument("--version-file", type=Path, default=Path("VERSION"))

    artifacts_parser = subparsers.add_parser(
        "verify-artifacts",
        help="Verify installer checksum, manifest, and expected version.",
    )
    artifacts_parser.add_argument("--directory", type=Path, required=True)
    artifacts_parser.add_argument("--expected-version", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "validate-source":
            version = validate_source_version(args.tag, args.version_file)
            print(f"Release source version is valid: {version}")
        else:
            digest = verify_release_artifacts(args.directory, args.expected_version)
            print(f"Release artifacts are valid: sha256={digest}")
    except ReleaseContractError as exc:
        print(f"Release contract error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
