# Transcription hint plan

This document describes the bridge between saved transcription context,
manual dictionaries, ASR settings, Craig execution hints, and transcript cache
fingerprints.

## Boundary

`src/transcription_hint_plan.py` does not run WhisperX and does not edit project
JSON. It only builds a stable plan that later pipeline code can pass into the
Craig transcription execution adapter.

Inputs:

- `TranscriptionContext`
- optional `TranscriptionDictionary`
- `TranscriptionAsrSettings`

Outputs:

- `CraigTranscriptionHint`
- generated `initial_prompt`
- generated `hotwords`
- deterministic cache fingerprint
- cache metadata settings

## Dictionary confirmation rule

Manual or Web-derived dictionary entries must not affect transcription unless
`transcription_context.dictionary_confirmed` is true.

When the dictionary is not confirmed:

- dictionary terms are not added to `initial_prompt`
- dictionary terms are not added to `hotwords`
- dictionary hash is empty
- dictionary changes do not invalidate transcript cache

When the dictionary is confirmed:

- enabled dictionary terms and aliases can be used as ASR hints
- dictionary JSON contributes to the cache fingerprint
- dictionary changes invalidate transcript cache when the plan is used

## Cache fingerprint inputs

The plan fingerprint includes:

- model
- device
- compute type
- language
- VAD onset / offset
- initial prompt hash
- hotwords hash
- confirmed dictionary hash
- game title
- optional WhisperX version

This keeps cache invalidation deterministic before the pipeline starts passing
hints to WhisperX.

## Next step

The next wiring step is to build this plan at the workflow boundary and pass its
`CraigTranscriptionHint` into the Craig transcription execution adapter.
