# Quality checks

This repository uses a staged quality gate. The first enforced gate is intentionally small so it can run on every pull request without requiring a broad style-only rewrite.

## Local commands

Install runtime dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install development-only tooling:

```powershell
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` also includes the YAML parser used by the GitHub Actions
contract tests. Any CI job that runs `tests/test_release_distribution.py` must
install both dependency files.

Run the same default quality checks as the local entrypoint:

```powershell
python scripts/check_quality.py
```

Run only Ruff lint checks:

```powershell
python scripts/check_quality.py --lint-only
```

Run Ruff lint checks for selected files or directories:

```powershell
python scripts/check_quality.py --lint-only --paths src tests
```

Run only the test suite:

```powershell
python scripts/check_quality.py --tests-only
```

Check that every `test_*.py` module is importable and collected by standard
unittest discovery:

```powershell
python scripts/check_unittest_discovery.py
```

The checker rejects module-level `test_*` functions, zero-test modules, and
import failures. It also fails when no test modules match or a custom
`load_tests` hook raises an error. Platform-specific skipped tests still count
as discovered.

## GUI test harness

GUI behavior tests share `tests/gui_test_harness.py`. The harness owns each QML
engine and window, provides bounded condition-based waits, finds controls by
`objectName`, performs mouse and keyboard interactions, checks visual bounds,
captures Qt/QML runtime messages for failure diagnostics, and reliably drains
deferred deletion during cleanup. `tests/edit_bay_gui_test_session.py` owns the
shared backend, creates a separate workspace for every test, and centralizes the
backend state and background-work reset.

Use `wait_until` for asynchronous UI state instead of fixed sleeps. Runtime QML
warnings from application-owned QML should fail the relevant behavior test.
Only allow a known message with `AllowedQmlMessage`, including a specific reason;
the harness deliberately ignores unrelated Qt backend noise when applying that
policy.

Large-project responsiveness uses deterministic contracts in the regular test
suite and a separate Windows Qt Multimedia benchmark. See
[`GUI_PERFORMANCE.md`](GUI_PERFORMANCE.md) for the eight scenarios, metrics,
baseline comparison, and local commands.

## QML test contracts

QML source assertions are limited to contracts that cannot be expressed more
reliably through a loaded UI. Keep `qmllint`, security prohibitions, exact copy
that is itself a product requirement, importability, public component existence,
and stable `objectName` values required by the GUI harness. Test user actions,
visibility, enabled state, layout bounds, backend state, and saved project state by
loading `Main.qml` through the GUI harness.

Do not assert internal binding expressions, component placement, fixed pixel
values, or the count and order of current layout elements. These details may
change without changing the product contract. When a static behavior assertion is
removed, identify its replacement behavior test or owning feature Issue in the
pull request.

Future UI behavior tests are owned by the feature that introduces the behavior:

| Behavior | Owning Issue |
|---|---|
| Codex sidebar authentication and persistence | #249 |
| Shared preview, edit modes, and playhead | #273 |
| Subtitle and volume modes | #274 |
| Cut mode and time mapping | #254 |
| Separate short-video workspace | #275 |
| Transcription and render actions | #276 |
| Project-first start screen | #277 |

Each feature pull request adds its behavior tests with the implementation. Do not
add permanently skipped tests or contracts for components that do not exist yet.

## Windows launcher test contracts

Test BAT and PowerShell launcher routing by starting the real entrypoint against a
temporary distribution tree. Assert the resolved install root, exclusive GUI or
repair child process, exit result, and diagnostic file instead of requiring C API
names or PowerShell function names to remain in source files. Windows-only
launcher behavior belongs to the required `windows-launcher-runtime` CI group;
Linux skips do not count as coverage. A missing required shell on Windows is a
test failure, not a skip.

Native launcher and installer artifact requirements belong to their release
gates. Product-EXE presence and removal of the PowerShell fallback are owned by
#257. PE subsystem, architecture, imports, resources, and signing inspection are
owned by #262. Do not preserve a future-obsolete fallback with a positive source
marker while those artifact contracts are pending.

## Windows updater test contracts

Test updater behavior by running the real BAT or PowerShell entrypoint against a
temporary distribution tree. Assert installer start or non-start, exit result,
installed version and files, preserved user data, recovery state, structured
result, and restart marker. Positive source markers for PowerShell commands,
function names, and log messages do not count as behavior coverage. Windows-only
updater behavior belongs to the required `windows-launcher-runtime` CI group;
Linux skips do not count as coverage. A missing required shell on Windows is a
test failure, not a skip.

Git checkout tests use a temporary bare repository to verify fast-forward-only
updates, tracked-change rejection, setup execution, and untracked-data
preservation. ZIP release tests use a loopback HTTP server and URL dependency
injection to exercise release metadata resolution and archive download without
external network access. The focused prohibition against `git reset --hard`
remains a source-level data-loss guard.

