# Typed runtime settings

Issue #3 is being introduced in stages. The first stage added typed settings models and tests without changing the runtime execution path. Later stages added option adapters that match existing workflow function keyword names and migrated `subtitle_workflow.main()` to those adapters.

## Current scope

`src/runtime_settings.py` reads the same resolved runtime config dictionary that existing code already uses. The merge order remains unchanged:

1. `shared` values from `assets/runtime_config.json`
2. command-specific values such as `craig_pipeline`

The typed models are now used by `subtitle_workflow.main()` for the transcribe and render phases. The ASS phase still avoids resolving typed settings because it does not need runtime config values.

GUI runtime config generation now uses the same runtime settings boundary for the typed keys it persists. GUI-only or not-yet-typed values remain explicit raw handling until they receive typed models.

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

The first adapters are:

- `transcribe_runtime_options(settings)`
  - returns the typed settings that map to `subtitle_workflow.transcribe_to_project` keyword arguments
- `render_runtime_options(settings)`
  - returns the typed settings that map to `subtitle_workflow.render_project_video` keyword arguments
- `configured_render_settings(settings, config)`
  - preserves the existing behavior where only explicitly configured render keys are written into project metadata
- `gui_runtime_config_updates(settings)`
  - returns the shared and `craig_pipeline` updates that the GUI should write for typed runtime settings

`subtitle_workflow.main()` applies CLI-only overrides after reading the typed settings. This keeps explicit command-line values higher priority than `runtime_config.json` values.

`build_gui_runtime_config()` applies GUI-selected values through `gui_runtime_config_updates()`, then keeps `postprocess_workers` as an explicit raw value because it is not typed yet.

## Migration policy

Do not replace many existing call sites in the same pull request that changes the settings model. The safe order is:

1. Add typed models and tests.
2. Add read-only adapters for existing config files.
3. Migrate one caller at a time.
4. Keep tests that compare typed settings against existing runtime config resolution.

This avoids changing command-line behavior, GUI startup behavior, or rendered output while the settings model is introduced.
