# Typed runtime settings

Issue #3 was introduced in stages. The first stage added typed settings models and tests without changing the runtime execution path. Later stages added option adapters, migrated `subtitle_workflow.main()`, and moved GUI runtime config generation to the typed settings boundary.

## Current scope

`src/runtime_settings.py` reads the same resolved runtime config dictionary that existing code already uses. The merge order remains unchanged:

1. `shared` values from `assets/runtime_config.json`
2. command-specific values such as `craig_pipeline`

The typed models are used by `subtitle_workflow.main()` for the transcribe and render phases. The ASS phase still avoids resolving typed settings because it does not need runtime config values.

GUI runtime config generation uses the same runtime settings boundary for the typed keys it persists. One-shot source fields such as `video`, `audio_file`, `output_dir`, `reference_audio`, and `reference_track` are still stripped before writing the GUI runtime config.

## Settings groups

The typed groups are:

- `TranscriptionSettings`
- `SubtitleLayoutSettings`
- `VideoExportSettings`
- `AudioNormalizeSettings`
- `SilenceCutSettings`
- `AlignmentSettings`
- `PipelineSettings`

These groups make default drift easier to detect. `PipelineSettings` currently contains `postprocess_workers`, which is shared by CLI workflow execution and GUI runtime config generation.

## Option adapters

The runtime settings adapters are:

- `transcribe_runtime_options(settings)`
  - returns the typed settings that map to `subtitle_workflow.transcribe_to_project` keyword arguments
- `render_runtime_options(settings)`
  - returns the typed settings that map to `subtitle_workflow.render_project_video` keyword arguments
- `configured_render_settings(settings, config)`
  - preserves the existing behavior where only explicitly configured render keys are written into project metadata
- `gui_runtime_config_updates(settings)`
  - returns the shared and `craig_pipeline` updates that the GUI should write for typed runtime settings

`subtitle_workflow.main()` applies CLI-only overrides after reading the typed settings. This keeps explicit command-line values higher priority than `runtime_config.json` values.

`build_gui_runtime_config()` applies GUI-selected values through `gui_runtime_config_updates()` and keeps transient source selection values out of the persisted runtime config.

## Close-out state for Issue #3

The settings consolidation is sufficient for Issue #3 when these invariants hold:

- `runtime_config.json` still loads through the existing shared plus command-specific merge behavior.
- `RuntimeSettings` covers transcription, subtitle layout, video export, audio normalization, silence cut, alignment, and postprocess worker settings.
- `subtitle_workflow.main()` uses typed settings adapters for transcribe and render runtime options.
- GUI runtime config writing routes typed settings through `gui_runtime_config_updates()`.
- Tests cover runtime config conversion, CLI override precedence, GUI runtime config persistence, invalid type handling, and default fallback behavior.
- Existing command-line and GUI startup behavior remains backward compatible.

Remaining `DEFAULT_*` constants in pipeline modules are compatibility defaults for function signatures or lower-level modules. They should only be removed after each lower-level API receives its own typed boundary and parity tests.

## Migration policy for future settings

Do not replace many existing call sites in the same pull request that changes the settings model. The safe order is:

1. Add typed models and tests.
2. Add read-only adapters for existing config files.
3. Migrate one caller at a time.
4. Keep tests that compare typed settings against existing runtime config resolution.

This avoids changing command-line behavior, GUI startup behavior, or rendered output while the settings model is extended.
