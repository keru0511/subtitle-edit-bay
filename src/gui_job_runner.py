from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .processing_progress import parse_progress_events
from .qprocess_launcher import prepare_qprocess_launch


class GuiJobRunner(QObject):
    """Own the GUI QProcess lifecycle while keeping backend state as a facade.

    The runner owns process startup, channel draining, machine-event forwarding,
    cancellation identity checks, terminal classification, and shutdown.  The
    backend remains responsible for domain-specific status text and completion
    integration, so this boundary does not alter job semantics or the public
    QML contract.
    """

    started = Signal()
    outputReceived = Signal(str)
    machineProgress = Signal(object)
    finished = Signal(int, object)
    errorOccurred = Signal(object)
    terminal = Signal(str, int, str)
    launchPreparationFailed = Signal(str)

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        parent: QObject | None = None,
        process: QProcess | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace_root = Path(workspace_root).resolve()
        self._process = process or QProcess(self)
        if process is not None and self._process.parent() is None:
            self._process.setParent(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_process_output)
        self._process.started.connect(self._on_started)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._sequence = 0
        self._active_job_id = ""
        self._last_job_id = ""
        self._cancel_requested = False
        self._cancel_process_id = 0
        self._shutdown = False

    @property
    def process(self) -> QProcess:
        """Expose the process for the legacy backend compatibility surface."""

        return self._process

    @property
    def active_job_id(self) -> str:
        return self._active_job_id

    @property
    def last_job_id(self) -> str:
        return self._last_job_id

    @property
    def running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    @property
    def process_id(self) -> int:
        return int(self._process.processId())

    def start(self, command: list[str], *, job_id: str = "") -> bool:
        if self._shutdown or not command or self.running:
            return False
        self._sequence += 1
        self._active_job_id = str(job_id or f"job-{self._sequence}")
        self._cancel_requested = False
        self._cancel_process_id = 0

        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("PYTHONUNBUFFERED", "1")
        self._process.setProcessEnvironment(environment)

        try:
            launch = prepare_qprocess_launch(command, self.workspace_root)
        except OSError as error:
            self.launchPreparationFailed.emit(str(error))
            self._process.setWorkingDirectory(str(self.workspace_root))
            self._process.start(command[0], command[1:])
            return True

        self._process.setWorkingDirectory(launch.working_directory)
        self._process.start(launch.program, list(launch.arguments))
        return True

    def cancel(self, *, job_id: str | None = None) -> bool:
        if not self.running:
            return False
        if job_id is not None and str(job_id) != self._active_job_id:
            return False
        self._cancel_requested = True
        expected_process_id = self.process_id
        expected_job_id = self._active_job_id
        self._cancel_process_id = expected_process_id
        if os.name == "nt" and expected_process_id:
            subprocess.run(
                ["taskkill", "/PID", str(expected_process_id), "/T"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            self._process.terminate()
        QTimer.singleShot(
            5000,
            lambda process_id=expected_process_id, active_job_id=expected_job_id: self.kill_if_running(
                process_id,
                job_id=active_job_id,
            ),
        )
        return True

    def kill_if_running(
        self,
        expected_process_id: int = 0,
        *,
        job_id: str | None = None,
    ) -> bool:
        if job_id is not None and str(job_id) != self._active_job_id:
            return False
        current_process_id = self.process_id
        if expected_process_id and current_process_id != expected_process_id:
            return False
        if not self.running:
            return False
        if os.name == "nt" and current_process_id:
            subprocess.run(
                ["taskkill", "/PID", str(current_process_id), "/T", "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            self._process.kill()
        return True

    def _read_process_output(self) -> None:
        data = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8",
            errors="replace",
        )
        if not data:
            return
        self.outputReceived.emit(data)
        for event in parse_progress_events(data):
            self.machineProgress.emit(event)

    def _on_started(self) -> None:
        self.started.emit()

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self.errorOccurred.emit(error)
        if (
            self._process.state() == QProcess.ProcessState.NotRunning
            and not self._cancel_requested
        ):
            job_id = self._active_job_id
            if job_id:
                self._last_job_id = job_id
                self.terminal.emit("error", -1, job_id)
                self._active_job_id = ""
                self._cancel_requested = False
                self._cancel_process_id = 0

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_process_output()
        job_id = self._active_job_id
        if not job_id:
            return
        outcome = (
            "canceled"
            if self._cancel_requested
            else "completed"
            if exit_code == 0
            else "error"
        )
        self._last_job_id = job_id
        self.finished.emit(exit_code, exit_status)
        self.terminal.emit(outcome, int(exit_code), job_id)
        self._active_job_id = ""
        self._cancel_requested = False
        self._cancel_process_id = 0

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        if self.running:
            self._cancel_requested = True
            self._process.kill()
            self._process.waitForFinished(1_000)
        self._cancel_process_id = 0
        self._active_job_id = ""
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(1_000)


__all__ = ["GuiJobRunner"]
