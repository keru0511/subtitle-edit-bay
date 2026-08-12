# Transcription dictionary support

This document summarizes the game-specific transcription dictionary path added for #1.

The goal is to improve WhisperX transcription for game commentary without requiring a local LLM. The supported path is intentionally deterministic: the creator provides or confirms game-specific context, the app normalizes it, and only confirmed dictionary terms are passed to transcription.

## Supported workflow

### GUI workflow

1. Open the app.
2. Add the video, Craig speaker audio files, and output directory.
3. In the transcription dictionary panel, enter the game title.
4. Add creator-confirmed terms such as character names, map names, weapon names, spell names, player names, or abbreviations.
5. Optionally enter notes that help describe the session.
6. Optionally set a manual dictionary JSON path.
7. Enable dictionary confirmation only after the dictionary content has been reviewed.
8. Start transcription.

The GUI stores the normalized transcription context in `.gui/runtime_config.json` under `craig_pipeline.transcription_context`. The GUI transcription command runs through `src.subtitle_workflow transcribe`, so the saved context is available to the context-aware workflow boundary.

### CLI workflow

`src.subtitle_workflow transcribe` accepts transcription context from runtime config or an explicit context file.

```powershell
.\.venv\Scripts\python.exe -m src.subtitle_workflow transcribe `
  --video recording.mkv `
  --audio-file alice.flac `
  --audio-file bob.flac `
  --output-dir video_export/session `
  --transcription-context-file transcription-context.json `
  --run
```

The context file can be either a raw `transcription_context` object or a runtime-config-shaped JSON file containing `transcription_context`.

```json
{
  "game_title": "Splatoon 3",
  "game_notes": "ranked session",
  "creator_terms": ["ナワバリバトル", "スプラシューター"],
  "dictionary_path": "dictionaries/splatoon.json",
  "dictionary_confirmed": true,
  "web_dictionary_enabled": false
}
```

## Manual dictionary JSON

A manual dictionary is optional. It is loaded only when the context points to it and `dictionary_confirmed` is true.

```json
{
  "terms": [
    {
      "term": "スプラシューター",
      "aliases": ["スシ"],
      "enabled": true,
      "type_hint": "weapon",
      "score": 1.0,
      "sources": [
        {
          "label": "creator",
          "url": null
        }
      ]
    }
  ]
}
```

Disabled terms and unconfirmed dictionaries are not passed to WhisperX.

## Cache behavior

Transcript cache reuse is fingerprint-aware when transcription hints are active. The fingerprint includes ASR settings and hint-related inputs such as prompt, hotwords, dictionary hash, and game context. Changing dictionary content, enabled terms, game title, model, language, or VAD settings changes the fingerprint and prevents stale transcript reuse.

Legacy transcript reuse remains available when no fingerprint is supplied, preserving existing behavior for workflows that do not use transcription hints.

## Non-goals in #1

The following work is intentionally not part of #1 close-out and is tracked in #48:

- Web-derived dictionary candidate extraction.
- Candidate review UI for web-derived terms.
- Network-backed candidate fetching.
- Automatically inferring the game title.

Web-derived terms must remain candidates, not trusted dictionary entries. They must not be passed to WhisperX until the creator confirms them.

## #1 close-out checklist

- [x] Transcription context model and project persistence exist.
- [x] Manual dictionary JSON loading exists.
- [x] Initial prompt and hotword generation exist.
- [x] Transcript cache fingerprint safety exists.
- [x] WhisperX command integration exists.
- [x] Craig and subtitle workflow routing support cache-aware hints.
- [x] Runtime config and CLI context resolution exist.
- [x] GUI can edit and persist transcription context.
- [x] GUI transcription runs through `subtitle_workflow transcribe`.
- [x] Web candidate extraction and candidate review UI are split to #48.
