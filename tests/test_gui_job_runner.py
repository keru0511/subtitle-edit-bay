from __future__ import annotations

import os
import sys
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject

from src.gui_job_runner import GuiJobRunner
from tests.typed_case import TypedTestCase


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class RunnerProbe(QObject):
    def __init__(self, runner: GuiJobRunner) -> None:
        super().__init__(runner)
        self.outputs: list[str] = []
        self.progress: list[dict[str, object]] = []
        self.finished: list[tuple[int, object]] = []
        self.errors: list[object] = []
        self.terminals: list[tuple[str, int, str]] = []
        runner.outputReceived.connect(self.outputs.append)
        runner.machineProgress.connect(self.progress.append)
        runner.finished.connect(lambda code, status: self.finished.append((int(code), status)))
        runner.errorOccurred.connect(self.errors.append)
        runner.terminal.connect(
            lambda outcome, code, job_id: self.terminals.append(
                (str(outcome), int(code), str(job_id))
            )
        )


def _application() -> QCoreApplication:
    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication(["subtitle-edit-bay-gui-job-runner"])
    return application


def _wait_for(application: QCoreApplication, predicate: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt event loop wait timed out")


def _python_process(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class GuiJobRunnerTests(TypedTestCase):
    def test_start_success_collects_stdout_stderr_and_machine_progress(self) -> None:
        application = _application()
        runner = GuiJobRunner(Path.cwd())
        self.addCleanup(runner.shutdown)
        probe = RunnerProbe(runner)

        self.assertTrue(
            runner.start(
                _python_process(
                    "import json, sys; "
                    "print('stdout-line', flush=True); "
                    "print('PROGRESS_EVENT ' + json.dumps({"
                    "'job': 'render', 'step': 'encode', "
                    "'phase': 'progress', 'progress': 0.5"
                    "}), flush=True); "
                    "print('stderr-line', file=sys.stderr, flush=True)"
                ),
                job_id="render",
            )
        )
        _wait_for(application, lambda: bool(probe.finished))
        self.assertEqual(probe.terminals[-1][0], "completed")
        self.assertIn("stdout-line", "".join(probe.outputs))
        self.assertIn("stderr-line", "".join(probe.outputs))
        self.assertEqual(probe.progress[-1]["progress"], 0.5)

    def test_start_failure_emits_terminal_error(self) -> None:
        application = _application()
        runner = GuiJobRunner(Path.cwd())
        self.addCleanup(runner.shutdown)
        probe = RunnerProbe(runner)

        missing = str(Path.cwd() / "missing-gui-job-runner-executable")
        self.assertTrue(runner.start([missing], job_id="missing"))
        _wait_for(application, lambda: bool(probe.terminals))
        self.assertEqual(probe.terminals[-1], ("error", -1, "missing"))
        self.assertTrue(probe.errors)

    def test_cancel_rejects_stale_job_identity_and_reports_canceled(self) -> None:
        application = _application()
        runner = GuiJobRunner(Path.cwd())
        self.addCleanup(runner.shutdown)
        probe = RunnerProbe(runner)

        self.assertTrue(
            runner.start(
                _python_process("import time; time.sleep(10)"),
                job_id="first",
            )
        )
        _wait_for(application, lambda: runner.running)
        self.assertFalse(runner.cancel(job_id="stale"))
        self.assertTrue(runner.running)
        self.assertTrue(runner.cancel(job_id="first"))
        _wait_for(application, lambda: bool(probe.terminals))
        self.assertEqual(probe.terminals[-1][0], "canceled")

    def test_cancel_force_kill_timer_cannot_kill_the_next_job(self) -> None:
        application = _application()
        runner = GuiJobRunner(Path.cwd())
        self.addCleanup(runner.shutdown)
        probe = RunnerProbe(runner)
        scheduled_callbacks: list[tuple[int, Callable[[], bool]]] = []

        with patch(
            "src.gui_job_runner.QTimer.singleShot",
            side_effect=lambda interval, callback: scheduled_callbacks.append(
                (int(interval), callback)
            ),
        ):
            self.assertTrue(
                runner.start(
                    _python_process("import time; time.sleep(10)"),
                    job_id="canceled-job",
                )
            )
            _wait_for(application, lambda: runner.running)
            self.assertTrue(runner.cancel(job_id="canceled-job"))
            _wait_for(application, lambda: bool(probe.terminals))

        self.assertEqual(probe.terminals[-1][0], "canceled")
        self.assertEqual(scheduled_callbacks[0][0], 5000)
        self.assertTrue(
            runner.start(
                _python_process("import time; time.sleep(10)"),
                job_id="next-job",
            )
        )
        _wait_for(application, lambda: runner.running)

        old_force_kill = scheduled_callbacks[0][1]
        self.assertFalse(old_force_kill())
        application.processEvents()

        self.assertTrue(runner.running)
        self.assertEqual(runner.active_job_id, "next-job")

    def test_terminal_completed_and_shutdown_cleanup_are_idempotent(self) -> None:
        application = _application()
        runner = GuiJobRunner(Path.cwd())
        self.assertTrue(runner.start(_python_process("import time; time.sleep(10)"), job_id="cleanup"))
        _wait_for(application, lambda: runner.running)
        runner.shutdown()
        runner.shutdown()
        self.assertFalse(runner.running)
        self.assertEqual(runner.active_job_id, "")


if __name__ == "__main__":
    unittest.main()
