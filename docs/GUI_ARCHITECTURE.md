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
- `src/ui/Main.qml`
  - thin QML entrypoint
- `src/ui/screens/MainWorkflowScreen.qml`
  - current full workflow screen implementation
- `src/ui/components/PanelTitle.qml`
- `src/ui/components/SmallButton.qml`
- `src/ui/components/CompactSpinBox.qml`
- `src/ui/components/TimeField.qml`
  - standalone shared controls ready for gradual replacement inside workflow screens

`gui_state_base` still re-exports the source-selection and runtime-state symbols so existing imports from `src.gui_state` and `src.gui_base` continue to work.

`Main.qml` is now only an entrypoint. The current workflow screen remains behavior-compatible in `screens/MainWorkflowScreen.qml`, so later QML PRs can extract large pieces without also changing app bootstrapping.

The first shared controls now exist as standalone QML files and are covered by the static QML lint tests. Replacing the remaining inline component definitions in `MainWorkflowScreen.qml` should be done in a separate PR so behavior review is limited to usage replacement.

## Next QML steps

QML should be split after the Python source/runtime-state boundaries and top-level QML entrypoint are stable. Recommended order:

1. Replace common visual controls in `screens/MainWorkflowScreen.qml` with `components/*` controls.
2. Extract subtitle overlay and timeline components.
3. Extract editor and mixer screens.
4. Keep `Main.qml` focused on backend injection and entrypoint loading.

Each step should keep existing GUI behavior unchanged and rely on the Windows GUI smoke tests for regression coverage.
