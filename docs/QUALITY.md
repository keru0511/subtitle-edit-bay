# Quality checks

This repository uses a staged quality gate. The first enforced gate is intentionally small so it can run on every pull request without requiring a broad style-only rewrite.

## Local commands

Install runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install development-only tooling:

```powershell
python -m pip install -r requirements-dev.txt
```

Run the same default quality checks as the local entrypoint:

```powershell
python scripts/check_quality.py
```

Run only Ruff lint checks:

```powershell
python scripts/check_quality.py --lint-only
```

Run only the test suite:

```powershell
python scripts/check_quality.py --tests-only
```

Run only Ruff format checks:

```powershell
python scripts/check_quality.py --format-only
```

Run lint, format check, and tests together:

```powershell
python scripts/check_quality.py --include-format
```

Apply Ruff formatting locally:

```powershell
python scripts/check_quality.py --format-only --fix-format
```

Install dependencies and then run checks from a fresh environment:

```powershell
python scripts/check_quality.py --install-runtime --install-dev
```

The CI Ruff job uses the same script with `--lint-only`; Windows CI keeps separate jobs for runtime-heavy smoke checks.

## Current Ruff scope

Ruff is currently configured to catch broken Python only:

- syntax errors
- undefined names
- severe Pyflakes control-flow errors

Ruff format is available through the shared quality entrypoint, but formatting is not enforced by the regular CI job yet. Format enforcement should be enabled in a separate formatting-only pull request to avoid mixing behavior work with large formatting diffs.

Import sorting, broad style rules, and type checking are not enforced yet. They should be enabled in separate follow-up changes after the existing hot spots are cleaned up.

## Heavier Windows checks

The regular CI workflow also runs Windows smoke checks for GUI startup, FFmpeg media handling, Qt Multimedia playback, audio mixer operations, and installer startup.

WhisperX, PyTorch, and CUDA checks are intentionally kept in the manual Windows deep runtime workflow because they are slower and depend on runner capabilities.
