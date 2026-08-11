# Craig transcription execution adapter

This document describes the Craig-specific adapter added for Issue #1.

## Purpose

`src/transcription_execution.py` owns the low-level WhisperX execution and optional fingerprint-aware cache validation. Craig transcription still needs a narrow boundary that can pass per-audio prompt, hotwords, and cache metadata without expanding the main pipeline all at once.

`src/craig_transcription_execution.py` provides that boundary.

## File adapter

`CraigTranscriptionHint` contains optional data for one Craig speaker audio file:

```python
CraigTranscriptionHint(
    initial_prompt="Game title: ...",
    hotwords=("...",),
    cache_fingerprint="...",
    cache_settings={...},
)
```

`transcribe_craig_audio_file_with_cache()` forwards this data to `transcribe_audio_with_cache()`.

When no hint is supplied, the adapter passes no fingerprint and therefore preserves legacy path-exists cache reuse.

## Batch adapter

`transcribe_craig_audio_batch_with_cache()` runs the same adapter for an ordered batch of Craig speaker audio files.

It returns:

- `transcript_map`, preserving the existing Craig pipeline shape of absolute audio path to absolute transcript JSON path;
- ordered per-file results with `cache_hit` and optional metadata sidecar path.

This lets the main Craig pipeline switch from direct `transcribe_audio_file()` calls to a cache-aware batch call without duplicating hint resolution or fingerprint validation logic.

## Hint lookup

`resolve_craig_transcription_hint()` can resolve hints by:

- original path string
- absolute path string
- file name

If a per-file mapping does not match, callers may pass a `default_hint`, which is useful when all Craig speaker tracks should share one game-level prompt/hotword/fingerprint plan.

## Next step

The next PR should replace the transcription loop inside `craig_pipeline.transcribe_craig_audio_files()` with `transcribe_craig_audio_batch_with_cache()`, then keep the CPU subtitle segment post-processing exactly where it is.

That PR should still keep default behavior unchanged when no hint/fingerprint is supplied.
