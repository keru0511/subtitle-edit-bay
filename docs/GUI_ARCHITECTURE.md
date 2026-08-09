# GUI architecture split

Issue #2 is being handled in small compatibility-preserving steps. The first step is to separate source-selection state from the broader GUI state helpers without changing the public imports used by the existing backend.

## Current split

- `src/gui_source_state.py`
  - source file extension constants
  - one-shot source config keys
  - `SourceSelection`
  - speaker source entry generation
- `src/gui_state_base.py`
  - runtime config writing
  - GUI command construction
  - compatibility re-exports for existing callers

`gui_state_base` still re-exports the source-selection symbols so existing imports from `src.gui_state` and `src.gui_base` continue to work.

## Next QML steps

QML should be split after the Python source-state boundary is stable. Recommended order:

1. Extract common visual controls from `Main.qml`.
2. Extract subtitle overlay and timeline components.
3. Extract editor and mixer screens.
4. Keep `Main.qml` focused on top-level layout, screen switching, and backend injection.

Each step should keep existing GUI behavior unchanged and rely on the Windows GUI smoke tests for regression coverage.
