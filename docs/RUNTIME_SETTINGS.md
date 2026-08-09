# Typed runtime settings

Issue #3 is being introduced in stages. The first stage added typed settings models and tests without changing the runtime execution path. The next stage adds read-only option adapters that match existing workflow function keyword names.

## Current scope

`src/runtime_settings.py` reads the same resolved runtime config dictionary that existing code already uses. The merge order remains unchanged:

1. `shared` values from `assets/runtime_config.json`
2. command-specific values such as `craig_pipeline`

The typed models are an inspection and validation boundary. The option adapters are still read-only and do not change GUI or CLI behavior by themselves.

## Settings groups

The typed groups are:

- `TranscriptionSettings`
- `SubtitleLayoutSettings`
- `VideoExportSettings`
- `AudioNormalizeSettings`
- `SilenceCutSettings`
- `AlignmentSettings`

These groups make default drift easier to detect before behavior code is migrated.

## Option adapters

The first read-only adapters are:

- `transcribe_runtime_options(settings)`
  - returns the typed settings that map to `subtitle_workflow.transcribe_to_project` keyword arguments
- `render_runtime_options(settings)`
  - returns the typed settings that map to `subtitle_workflow.render_project_video` keyword arguments
- `configured_render_settings(settings, config)`
  - preserves the existing behavior where only explicitly configured render keys are written into project metadata

These adapters are intentionally separate from the GUI and CLI call sites. They allow the next migration PR to replace one caller without re-deciding key names or defaults.

## Migration policy

Do not replace many existing call sites in the same pull request that changes the settings model. The safe order is:

1. Add typed models and tests.
2. Add read-only adapters for existing config files.
3. Migrate one caller at a time.
4. Keep tests that compare typed settings against existing runtime config resolution.

This avoids changing command-line behavior, GUI startup behavior, or rendered output while the settings model is introduced.
