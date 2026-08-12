# Transcription execution boundary

#1 is adding game-specific transcription hints in stages. This document covers the low-level execution boundary that combines WhisperX command construction with optional transcript-cache fingerprint checks.

## `transcribe_audio_with_cache`

`src.transcription_execution.transcribe_audio_with_cache()` is the cache-aware runner for a single audio file.

It keeps two cache modes:

1. Legacy cache mode
   - Used when no `cache_fingerprint` is supplied.
   - Existing transcript JSON is reused when it exists and `skip_existing=True`.
   - This preserves pre-dictionary behavior for existing callers.

2. Fingerprint-aware cache mode
   - Used when `cache_fingerprint` is supplied.
   - Existing transcript JSON is reused only when the sidecar metadata fingerprint matches.
   - Missing, invalid, or mismatched metadata is treated as a cache miss.
   - After a successful run, matching metadata is written next to the transcript JSON.

## Hint handling

The runner accepts `initial_prompt` and `hotwords` and passes them to `build_whisperx_command()` only when it actually runs WhisperX. Cache hits do not rebuild or execute the command.

## Current scope

This is still below the project/pipeline layer. The next step is to route existing `craig_pipeline` and `subtitle_workflow` transcription calls through this runner and to pass fingerprints generated from `transcription_context`, dictionary terms, ASR settings, and generated hints.
