# Transcription context rollout

Issue #1 adds game-specific transcription hints without requiring a local LLM. The implementation is intentionally staged so the GUI, dictionary, ASR hinting, and transcript cache behavior do not become coupled in one large change.

## Current boundary

`transcription_context` is project metadata that records creator-provided game context before any dictionary or WhisperX integration is enabled.

```json
{
  "transcription_context": {
    "game_title": "",
    "game_notes": "",
    "creator_terms": [],
    "dictionary_path": null,
    "dictionary_confirmed": false,
    "web_dictionary_enabled": false
  }
}
```

The first implementation step only defines and persists this shape. It does not pass anything to WhisperX yet and does not fetch Web terms.

## Field ownership

- `game_title`: creator-entered title. This is the source of truth for later dictionary generation.
- `game_notes`: optional DLC, map, mod, event, or ruleset notes.
- `creator_terms`: creator-entered names, nicknames, phrases, and other important words.
- `dictionary_path`: optional path to a later manual or generated dictionary JSON.
- `dictionary_confirmed`: whether the creator has reviewed the dictionary candidate list.
- `web_dictionary_enabled`: whether Web candidate generation is enabled for this project.

## Staged implementation order

1. Persist `transcription_context` in project JSON.
2. Add manual dictionary JSON loading.
3. Build `initial_prompt` and `hotwords` from context and enabled dictionary terms.
4. Add dictionary-aware transcript cache fingerprinting.
5. Pass prompt and hotwords to WhisperX.
6. Add heuristic Web dictionary candidate extraction without GUI auto-apply.
7. Add GUI review and confirmation flow.
8. Document the final workflow and close Issue #1.

This order prevents a common failure mode where UI settings appear saved but do not affect ASR, or where changed dictionaries still reuse stale transcripts.
