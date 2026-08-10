# Transcription dictionary rollout

This document covers the manual dictionary step for issue #1. The goal is to define a stable dictionary JSON boundary before prompt generation, cache fingerprinting, WhisperX command integration, Web candidate extraction, or GUI review UI are added.

## Manual dictionary JSON

A manual dictionary is a UTF-8 JSON object with a game title and a list of terms:

```json
{
  "game_title": "Splatoon 3",
  "terms": [
    {
      "term": "ナワバリバトル",
      "aliases": ["ナワバリ"],
      "type_hint": "mode",
      "enabled": true,
      "score": 1.0,
      "sources": []
    }
  ]
}
```

Fields:

- `game_title`: optional display/context title.
- `terms`: required array of dictionary term objects.
- `term`: required non-empty canonical term.
- `aliases`: optional array of alternate spellings or short names.
- `type_hint`: optional category such as `weapon`, `stage`, `character`, or `mode`.
- `enabled`: optional boolean. Missing values default to `true` for manual dictionaries.
- `score`: optional numeric score. Missing values default to `1.0`.
- `sources`: optional array of source metadata objects. Manual dictionaries usually leave this empty.

## Normalization policy

`src/transcription_dictionary.py` normalizes manual dictionaries before any later ASR hint logic sees them.

- Empty strings are dropped from aliases and source marker arrays.
- Duplicate aliases are removed while preserving order.
- Aliases equal to the canonical `term` are removed.
- Disabled terms are retained in the normalized dictionary but are excluded by `enabled_dictionary_terms()`.
- Invalid root, term, alias, score, enabled flag, or source shapes raise `TranscriptionDictionaryError`.

## Current scope

This step does not pass dictionary terms to WhisperX. It only fixes the JSON schema and normalization behavior.

Follow-up steps:

1. Build `initial_prompt` and `hotwords` from `transcription_context` and enabled dictionary terms.
2. Add dictionary-aware transcript cache fingerprinting before passing hints to WhisperX.
3. Pass hints to WhisperX only after cache safety is in place.
4. Add heuristic Web candidate providers and GUI review after the manual path is stable.
