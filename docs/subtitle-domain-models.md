# Subtitle Domain Models

This document summarizes the explicit project-facing models introduced in `subtitle_project.py`.

## Models

- `SubtitleSegment`
- `SpeakerInfo`
- `WaveformInfo`
- `AudioMixChannel`
- `AudioMix`
- `SubtitleProject`

These models are introduced to keep payload contracts explicit while preserving the existing
dict-based project JSON shape for compatibility.

## Migration boundary

`migrate_project_payload` keeps the payload in a forward-compatible shape and normalizes
missing required metadata before converting to typed models.

## Conversion path

1. Incoming payload is normalized in `validate_project`.
2. The normalized payload is parsed through typed models.
3. Internal processing uses `SubtitleProject` / `SubtitleSegment`; `to_json()` is called only by `save_project_model()` or the legacy compatibility boundary.
4. QML receives `project_to_view_payload()` output rather than persistence dictionaries.

## Extending segment fields

To add a new segment field:

1. Add the attribute to `SubtitleSegment`.
2. Extend `normalize_segment` and any defaulting logic as needed.
3. Update `to_json` roundtrip if custom behavior is required.
4. Add/update tests in `tests/test_subtitle_project.py`.

