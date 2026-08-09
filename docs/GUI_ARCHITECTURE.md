# GUI architecture split

Issue #2 is being handled in small compatibility-preserving steps. The first steps separate focused Python GUI helpers from the broad `gui_state_base.py` compatibility surface without changing the public imports used by the existing backend.

## Current split

- `src/gui_source_state.py`
  - source file extension constants
  - one-shot source config keys
  - `SourceSelection`
  - speaker source entry generation
- `src/gui_runtime_state.py`
  - GUI runtime config writing
  - GUI pipeline command construction
- `src/gui_state_base.py`
  - compatibility re-exports for existing callers

`gui_state_base` still re-exports the source-selection and runtime-state symbols so existing imports from `src.gui_state` and `src.gui_base` continue to work.

## Next QML steps

QML should be split after the Python source/runtime-state boundaries are stable. Recommended order:

1. Extract common visual controls from `Main.qml`.
2. Extract subtitle overlay and timeline components.
3. Extract editor and mixer screens.
4. Keep `Main.qml` focused on top-level layout, screen switching, and backend injection.

Each step should keep existing GUI behavior unchanged and rely on the Windows GUI smoke tests for regression coverage.
