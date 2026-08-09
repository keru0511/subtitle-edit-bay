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

Run the test suite:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Run Ruff lint checks:

```powershell
python -m ruff check .
```

## Current Ruff scope

Ruff is currently configured to catch broken Python only:

- syntax errors
- undefined names
- severe Pyflakes control-flow errors

Formatting, import sorting, broad style rules, and type checking are not enforced yet. They should be enabled in separate follow-up changes to avoid mixing behavior work with large formatting diffs.

## Heavier Windows checks

The regular CI workflow also runs Windows smoke checks for GUI startup, FFmpeg media handling, Qt Multimedia playback, audio mixer operations, and installer startup.

WhisperX, PyTorch, and CUDA checks are intentionally kept in the manual Windows deep runtime workflow because they are slower and depend on runner capabilities.
