# Transcription hints

This document covers the pure hint-building step for issue #1. The hint builder converts saved transcription context and a normalized manual dictionary into strings that can later be passed to WhisperX.

This step does not invoke WhisperX, does not change transcript cache behavior, and does not add GUI controls.

## Inputs

`build_transcription_hints()` accepts:

- `TranscriptionContext`
- Optional `TranscriptionDictionary`

The dictionary is used only when `TranscriptionContext.dictionary_confirmed` is `true`. This prevents unreviewed dictionary candidates from affecting ASR hints.

## Outputs

The builder returns `TranscriptionHints`:

- `initial_prompt`: Japanese context text for the ASR model.
- `hotwords`: unique ordered terms for later ASR hinting.

`hotwords` are built from:

1. `creator_terms`
2. confirmed enabled dictionary terms and aliases

Duplicates are removed while preserving the first occurrence. Disabled dictionary terms are ignored.

## Limits

The builder applies explicit limits before the hints reach any ASR command:

- maximum hotword count
- maximum hotword length
- maximum prompt length
- maximum number of dictionary terms included in the prompt text

These limits keep future WhisperX integration predictable and make cache fingerprints stable.

## Current scope

This stage intentionally stops before WhisperX integration. The next required step is dictionary-aware transcript cache fingerprinting. WhisperX command flags should be wired only after cache safety is in place.