Installer-only parent process-tree and file-lock release, atomic
application/runtime rollback, old-version restart after rollback, and restart
executable re-resolution from the updated install root are owned by #260.
PowerShell restart fallback removal and hidden-window behavior are owned by #257,
and installer artifact inspection is owned by #262.

## Media semantic E2E contracts

Use `tests/media_test_utils.py` for deterministic, download-free lavfi fixtures,
bounded FFmpeg/FFprobe execution with process-tree termination on timeout, stream
probing, RGB frame extraction, and region-level pixel comparison. Keep this helper
outside the `test_*.py` discovery pattern. The owning test module belongs to
`ffmpeg-runtime`; explicitly selected representative cases are rerun by
`windows-ffmpeg-runtime`.

Subtitle burn-in tests compare a rendered output with a no-dialogue control at the
same timestamp. For the 30 fps fixture, timing checks use a one-frame allowance
plus 20 ms of mux tolerance and sample on both sides of the start and end
boundaries. Manual line breaks must appear as `\N` in ASS and increase the
vertical occupied pixel region; file existence and ASS text alone are not enough.

CPU fallback tests inject only the probed NVENC capability. They pass the codec
returned by the production automatic-selection boundary into the real render,
then inspect the output codec, pixel format, duration, faststart layout, and audio
stream. Do not mock the FFmpeg encode or treat a permanently GPU-less runner as
the fallback condition.

Audio mixer semantic tests use three steady, low-level stereo tones at 440 Hz,
880 Hz, and 1320 Hz. They save the complete channel state, invoke the production
project render, reload the project, and inspect the output audio. Mute and solo
checks use narrow frequency bands and require at least 15 dB of separation. Gain
checks compare 50%, 100%, and 200% outputs with the expected `20 * log10(gain)`
change within 2 dB while the fixture remains below the limiter range.

Normalize checks measure EBU R128 integrated loudness with FFmpeg `ebur128`, not
`volumedetect` mean volume. A six-second steady fixture provides a deterministic
window: discard the first second as warm-up and measure the following four
seconds. Compare the source with normalize-off output, then compare normalize-on
output with the configured LUFS target. Assertion diagnostics must include
measured frequency levels or integrated loudness, target and tolerance, the
analysis command, and FFmpeg stderr.

Run the release workflow contract tests directly:

```powershell
python -m unittest tests.test_release_distribution -v
```

These tests parse the workflow and validate the job DAG, transitive test/build
gates, least-privilege publish permissions, and release step ordering. Mutation
tests confirm that missing dependencies, cycles, `continue-on-error`,
success-bypassing `if` conditions, implicit token permissions, and skipped
artifact verification, masked contract failures, or post-verification mutation
steps are rejected.

Run only Ruff format checks:

```powershell
python scripts/check_quality.py --format-only
```

Run Ruff format checks for selected files or directories:

```powershell
python scripts/check_quality.py --format-only --paths scripts/check_quality.py
```

Run only mypy type checks:

```powershell
python scripts/check_quality.py --type-only
```

Run mypy type checks for selected files or directories:

```powershell
python scripts/check_quality.py --type-only --paths scripts/check_quality.py
```

Run lint, type check, and tests together for selected files or directories:

```powershell
python scripts/check_quality.py --include-type-check --paths scripts/check_quality.py
```

Run lint, format check, and tests together:

```powershell
python scripts/check_quality.py --include-format
```

Apply Ruff formatting locally:

```powershell
python scripts/check_quality.py --format-only --fix-format
```

Install dependencies and then run checks from a fresh environment:

```powershell
python scripts/check_quality.py --install-runtime --install-dev
```

The CI Python quality job uses the same script with `--lint-only`, runs a scoped format check against `scripts/check_quality.py`, and runs a scoped mypy check against `scripts/check_quality.py`. Windows CI keeps separate jobs for runtime-heavy smoke checks.

## Current Ruff scope

Ruff is currently configured to catch broken Python only:

- syntax errors
- undefined names
- severe Pyflakes control-flow errors

Ruff format is available through the shared quality entrypoint. CI currently enforces a scoped format check for the quality entrypoint itself, but repository-wide formatting is not enforced yet. Repository-wide format enforcement should be enabled in a separate formatting-only pull request to avoid mixing behavior work with large formatting diffs.

## Current type-check scope

mypy is available through the shared quality entrypoint. CI currently enforces a scoped mypy check for `scripts/check_quality.py` only. Repository-wide type checking is not enforced yet because the broader codebase still needs typed settings and domain model boundaries.

Import sorting and broad style rules are not enforced yet. They should be enabled in separate follow-up changes after the existing hot spots are cleaned up.

## Heavier Windows checks

The regular CI workflow also runs Windows smoke checks for GUI startup, FFmpeg media handling, Qt Multimedia playback, audio mixer operations, and installer startup.

WhisperX, PyTorch, and CUDA checks are intentionally kept in the manual Windows deep runtime workflow because they are slower and depend on runner capabilities.
