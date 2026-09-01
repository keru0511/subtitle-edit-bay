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

`requirements-dev.txt` also includes the YAML parser used by the GitHub Actions
contract tests. Any CI job that runs `tests/test_release_distribution.py` must
install both dependency files.

Run the same default quality checks as the local entrypoint:

```powershell
python scripts/check_quality.py
```

Run only Ruff lint checks:

```powershell
python scripts/check_quality.py --lint-only
```

Run Ruff lint checks for selected files or directories:

```powershell
python scripts/check_quality.py --lint-only --paths src tests
```

Run only the test suite:

```powershell
python scripts/check_quality.py --tests-only
```

Run the release workflow contract tests directly:

```powershell
python -m unittest tests.test_release_distribution -v
```

These tests parse the workflow and validate the job DAG, transitive test/build
gates, least-privilege publish permissions, and release step ordering. Mutation
tests confirm that missing dependencies, cycles, `continue-on-error`,
success-bypassing `if` conditions, implicit token permissions, and skipped
artifact verification, masked contract failures, or post-verification mutation
steps are rejected.

Run only Ruff format checks:

```powershell
python scripts/check_quality.py --format-only
```

Run Ruff format checks for selected files or directories:

```powershell
python scripts/check_quality.py --format-only --paths scripts/check_quality.py
```

Run only mypy type checks:

```powershell
python scripts/check_quality.py --type-only
```

Run mypy type checks for selected files or directories:

```powershell
python scripts/check_quality.py --type-only --paths scripts/check_quality.py
```

Run lint, type check, and tests together for selected files or directories:

```powershell
python scripts/check_quality.py --include-type-check --paths scripts/check_quality.py
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

The CI Python quality job uses the same script with `--lint-only`, runs a scoped format check against `scripts/check_quality.py`, and runs a scoped mypy check against `scripts/check_quality.py`. Windows CI keeps separate jobs for runtime-heavy smoke checks.

## Current Ruff scope

Ruff is currently configured to catch broken Python only:

- syntax errors
- undefined names
- severe Pyflakes control-flow errors

Ruff format is available through the shared quality entrypoint. CI currently enforces a scoped format check for the quality entrypoint itself, but repository-wide formatting is not enforced yet. Repository-wide format enforcement should be enabled in a separate formatting-only pull request to avoid mixing behavior work with large formatting diffs.

## Current type-check scope

mypy is available through the shared quality entrypoint. CI currently enforces a scoped mypy check for `scripts/check_quality.py` only. Repository-wide type checking is not enforced yet because the broader codebase still needs typed settings and domain model boundaries.

Import sorting and broad style rules are not enforced yet. They should be enabled in separate follow-up changes after the existing hot spots are cleaned up.

## Heavier Windows checks

The regular CI workflow also runs Windows smoke checks for GUI startup, FFmpeg media handling, Qt Multimedia playback, audio mixer operations, and installer startup.

WhisperX, PyTorch, and CUDA checks are intentionally kept in the manual Windows deep runtime workflow because they are slower and depend on runner capabilities.
