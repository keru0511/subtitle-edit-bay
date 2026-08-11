# Transcription hint workflow boundary

`src/transcription_hint_workflow.py` bridges saved project/workflow context to the lower-level Craig transcription hint plan.

It intentionally does not run WhisperX and does not read or write transcript JSON. Its responsibilities are limited to:

- normalize a saved `transcription_context` mapping;
- resolve a confirmed manual dictionary path;
- load the dictionary only when `dictionary_confirmed` is true;
- build a `CraigTranscriptionHintPlan` from context, dictionary, and ASR settings.

## Dictionary confirmation rule

Unconfirmed dictionaries are inert. A context with `dictionary_confirmed=false` does not resolve or load `dictionary_path`, even if the path is present.

That prevents web or candidate dictionaries from affecting:

- `initial_prompt`;
- `hotwords`;
- `dictionary_hash`;
- transcript cache fingerprints.

When `dictionary_confirmed=true` and `dictionary_path` is set, the file must exist. A missing confirmed dictionary fails fast so a lost or moved dictionary does not silently produce a different cache key.

## Next integration step

The next PR can call `build_craig_hint_plan_from_context()` at the workflow boundary, then pass `plan.hint` to the Craig transcription adapter.
