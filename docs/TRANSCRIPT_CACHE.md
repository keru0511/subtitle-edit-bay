# Transcript cache fingerprinting

This document covers the cache safety step for issue #1.

The current helper layer lives in `src/transcript_cache.py`. It defines deterministic fingerprint and sidecar metadata utilities, but it does not yet change the existing WhisperX pipeline behavior. Pipeline integration should happen in the next PR, after the fingerprint contract is stable.

## Fingerprint inputs

`build_transcript_cache_fingerprint()` includes:

- model
- device
- compute type
- language
- VAD onset / offset
- initial prompt hash
- hotwords hash
- dictionary hash
- game title
- optional WhisperX version

Changing any of these values changes the fingerprint and should cause a cache miss once the pipeline passes an expected fingerprint.

## Metadata sidecar

Transcript cache metadata is stored beside the transcript JSON:

```text
1-alice.json
1-alice.json.cache.json
```

The metadata contains:

```json
{
  "schema_version": 1,
  "fingerprint": "...",
  "settings": {}
}
```

## Compatibility policy

`transcript_cache_is_valid(path)` without an expected fingerprint keeps the existing legacy behavior: an existing transcript JSON is reusable.

`transcript_cache_is_valid(path, expected_fingerprint="...")` is stricter:

- missing transcript: cache miss
- missing metadata: cache miss
- invalid metadata: cache miss
- mismatched fingerprint: cache miss
- matching fingerprint: cache hit

This lets the next PR opt in to dictionary-aware cache safety without silently breaking current no-hint transcript reuse.
