# Typed runtime settings

Issue #3 is being introduced in stages. This first stage adds typed settings models and tests without changing the runtime execution path.

## Current scope

`src/runtime_settings.py` reads the same resolved runtime config dictionary that existing code already uses. The merge order remains unchanged:

1. `shared` values from `assets/runtime_config.json`
2. command-specific values such as `craig_pipeline`

The typed models are currently an inspection and validation boundary. Existing GUI and CLI command construction still use the existing runtime config helpers.

## Settings groups

The first typed groups are:

- `TranscriptionSettings`
- `SubtitleLayoutSettings`
- `VideoExportSettings`
- `AudioNormalizeSettings`
- `SilenceCutSettings`
- `AlignmentSettings`

These groups make default drift easier to detect before behavior code is migrated.

## Migration policy

Do not replace existing call sites in the same pull request that introduces the models. The safe order is:

1. Add typed models and tests.
2. Add read-only adapters for existing config files.
3. Migrate one caller at a time.
4. Keep tests that compare typed settings against existing runtime config resolution.

This avoids changing command-line behavior, GUI startup behavior, or rendered output while the settings model is introduced.
