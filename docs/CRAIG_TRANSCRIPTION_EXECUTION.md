# Craig transcription execution adapter

This document describes the Craig-specific adapter added for Issue #1.

## Purpose

`src/transcription_execution.py` owns the low-level WhisperX execution and optional fingerprint-aware cache validation. Craig transcription still needs a narrow boundary that can pass per-audio prompt, hotwords, and cache metadata without expanding the main pipeline all at once.

`src/craig_transcription_execution.py` provides that boundary.

## Current adapter

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

## Hint lookup

`resolve_craig_transcription_hint()` can resolve hints by:

- original path string
- absolute path string
- file name

This lets the pipeline build hints either globally or per selected Craig audio file.

## Next step

The next PR should replace `craig_pipeline.transcribe_audio_file()` internals with this adapter while keeping its existing public function signature compatible. It can then add optional keyword-only hint/fingerprint inputs to `transcribe_craig_audio_files()`.

That PR should still keep default behavior unchanged when no hint/fingerprint is supplied.
