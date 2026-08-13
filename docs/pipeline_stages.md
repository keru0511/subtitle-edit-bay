# Pipeline stage decomposition (Issue #4)

`craig_pipeline` and `subtitle_workflow` now expose explicit stage functions and typed stage results.

## New stage helpers

- `src/craig_pipeline.py`
  - `PipelineInputs` / `AlignmentResult` / `TranscriptionResult` / `SegmentRefinementResult` / `SegmentArtifacts` / `RenderResult`
  - `resolve_pipeline_inputs(...)`
  - `run_alignment_stage(...)`
  - `run_transcription_stage(...)`
  - `run_refine_stage(...)`
  - `write_segment_outputs(...)`
  - `run_render_stage(...)`

- `src/subtitle_workflow.py`
  - `SubtitlePipelineInputs` / `SubtitleAlignmentResult` / `SubtitleRefineResult`
  - `resolve_subtitle_inputs(...)`
  - `run_subtitle_alignment_stage(...)`
  - `run_subtitle_transcription_stage(...)`
  - `run_subtitle_refine_stage(...)`
  - `build_project_stage(...)`

## Compatibility behavior

Issue #4 preserves existing CLI and side effects:

- External commands, option names, defaults, and return values are unchanged.
- JSON/output file naming (`*.craig.merged.json`, `*.craig.filtered.json`, `*.craig.ass`, etc.) is unchanged.
- `subtitle_workflow.transcribe_to_project` still creates the same editable project structure and overwrite behavior.
- `craig_pipeline.run_craig_pipeline` still performs the same full path by default, but now through stage helpers.

## Why this matters

Each stage now has a typed, testable boundary and explicit side-effect boundary so failures are easier to locate and recovery can be resumed from known points without rewriting the overall pipeline.
