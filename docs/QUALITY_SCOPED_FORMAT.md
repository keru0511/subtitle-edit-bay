# Scoped Ruff format rollout

This note exists to keep the staged format rollout explicit.

The repository is not ready for repository-wide `ruff format --check` enforcement yet, because that would create a large mechanical diff. Instead, the CI quality job starts by checking the shared quality entrypoint itself:

```powershell
python scripts/check_quality.py --format-only --paths scripts/check_quality.py
```

This gives CI coverage for the format-check path without requiring a full-repository formatting change. A later formatting-only pull request can run `python scripts/check_quality.py --format-only --fix-format` for the whole repository and then switch CI to `--include-format`.
