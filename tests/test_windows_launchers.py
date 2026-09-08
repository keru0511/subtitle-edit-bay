import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


ROOT = Path(__file__).resolve().parent.parent


class WindowsLauncherTests(unittest.TestCase):
    def test_gui_initializes_typing_extensions_before_pyside(self) -> None:
        gui = (ROOT / "src" / "gui.py").read_text(encoding="utf-8")

        self.assertLess(gui.index("from typing_extensions import Self"), gui.index("from PySide6.QtCore import"))

    def _require_windows_git(self) -> str:
        executable = shutil.which("git.exe")
        if executable:
            return str(Path(executable).resolve())
        self.fail("Git for Windows is required")

    def _require_windows_powershell(self) -> str:
        candidates: list[Path] = []
        system_root = os.environ.get("SystemRoot")
        if system_root:
            candidates.extend(
                [
                    Path(system_root) / "Sysnative" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
                    Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
                ]
            )
        path_executable = shutil.which("powershell.exe")
        if path_executable:
            candidates.append(Path(path_executable))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        self.fail("Windows PowerShell is required")

    def _seed_installer_distribution(self, install: Path, restart_marker: Path) -> None:
        (install / "scripts").mkdir(parents=True)
        (install / "src").mkdir()
        (install / ".gui").mkdir()
        (install / ".venv").mkdir()
        (install / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
        escaped_marker = str(restart_marker).replace("'", "''")
        (install / "scripts" / "launch.ps1").write_text(
            f"[IO.File]::WriteAllText('{escaped_marker}', 'started') # old launcher\n",
            encoding="utf-8",
        )
        (install / "scripts" / "setup.ps1").write_text(
            "New-Item -ItemType Directory -Path '.venv' -Force | Out-Null\n"
            "[IO.File]::WriteAllText('.venv\\new-runtime.txt', 'new runtime')\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (install / "scripts" / "validate_runtime.ps1").write_text("exit 0\n", encoding="utf-8")
        (install / ".venv" / "old-runtime.txt").write_text("old runtime", encoding="utf-8")
        (install / "src" / "app.py").write_text("old app", encoding="utf-8")
        (install / ".gui" / "runtime_config.json").write_text("old gui", encoding="utf-8")

    def _write_fake_installer(self, base: Path, powershell: str, script_body: str) -> Path:
        (base / "fake-installer.ps1").write_text(script_body, encoding="utf-8")
        installer = base / "fake-installer.cmd"
        installer.write_text(
            "@echo off\r\n"
            f'"{powershell}" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass '
            '-File "%~dp0fake-installer.ps1" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
        return installer

    def _run_installer_update(
        self,
        *,
        powershell: str,
        package: Path,
        install: Path,
        result_path: Path,
        parent_pid: int = -1,
        expected_version: str = "v9.9.9",
        expected_sha256: str | None = None,
        test_fault_point: str | None = None,
        test_fault_marker: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        digest = expected_sha256 or hashlib.sha256(package.read_bytes()).hexdigest()
        arguments = [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "apply_installer_update.ps1"),
            "-PackagePath",
            str(package),
            "-ParentPid",
            str(parent_pid),
            "-InstallRoot",
            str(install),
            "-ExpectedVersion",
            expected_version,
            "-ExpectedSha256",
            digest,
            "-ResultPath",
            str(result_path),
        ]
        if test_fault_point:
            arguments.extend(["-TestFaultPoint", test_fault_point])
        if test_fault_marker:
            arguments.extend(["-TestFaultMarkerPath", str(test_fault_marker)])
        return subprocess.run(
            arguments,
            cwd=install,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def _wait_for_path(self, path: Path, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

    def _run_git(self, git: str, *arguments: str | Path, cwd: Path) -> str:
        result = subprocess.run(
            [git, *(str(argument) for argument in arguments)],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def _run_update_script(
        self,
        powershell: str,
        distribution: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(distribution / "scripts" / "update.ps1"),
                *arguments,
            ],
            cwd=distribution,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def _write_git_distribution_version(self, source: Path, version: str, app_content: str) -> None:
        (source / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (source / "src" / "app.py").write_text(app_content, encoding="utf-8")
        (source / "scripts" / "setup.ps1").write_text(
            "$root = Split-Path -Parent $PSScriptRoot\n"
            f"[IO.File]::WriteAllText((Join-Path $root 'setup-ran.txt'), '{version}')\n",
            encoding="utf-8",
        )

    def _create_git_update_fixture(self, base: Path, git: str) -> tuple[Path, Path]:
        remote = base / "remote.git"
        source = base / "upstream source"
        distribution = base / "Subtitle Edit Bay"

        self._run_git(git, "init", "--bare", "--initial-branch=main", remote, cwd=base)
        self._run_git(git, "init", "--initial-branch=main", source, cwd=base)
        self._run_git(git, "config", "user.name", "Updater Test", cwd=source)
        self._run_git(git, "config", "user.email", "updater@example.invalid", cwd=source)
        (source / "scripts").mkdir()
        (source / "src").mkdir()
        shutil.copy2(ROOT / "scripts" / "update.ps1", source / "scripts" / "update.ps1")
        self._write_git_distribution_version(source, "v0.1.0", "old app")
        self._run_git(git, "add", ".", cwd=source)
        self._run_git(git, "commit", "-m", "initial", cwd=source)
        self._run_git(git, "remote", "add", "origin", remote, cwd=source)
        self._run_git(git, "push", "--set-upstream", "origin", "main", cwd=source)
        self._run_git(git, "clone", "--branch", "main", remote, distribution, cwd=base)
        self._run_git(git, "config", "user.name", "Updater Test", cwd=distribution)
        self._run_git(git, "config", "user.email", "updater@example.invalid", cwd=distribution)
        return source, distribution

    def _push_git_distribution_version(
        self,
        source: Path,
        git: str,
        version: str,
        app_content: str,
    ) -> str:
        self._write_git_distribution_version(source, version, app_content)
        self._run_git(git, "add", ".", cwd=source)
        self._run_git(git, "commit", "-m", f"release {version}", cwd=source)
        self._run_git(git, "push", "origin", "main", cwd=source)
        return self._run_git(git, "rev-parse", "HEAD", cwd=source)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_start_batch_runs_launch_script_from_distribution_root(self) -> None:
        command_prompt = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        self.assertTrue(command_prompt, "Windows command prompt is required")

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "Subtitle Edit Bay"
            scripts = distribution / "scripts"
            scripts.mkdir(parents=True)
            outside = base / "unrelated working directory"
            outside.mkdir()
            shutil.copy2(ROOT / "start.bat", distribution / "start.bat")

            marker = base / "launch-result.json"
            escaped_marker = str(marker).replace("'", "''")
            (scripts / "launch.ps1").write_text(
                "$payload = [ordered]@{\n"
                "    working_directory = (Get-Location).Path\n"
                "    script_root = $PSScriptRoot\n"
                "} | ConvertTo-Json -Compress\n"
                f"[IO.File]::WriteAllText('{escaped_marker}', $payload)\n"
                "exit 23\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [str(command_prompt), "/d", "/c", str(distribution / "start.bat")],
                cwd=outside,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )

            self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
            self.assertTrue(marker.is_file(), result.stdout + result.stderr)
            payload = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(Path(payload["working_directory"]).resolve(), distribution.resolve())
            self.assertEqual(Path(payload["script_root"]).resolve(), scripts.resolve())

    def test_setup_uses_module_pip_and_winget_fallbacks(self) -> None:
        launcher = (ROOT / "setup.bat").read_text(encoding="utf-8")
        setup = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8")

        self.assertIn(r"Sysnative\WindowsPowerShell\v1.0\powershell.exe", launcher)
        self.assertIn(r"System32\WindowsPowerShell\v1.0\powershell.exe", launcher)
        self.assertIn('"%POWERSHELL_EXE%"', launcher)
        self.assertIn("$runtimeContract.python.winget_package", setup)
        self.assertIn("$runtimeContract.ffmpeg.winget_package", setup)
        self.assertIn("-m pip install", setup)
        self.assertIn("check_runtime_dependencies", setup)
        self.assertIn('Get-Command "nvidia-smi.exe"', setup)
        self.assertIn('"Sysnative\\nvidia-smi.exe"', setup)
        self.assertIn('"System32\\nvidia-smi.exe"', setup)
        self.assertIn("PowerShell architecture:", setup)
        self.assertIn('State = "execution_failed"', setup)
        self.assertIn("NVIDIA SMI probe failed (exit code", setup)
        self.assertIn("Update or reinstall the NVIDIA driver", setup)
        self.assertIn("PyTorch CUDA runtime:", setup)
        self.assertIn("PyTorch CUDA available:", setup)
        self.assertIn("changed unavailable CUDA selection to cpu/int8", setup)
        self.assertIn("runtime\\runtime-contract.json", setup)
        self.assertIn("--require-hashes", setup)
        self.assertIn(".local\\runtimes", setup)
        self.assertNotIn("Move-Item -LiteralPath $stagingVenv", setup)
        self.assertIn("Set-ActiveRuntimeGeneration", setup)
        self.assertIn("runtime-manifest.json", setup)
        self.assertIn("verify-tools", setup)
        self.assertNotIn('pip install -r "requirements.txt"', setup)
        self.assertIn("-m pip check", setup)
        self.assertIn('$ErrorActionPreference = "Continue"', setup)
        self.assertIn('$PSDefaultParameterValues["*:ErrorAction"] = "Stop"', setup)
        self.assertIn('$ProfileContract.PSObject.Properties["extra_index_url"]', setup)
        self.assertNotIn("$profileContract.extra_index_url", setup)
        setup_state = (ROOT / "scripts" / "setup_state.ps1").read_text(encoding="utf-8")
        self.assertNotIn("Set-StrictMode", setup_state)

        launch = (ROOT / "installer" / "launch.ps1").read_text(encoding="utf-8")
        self.assertIn("Resolve-ActiveRuntimeDirectory", launch)
        self.assertIn("runtime_directory", launch)
        self.assertIn("Show-SetupFailure", launch)
        self.assertEqual(launch.count("Show-SetupFailure; exit $exitCode"), 2)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_cpu_install_arguments_allow_missing_optional_extra_index_without_test_hook(self) -> None:
        powershell = self._require_windows_powershell()
        environment = os.environ.copy()
        environment.pop("SUBTITLE_EDIT_BAY_SETUP_TEST_HOOK", None)
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "setup.ps1"),
                "-ProbeCpuInstallArgumentsOnly",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn("--require-hashes", arguments)
        self.assertIn("--index-url", arguments)
        self.assertIn("-r", arguments)
        self.assertNotIn("--extra-index-url", arguments)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_launcher_requests_repair_for_cpu_only_torch_when_cuda_is_selected(self) -> None:
        powershell = self._require_windows_powershell()
        launch_script = ROOT / "installer" / "launch.ps1"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / ".gui" / "runtime_config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({"shared": {"device": "cuda"}}), encoding="utf-8")

            unavailable_python = root / "cuda-unavailable.cmd"
            unavailable_python.write_text("@exit /b 1\r\n", encoding="ascii")
            available_python = root / "cuda-available.cmd"
            available_python.write_text("@exit /b 0\r\n", encoding="ascii")

            def probe(python: Path) -> str:
                result = subprocess.run(
                    [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(launch_script),
                        "-ProbeCudaRepairOnly",
                        "-ProjectRootOverride",
                        str(root),
                        "-PythonOverride",
                        str(python),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout.strip().splitlines()[-1]

            self.assertEqual(probe(unavailable_python), "true")
            self.assertEqual(probe(available_python), "false")

            config_path.write_text(json.dumps({"shared": {"device": "cpu"}}), encoding="utf-8")
            self.assertEqual(probe(unavailable_python), "false")

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installed_launcher_resolves_root_and_routes_setup_or_gui_exclusively(self) -> None:
        powershell = self._require_windows_powershell()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Subtitle Edit Bay"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            launch_script = scripts / "launch.ps1"
            shutil.copy2(ROOT / "installer" / "launch.ps1", launch_script)
            shutil.copy2(ROOT / "scripts" / "setup_state.ps1", scripts / "setup_state.ps1")
            outside = Path(temp_dir) / "unrelated working directory"
            outside.mkdir()
            config_path = root / ".gui" / "runtime_config.json"
            config_path.parent.mkdir(parents=True)
            (root / "VERSION").write_text("1.2.3\n", encoding="ascii")
            (root / ".local").mkdir()
            (root / ".local" / "setup-status.json").write_text(
                json.dumps({"schema_version": 1, "status": "success", "app_version": "1.2.3"}),
                encoding="utf-8",
            )
            (root / "assets").mkdir()
            (root / "src").mkdir()
            (root / "src" / "__init__.py").write_text("", encoding="ascii")
            (root / "src" / "gui.py").write_text(
                'from pathlib import Path\nPath("gui-ran.txt").write_text("gui", encoding="ascii")\n',
                encoding="ascii",
            )
            logs = root / "test logs"
            setup_marker = root / "setup-ran.txt"
            gui_marker = root / "gui-ran.txt"

            unavailable_python = root / "cuda unavailable.cmd"
            unavailable_python.write_text("@exit /b 1\r\n", encoding="ascii")
            available_python = root / "cuda available.cmd"
            available_python.write_text("@exit /b 0\r\n", encoding="ascii")
            setup = root / "setup runner.cmd"
            setup.write_text('@echo off\r\n> "%~dp0setup-ran.txt" echo setup\r\nexit /b 0\r\n', encoding="ascii")
            gui = Path(sys.executable)
            inherited_path = os.environ.get("Path") or os.environ.get("PATH") or ""
            environment = {key: value for key, value in os.environ.items() if key.lower() != "path"}
            environment["Path"] = inherited_path

            def run_launcher(
                *,
                device: str,
                python: Path,
                pythonw: Path = gui,
                use_default_config: bool = False,
                expected_returncode: int = 0,
            ) -> None:
                setup_marker.unlink(missing_ok=True)
                gui_marker.unlink(missing_ok=True)
                if use_default_config:
                    config_path.unlink(missing_ok=True)
                    (root / "assets" / "runtime_config.json").write_text(
                        json.dumps({"shared": {"device": device}}),
                        encoding="utf-8",
                    )
                else:
                    config_path.write_text(json.dumps({"shared": {"device": device}}), encoding="utf-8")

                result = subprocess.run(
                    [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(launch_script),
                        "-SuppressMessages",
                        "-PythonOverride",
                        str(python),
                        "-PythonwOverride",
                        str(pythonw),
                        "-SetupExecutableOverride",
                        str(setup),
                        "-LogDirectoryOverride",
                        str(logs),
                    ],
                    cwd=outside,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                )
                self.assertEqual(result.returncode, expected_returncode, result.stdout + result.stderr)

            run_launcher(device="cuda", python=unavailable_python)
            deadline = time.monotonic() + 5
            while not setup_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(setup_marker.is_file())
            self.assertFalse(gui_marker.exists())

            run_launcher(device="cuda", python=available_python)
            self.assertTrue(
                gui_marker.is_file(),
                (logs / "latest-launch-error.log").read_text(encoding="utf-8", errors="replace"),
            )
            self.assertFalse(setup_marker.exists())

            run_launcher(device="cpu", python=unavailable_python)
            self.assertTrue(
                gui_marker.is_file(),
                (logs / "latest-launch-error.log").read_text(encoding="utf-8", errors="replace"),
            )
            self.assertFalse(setup_marker.exists())

            run_launcher(device="cuda", python=unavailable_python, use_default_config=True)
            deadline = time.monotonic() + 5
            while not setup_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(setup_marker.is_file())
            self.assertFalse(gui_marker.exists())

            run_launcher(
                device="cpu",
                python=unavailable_python,
                pythonw=root / "missing pythonw.exe",
                expected_returncode=1,
            )
            deadline = time.monotonic() + 5
            while not setup_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(setup_marker.is_file())
            self.assertFalse(gui_marker.exists())

            (root / "src" / "gui.py").write_text(
                "import sys\n"
                'sys.stderr.write("synthetic GUI child failure\\n")\n'
                "raise SystemExit(23)\n",
                encoding="utf-8",
            )
            run_launcher(device="cpu", python=unavailable_python, expected_returncode=23)
            self.assertFalse(setup_marker.exists())
            self.assertFalse(gui_marker.exists())
            self.assertIn(
                "synthetic GUI child failure",
                (logs / "latest-launch-error.log").read_text(encoding="utf-8", errors="replace"),
            )

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell is required")
    def test_setup_gpu_probe_searches_sysnative_and_system32_paths(self) -> None:
        powershell = str(Path(shutil.which("powershell.exe") or "").resolve())
        setup_script = ROOT / "scripts" / "setup.ps1"

        for parent_architecture, system_directory in (
            ("32-bit", "Sysnative"),
            ("64-bit", "System32"),
        ):
            with self.subTest(parent_architecture=parent_architecture), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                windows_root = root / "Windows"
                candidate = windows_root / system_directory / "nvidia-smi.exe"
                candidate.parent.mkdir(parents=True)
                candidate.write_bytes(b"probe")
                environment = os.environ.copy()
                environment["PATH"] = ""
                environment["ProgramFiles"] = str(root / "Program Files")

                result = subprocess.run(
                    [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(setup_script),
                        "-ProbeNvidiaOnly",
                        "-NvidiaSmiSearchRoot",
                        str(windows_root),
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(Path(result.stdout.strip()), candidate.resolve())

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell is required")
    def test_setup_gpu_probe_distinguishes_driver_failure_from_missing_gpu(self) -> None:
        powershell = str(Path(shutil.which("powershell.exe") or "").resolve())
        setup_script = ROOT / "scripts" / "setup.ps1"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_nvidia_smi = root / "nvidia-smi.cmd"
            fake_nvidia_smi.write_text(
                "@echo off\r\n"
                "echo synthetic NVIDIA driver failure 1>&2\r\n"
                "exit /b 17\r\n",
                encoding="ascii",
            )

            failed = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(setup_script),
                    "-ProbeNvidiaStatusOnly",
                    "-NvidiaSmiOverride",
                    str(fake_nvidia_smi),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
            failure_probe = json.loads(failed.stdout.strip().splitlines()[-1])
            self.assertEqual(failure_probe["State"], "execution_failed")
            self.assertEqual(failure_probe["ExitCode"], 17)
            self.assertIn("synthetic NVIDIA driver failure", failure_probe["Output"])

            environment = os.environ.copy()
            environment["PATH"] = ""
            environment["ProgramFiles"] = str(root / "Program Files")
            missing = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(setup_script),
                    "-ProbeNvidiaStatusOnly",
                    "-NvidiaSmiSearchRoot",
                    str(root / "empty-windows"),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
            missing_probe = json.loads(missing.stdout.strip().splitlines()[-1])
            self.assertEqual(missing_probe["State"], "not_found")
            self.assertIsNone(missing_probe["ExitCode"])

    @unittest.skipUnless(os.name == "nt", "Windows architecture paths are required")
    def test_setup_batch_upgrades_32_bit_parent_to_64_bit_powershell(self) -> None:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        powershell_32 = system_root / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        powershell_64 = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        cmd_32 = system_root / "SysWOW64" / "cmd.exe"
        cmd_64 = system_root / "System32" / "cmd.exe"
        required = (powershell_32, powershell_64, cmd_32, cmd_64)
        if not all(path.is_file() for path in required):
            self.skipTest("Both 32-bit and 64-bit Windows shells are required")

        for powershell, expected_bits in ((powershell_32, "4"), (powershell_64, "8")):
            architecture = subprocess.run(
                [
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "[IntPtr]::Size",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(architecture.returncode, 0, architecture.stdout + architecture.stderr)
            self.assertEqual(architecture.stdout.strip(), expected_bits)

        launcher = ROOT / "setup.bat"
        for parent in (cmd_32, cmd_64):
            with self.subTest(parent=parent):
                result = subprocess.run(
                    [
                        str(parent),
                        "/d",
                        "/c",
                        str(launcher),
                        "--probe-powershell",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip().splitlines()[-1], "64")

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_update_batch_runs_update_script_from_distribution_root(self) -> None:
        command_prompt = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        self.assertTrue(command_prompt, "Windows command prompt is required")
        self._require_windows_powershell()

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "Subtitle Edit Bay"
            scripts = distribution / "scripts"
            scripts.mkdir(parents=True)
            outside = base / "unrelated working directory"
            outside.mkdir()
            shutil.copy2(ROOT / "update.bat", distribution / "update.bat")

            marker = base / "update-result.json"
            escaped_marker = str(marker).replace("'", "''")
            (scripts / "update.ps1").write_text(
                "$payload = [ordered]@{\n"
                "    working_directory = (Get-Location).Path\n"
                "    script_root = $PSScriptRoot\n"
                "} | ConvertTo-Json -Compress\n"
                f"[IO.File]::WriteAllText('{escaped_marker}', $payload)\n"
                "exit 23\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [str(command_prompt), "/d", "/c", str(distribution / "update.bat")],
                cwd=outside,
                input="\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue(marker.is_file(), result.stdout + result.stderr)
            payload = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(Path(payload["working_directory"]).resolve(), distribution.resolve())
            self.assertEqual(Path(payload["script_root"]).resolve(), scripts.resolve())

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_git_update_fast_forwards_and_preserves_untracked_data(self) -> None:
        powershell = self._require_windows_powershell()
        git = self._require_windows_git()

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source, distribution = self._create_git_update_fixture(base, git)
            remote_head = self._push_git_distribution_version(source, git, "v0.2.0", "new app")

            (distribution / ".gui").mkdir()
            (distribution / "video_import").mkdir()
            (distribution / ".gui" / "runtime_config.json").write_text("local gui", encoding="utf-8")
            (distribution / "video_import" / "source.mkv").write_bytes(b"local video")
            (distribution / "project-state.json").write_text("local project", encoding="utf-8")

            result = self._run_update_script(powershell, distribution)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Source version before update: v0.1.0", result.stdout)
            self.assertIn("Source version: v0.1.0 -> v0.2.0", result.stdout)
            self.assertEqual(self._run_git(git, "rev-parse", "HEAD", cwd=distribution), remote_head)
            self.assertEqual((distribution / "VERSION").read_text(encoding="utf-8"), "v0.2.0\n")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "new app")
            self.assertEqual((distribution / "setup-ran.txt").read_text(encoding="utf-8"), "v0.2.0")
            self.assertEqual(
                (distribution / ".gui" / "runtime_config.json").read_text(encoding="utf-8"),
                "local gui",
            )
            self.assertEqual((distribution / "video_import" / "source.mkv").read_bytes(), b"local video")
            self.assertEqual((distribution / "project-state.json").read_text(encoding="utf-8"), "local project")

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_git_update_rejects_tracked_worktree_changes(self) -> None:
        powershell = self._require_windows_powershell()
        git = self._require_windows_git()

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            _, distribution = self._create_git_update_fixture(base, git)
            initial_head = self._run_git(git, "rev-parse", "HEAD", cwd=distribution)
            (distribution / "src" / "app.py").write_text("local edit", encoding="utf-8")

            result = self._run_update_script(powershell, distribution)

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Tracked files have local changes", result.stdout + result.stderr)
            self.assertEqual(self._run_git(git, "rev-parse", "HEAD", cwd=distribution), initial_head)
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "local edit")
            self.assertFalse((distribution / "setup-ran.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_git_update_rejects_non_fast_forward_history(self) -> None:
        powershell = self._require_windows_powershell()
        git = self._require_windows_git()

        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            source, distribution = self._create_git_update_fixture(base, git)
            self._run_git(git, "config", "pull.rebase", "true", cwd=distribution)
            (distribution / "local-only.txt").write_text("local commit", encoding="utf-8")
            self._run_git(git, "add", "local-only.txt", cwd=distribution)
            self._run_git(git, "commit", "-m", "local commit", cwd=distribution)
            local_head = self._run_git(git, "rev-parse", "HEAD", cwd=distribution)
            (source / "remote-only.txt").write_text("remote commit", encoding="utf-8")
            self._push_git_distribution_version(source, git, "v0.2.0", "new app")

            result = self._run_update_script(powershell, distribution)

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("git pull failed", result.stdout + result.stderr)
            self.assertEqual(self._run_git(git, "rev-parse", "HEAD", cwd=distribution), local_head)
            self.assertEqual((distribution / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertFalse((distribution / "remote-only.txt").exists())
            self.assertFalse((distribution / "setup-ran.txt").exists())

    def test_git_update_never_uses_destructive_reset(self) -> None:
        updater = (ROOT / "scripts" / "update.ps1").read_text(encoding="utf-8")

        self.assertNotIn("reset --hard", updater)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_zip_release_update_downloads_and_preserves_local_data(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "distribution"
            archive_parent = base / "archive"
            archive_root = archive_parent / "subtitle-edit-bay-main"

            (distribution / "scripts").mkdir(parents=True)
            (distribution / "src").mkdir()
            (distribution / "assets").mkdir()
            for directory in ("video_import", "video_export", "out", ".gui", ".venv", ".local"):
                (distribution / directory).mkdir()
            shutil.copy2(ROOT / "scripts" / "update.ps1", distribution / "scripts" / "update.ps1")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")
            (distribution / "legacy.txt").write_text("remove me", encoding="utf-8")
            (distribution / ".env").write_text("keep me", encoding="utf-8")
            (distribution / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
            (distribution / ".local" / "update-manifest.json").write_text(
                '["README.md", "src/app.py", "legacy.txt"]\n',
                encoding="utf-8",
            )
            (distribution / ".local" / "user-state.json").write_text("old local", encoding="utf-8")
            (distribution / "assets" / "speaker_colors.json").write_text("old colors", encoding="utf-8")
            (distribution / "video_import" / "marker.txt").write_text("old import", encoding="utf-8")
            (distribution / "video_export" / "marker.txt").write_text("old export", encoding="utf-8")
            (distribution / "out" / "marker.txt").write_text("old output", encoding="utf-8")
            (distribution / ".gui" / "runtime_config.json").write_text("old gui", encoding="utf-8")
            (distribution / ".venv" / "marker.txt").write_text("old venv", encoding="utf-8")

            (archive_root / "scripts").mkdir(parents=True)
            (archive_root / "src").mkdir()
            (archive_root / "assets").mkdir()
            for directory in ("video_import", "video_export", "out", ".gui", ".venv", ".local"):
                (archive_root / directory).mkdir()
            (archive_root / "README.md").write_text("new readme", encoding="utf-8")
            (archive_root / "src" / "app.py").write_text("new code", encoding="utf-8")
            (archive_root / "VERSION").write_text("v0.2.0\n", encoding="utf-8")
            (archive_root / "assets" / "speaker_colors.json").write_text("new colors", encoding="utf-8")
            (archive_root / "video_import" / "marker.txt").write_text("new import", encoding="utf-8")
            (archive_root / "video_export" / "marker.txt").write_text("new export", encoding="utf-8")
            (archive_root / "out" / "marker.txt").write_text("new output", encoding="utf-8")
            (archive_root / ".gui" / "runtime_config.json").write_text("new gui", encoding="utf-8")
            (archive_root / ".venv" / "marker.txt").write_text("new venv", encoding="utf-8")
            (archive_root / ".local" / "user-state.json").write_text("new local", encoding="utf-8")
            (archive_root / "scripts" / "setup.ps1").write_text(
                '$root = Split-Path -Parent $PSScriptRoot\n'
                '[IO.File]::WriteAllText((Join-Path $root "setup-ran.txt"), "ok")\n',
                encoding="utf-8",
            )

            zip_path = Path(shutil.make_archive(str(base / "latest"), "zip", root_dir=archive_parent))
            archive_bytes = zip_path.read_bytes()
            requested_paths: list[str] = []

            class ReleaseHandler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    requested_paths.append(self.path)
                    if self.path == "/releases/latest":
                        payload = json.dumps({"tag_name": "v0.2.0"}).encode("utf-8")
                        content_type = "application/json"
                    elif self.path == "/archive/refs/tags/v0.2.0.zip":
                        payload = archive_bytes
                        content_type = "application/zip"
                    else:
                        self.send_error(404)
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format: str, *args: object) -> None:
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), ReleaseHandler)
            server_url = f"http://127.0.0.1:{server.server_address[1]}"
            server_thread = Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            try:
                result = self._run_update_script(
                    powershell,
                    distribution,
                    "-ReleaseApiUrlOverride",
                    f"{server_url}/releases/latest",
                    "-ReleaseArchiveBaseUrlOverride",
                    f"{server_url}/",
                )
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                requested_paths,
                ["/releases/latest", "/archive/refs/tags/v0.2.0.zip"],
            )
            self.assertIn("Resolved latest release: v0.2.0", result.stdout)
            self.assertIn(f"Downloading ZIP distribution from {server_url}", result.stdout)
            self.assertIn("Source version before update: v0.1.0", result.stdout)
            self.assertIn("Source version after update: v0.2.0", result.stdout)
            self.assertEqual((distribution / "README.md").read_text(encoding="utf-8"), "new readme")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "new code")
            self.assertEqual((distribution / "VERSION").read_text(encoding="utf-8"), "v0.2.0\n")
            self.assertEqual((distribution / ".env").read_text(encoding="utf-8"), "keep me")
            self.assertFalse((distribution / "legacy.txt").exists())
            self.assertEqual(
                (distribution / "assets" / "speaker_colors.json").read_text(encoding="utf-8"),
                "old colors",
            )
            self.assertEqual((distribution / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual((distribution / ".venv" / "marker.txt").read_text(encoding="utf-8"), "old venv")
            self.assertEqual((distribution / ".local" / "user-state.json").read_text(encoding="utf-8"), "old local")
            self.assertEqual((distribution / "video_import" / "marker.txt").read_text(encoding="utf-8"), "old import")
            self.assertEqual((distribution / "video_export" / "marker.txt").read_text(encoding="utf-8"), "old export")
            self.assertEqual((distribution / "out" / "marker.txt").read_text(encoding="utf-8"), "old output")
            self.assertEqual((distribution / "setup-ran.txt").read_text(encoding="utf-8"), "ok")

            installed_manifest = json.loads(
                (distribution / ".local" / "update-manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("README.md", installed_manifest)
            self.assertIn("VERSION", installed_manifest)
            self.assertIn("scripts\\setup.ps1", installed_manifest)
            self.assertNotIn(".gui\\runtime_config.json", installed_manifest)

            backups = list((distribution / ".local" / "update_backups").glob("*/README.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old readme")

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_zip_update_rolls_back_when_setup_fails(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            distribution = base / "distribution"
            archive_parent = base / "archive"
            archive_root = archive_parent / "subtitle-edit-bay-main"

            (distribution / "scripts").mkdir(parents=True)
            (distribution / "src").mkdir()
            (distribution / "assets").mkdir()
            for directory in ("video_import", "video_export", "out", ".gui", ".venv", ".local"):
                (distribution / directory).mkdir()
            shutil.copy2(ROOT / "scripts" / "update.ps1", distribution / "scripts" / "update.ps1")
            (distribution / "README.md").write_text("old readme", encoding="utf-8")
            (distribution / "src" / "app.py").write_text("old code", encoding="utf-8")
            (distribution / "legacy.txt").write_text("remove me", encoding="utf-8")
            (distribution / ".env").write_text("keep me", encoding="utf-8")
            (distribution / ".local" / "update-manifest.json").write_text(
                '["README.md", "src/app.py", "legacy.txt"]\n',
                encoding="utf-8",
            )
            (distribution / ".local" / "user-state.json").write_text("old local", encoding="utf-8")
            (distribution / "assets" / "speaker_colors.json").write_text("old colors", encoding="utf-8")
            (distribution / "video_import" / "marker.txt").write_text("old import", encoding="utf-8")
            (distribution / "video_export" / "marker.txt").write_text("old export", encoding="utf-8")
            (distribution / "out" / "marker.txt").write_text("old output", encoding="utf-8")
            (distribution / ".gui" / "runtime_config.json").write_text("old gui", encoding="utf-8")
            (distribution / ".venv" / "marker.txt").write_text("old venv", encoding="utf-8")
            (distribution / "VERSION").write_text("v0.1.0\n", encoding="utf-8")

            (archive_root / "scripts").mkdir(parents=True)
            (archive_root / "src").mkdir()
            (archive_root / "README.md").write_text("new readme", encoding="utf-8")
            (archive_root / "src" / "app.py").write_text("new code", encoding="utf-8")
            (archive_root / "VERSION").write_text("v0.2.0\n", encoding="utf-8")
            (archive_root / "scripts" / "setup.ps1").write_text(
                'throw "setup failed"\n',
                encoding="utf-8",
            )

            zip_path = Path(shutil.make_archive(str(base / "latest"), "zip", root_dir=archive_parent))
            result = subprocess.run(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(distribution / "scripts" / "update.ps1"),
                    "-ArchiveUrl",
                    str(zip_path),
                ],
                cwd=distribution,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Update failed", result.stdout)
            self.assertIn("Restoring files", result.stdout)
            self.assertEqual((distribution / "README.md").read_text(encoding="utf-8"), "old readme")
            self.assertEqual((distribution / "src" / "app.py").read_text(encoding="utf-8"), "old code")
            self.assertTrue((distribution / "legacy.txt").is_file())
            self.assertEqual((distribution / "legacy.txt").read_text(encoding="utf-8"), "remove me")
            self.assertEqual((distribution / ".env").read_text(encoding="utf-8"), "keep me")
            self.assertEqual(
                (distribution / "assets" / "speaker_colors.json").read_text(encoding="utf-8"),
                "old colors",
            )
            self.assertEqual((distribution / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((distribution / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual((distribution / ".venv" / "marker.txt").read_text(encoding="utf-8"), "old venv")
            self.assertEqual((distribution / ".local" / "user-state.json").read_text(encoding="utf-8"), "old local")
            self.assertEqual((distribution / "video_import" / "marker.txt").read_text(encoding="utf-8"), "old import")
            self.assertEqual((distribution / "video_export" / "marker.txt").read_text(encoding="utf-8"), "old export")
            self.assertEqual((distribution / "out" / "marker.txt").read_text(encoding="utf-8"), "old output")
            self.assertFalse((distribution / "setup-ran.txt").exists())

            backups = list((distribution / ".local" / "update_backups").glob("*/README.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old readme")

    def test_local_install_artifacts_are_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".venv/", ignore)
        self.assertIn(".local/", ignore)

    def test_documentation_uses_launchers_and_virtual_environment(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        usage = (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8")

        self.assertIn("setup.bat", readme)
        self.assertIn("start.bat", readme)
        self.assertIn("update.bat", readme)
        self.assertIn(r".\.venv\Scripts\python.exe", readme)
        self.assertIn(r".\.venv\Scripts\python.exe", usage)
        self.assertIn("update.bat", usage)
        self.assertNotIn("\npython -m src.gui", readme)
        self.assertNotIn("\npython -m src.gui", usage)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_applies_update_writes_result_and_restarts(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "Subtitle Edit Bay"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)
            escaped_restart_marker = str(restart_marker).replace("'", "''")

            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'installer-arguments.txt'), ($args -join \"`n\"))\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
                f"[IO.File]::WriteAllText((Join-Path $root 'scripts\\launch.ps1'), \"[IO.File]::WriteAllText('{escaped_restart_marker}', 'started')\")\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\app.py'), 'new app')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\new.py'), 'new file')\n"
                "exit 0\n",
            )
            result_path = base / "update-result.json"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deadline = time.monotonic() + 5
            while not restart_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v9.9.9")
            self.assertIn("WriteAllText", (install / "scripts" / "launch.ps1").read_text(encoding="utf-8"))
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "new app")
            self.assertEqual((install / "src" / "new.py").read_text(encoding="utf-8"), "new file")
            self.assertEqual((install / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertTrue(restart_marker.is_file(), update_result)
            self.assertEqual(update_result["status"], "success", update_result)
            self.assertEqual(update_result["old_version"], "v0.1.0", update_result)
            self.assertEqual(update_result["new_version"], "v9.9.9", update_result)
            self.assertEqual(update_result["restart_mode"], "powershell", update_result)
            installer_arguments = (install / "installer-arguments.txt").read_text(encoding="utf-8")
            self.assertIn("/DIR=", installer_arguments)
            self.assertIn(str(install), installer_arguments)
            self.assertIn("/LOG=", installer_arguments)
            self.assertTrue(Path(update_result["log"]).is_file())
            self.assertTrue(Path(update_result["setup_log"]).is_file())
            self.assertNotEqual(Path(update_result["log"]).resolve(), result_path.resolve())
            self.assertTrue((install / ".venv" / "new-runtime.txt").is_file())
            self.assertFalse((install / ".venv" / "old-runtime.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_rejects_checksum_before_starting_installer(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            installer_marker = base / "installer-started.txt"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)

            escaped_marker = str(installer_marker).replace("%", "%%")
            fake_installer = base / "fake-installer.cmd"
            fake_installer.write_text(
                f'@echo off\r\n> "{escaped_marker}" echo started\r\nexit /b 0\r\n',
                encoding="utf-8",
            )
            result_path = base / "update-result.json"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
                expected_sha256="0" * 64,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertFalse(installer_marker.exists(), update_result)
            self._wait_for_path(restart_marker)
            self.assertTrue(restart_marker.exists(), update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertNotEqual(update_result["status"], "success", update_result)
            self.assertIn("checksum does not match", update_result["message"])

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_rolls_back_when_installed_version_mismatches(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)

            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.8')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'scripts\\launch.ps1'), 'new launcher')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\app.py'), 'new app')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\new.py'), 'new file')\n"
                "exit 0\n",
            )
            result_path = base / "update-result.json"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self._wait_for_path(restart_marker)
            self.assertTrue(restart_marker.exists(), update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertIn("old launcher", (install / "scripts" / "launch.ps1").read_text(encoding="utf-8"))
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertFalse((install / "src" / "new.py").exists())
            self.assertEqual((install / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual(update_result["status"], "rollback", update_result)
            self.assertTrue(update_result["rollback_restored"])
            self.assertTrue(update_result["rollback_restarted"])
            self.assertTrue((install / ".venv" / "old-runtime.txt").is_file())
            self.assertFalse((install / ".venv" / "new-runtime.txt").exists())
            self.assertIn("does not match v9.9.9", update_result["message"])

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_restores_snapshot_after_partial_failure(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)

            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'scripts\\launch.ps1'), 'new launcher')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\app.py'), 'new app')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\new.py'), 'new file')\n"
                "exit 1\n",
            )
            result_path = base / "update-result.json"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self._wait_for_path(restart_marker)
            self.assertTrue(restart_marker.exists(), update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertIn("old launcher", (install / "scripts" / "launch.ps1").read_text(encoding="utf-8"))
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertFalse((install / "src" / "new.py").exists())
            self.assertEqual((install / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual(update_result["status"], "rollback", update_result)
            self.assertTrue(update_result["rollback_restored"])
            self.assertTrue(update_result["rollback_restarted"])
            self.assertTrue((install / ".venv" / "old-runtime.txt").is_file())
            self.assertIn("Installer exited with code 1", update_result["message"])

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_waits_for_parent_and_runtime_file_lock(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)
            locked_runtime = install / ".venv" / "Scripts" / "python.exe"
            locked_runtime.parent.mkdir()
            locked_runtime.write_bytes(b"locked runtime")
            lock_marker = base / "lock-ready.txt"
            lock_script = base / "hold-runtime.ps1"
            lock_script.write_text(
                "param([string]$Path, [string]$Marker)\n"
                "$stream = [IO.File]::Open($Path, 'Open', 'Read', 'None')\n"
                "[IO.File]::WriteAllText($Marker, 'ready')\n"
                "Start-Sleep -Milliseconds 1200\n"
                "$stream.Dispose()\n",
                encoding="utf-8",
            )
            holder = subprocess.Popen(
                [
                    powershell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(lock_script),
                    "-Path",
                    str(locked_runtime),
                    "-Marker",
                    str(lock_marker),
                ]
            )
            deadline = time.monotonic() + 5
            while not lock_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(lock_marker.exists())
            parent = subprocess.Popen(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Milliseconds 500"]
            )
            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
                "exit 0\n",
            )
            started = time.monotonic()
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=base / "update-result.json",
                parent_pid=parent.pid,
            )
            elapsed = time.monotonic() - started
            parent.wait(timeout=5)
            holder.wait(timeout=5)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertGreaterEqual(elapsed, 0.8)
            update_result = json.loads((base / "update-result.json").read_text(encoding="utf-8"))
            self.assertIn(parent.pid, update_result["process_ids"])
            self.assertTrue((install / ".venv" / "new-runtime.txt").is_file())
            self._wait_for_path(restart_marker)
            self.assertTrue(restart_marker.is_file(), update_result)
            time.sleep(0.5)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_rolls_back_runtime_validation_failure_and_restarts_old_version(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)
            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'scripts\\validate_runtime.ps1'), 'exit 9')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\app.py'), 'new app')\n"
                "exit 0\n",
            )
            result_path = base / "update-result.json"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self._wait_for_path(restart_marker)
            self.assertEqual(update_result["status"], "rollback", update_result)
            self.assertTrue(update_result["rollback_restarted"], update_result)
            self.assertTrue(restart_marker.exists(), update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertTrue((install / ".venv" / "old-runtime.txt").is_file())
            self.assertFalse((install / ".venv" / "new-runtime.txt").exists())
            self.assertTrue(Path(update_result["log"]).is_file())
            self.assertTrue(Path(update_result["setup_log"]).is_file())

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_generation_runtime_rollback_restores_old_generation_and_manifest(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)

            old_runtime = install / ".local" / "runtimes" / "gen-old"
            (old_runtime / "Scripts").mkdir(parents=True)
            (old_runtime / "old-marker.txt").write_text("old runtime gen", encoding="utf-8")
            (old_runtime / "Scripts" / "python.exe").write_bytes(b"old python")
            active_manifest = install / ".local" / "runtime-manifest.json"
            active_manifest.write_text(
                json.dumps({"runtime_directory": ".local/runtimes/gen-old"}) + "\n",
                encoding="utf-8",
            )
            setup_status = install / ".local" / "setup-status.json"
            setup_status.write_text(
                json.dumps({"status": "success", "app_version": "v0.1.0"}) + "\n",
                encoding="utf-8",
            )
            shutil.rmtree(install / ".venv")
            subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(install / ".venv"), str(old_runtime)],
                check=True,
                capture_output=True,
            )

            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
                "$newRuntime = Join-Path $root '.local\\runtimes\\gen-new'\n"
                "New-Item -ItemType Directory -Path (Join-Path $newRuntime 'Scripts') -Force | Out-Null\n"
                "[IO.File]::WriteAllText((Join-Path $newRuntime 'new-marker.txt'), 'new runtime gen')\n"
                "[IO.File]::WriteAllText((Join-Path $root '.local\\runtime-manifest.json'), '{\"runtime_directory\":\".local/runtimes/gen-new\"}`n')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'scripts\\validate_runtime.ps1'), 'exit 1')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\app.py'), 'new app')\n"
                "exit 0\n",
            )
            result_path = base / "update-result.json"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self._wait_for_path(restart_marker)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(update_result["status"], "rollback", update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            restored_manifest = json.loads(active_manifest.read_text(encoding="utf-8"))
            self.assertEqual(restored_manifest["runtime_directory"], ".local/runtimes/gen-old")
            restored_status = json.loads(setup_status.read_text(encoding="utf-8"))
            self.assertEqual(restored_status["app_version"], "v0.1.0")
            self.assertTrue((install / ".venv" / "old-marker.txt").is_file())
            self.assertFalse((install / ".local" / "runtimes" / "gen-new").exists())
            self.assertTrue(restart_marker.is_file(), update_result)
            time.sleep(0.5)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_launch_script_gui_process_exit_does_not_wait_for_child_update_helper(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            scripts = install / "scripts"
            scripts.mkdir(parents=True)
            (install / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
            (install / ".local").mkdir()
            (install / ".local" / "setup-status.json").write_text(
                json.dumps({"status": "success", "app_version": "v0.1.0"}) + "\n",
                encoding="utf-8",
            )
            pid_marker = base / "helper.pid"
            mock_helper = base / "mock-helper.ps1"
            mock_helper.write_text(
                "Start-Sleep -Seconds 30\n",
                encoding="utf-8",
            )
            escaped_helper = str(mock_helper).replace("\\", "\\\\")
            escaped_temp = str(tempfile.gettempdir()).replace("\\", "\\\\")
            escaped_pid_marker = str(pid_marker).replace("\\", "\\\\")
            (install / "src").mkdir(parents=True)
            (install / "src" / "gui.py").write_text(
                "import subprocess, sys\n"
                f"p = subprocess.Popen([r'{powershell}', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', r'{escaped_helper}'], cwd=r'{escaped_temp}', stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)\n"
                f"open(r'{escaped_pid_marker}', 'w', encoding='utf-8').write(str(p.pid))\n"
                "sys.stderr.write('large-stderr-start\\n' + ('x' * 262144) + '\\nlarge-stderr-end\\n')\n"
                "sys.stderr.flush()\n"
                "sys.exit(0)\n",
                encoding="utf-8",
            )
            shutil.copy2(ROOT / "installer" / "launch.ps1", scripts / "launch.ps1")
            shutil.copy2(ROOT / "scripts" / "setup_state.ps1", scripts / "setup_state.ps1")

            started = time.monotonic()
            helper_was_running = False
            try:
                result = subprocess.run(
                    [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(scripts / "launch.ps1"),
                        "-SuppressMessages",
                        "-ProjectRootOverride",
                        str(install),
                        "-PythonwOverride",
                        sys.executable,
                        "-PythonOverride",
                        sys.executable,
                        "-LogDirectoryOverride",
                        str(install / ".local" / "logs"),
                    ],
                    cwd=install,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                )
                self._wait_for_path(pid_marker)
                self.assertTrue(pid_marker.is_file(), result.stdout + result.stderr)
                helper_pid = int(pid_marker.read_text(encoding="utf-8").strip())
                check = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {helper_pid}"], capture_output=True, text=True
                )
                helper_was_running = str(helper_pid) in check.stdout
            finally:
                if pid_marker.is_file():
                    try:
                        helper_pid = int(pid_marker.read_text(encoding="utf-8").strip())
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(helper_pid)], capture_output=True)
                        for _ in range(50):
                            check = subprocess.run(["tasklist", "/FI", f"PID eq {helper_pid}"], capture_output=True, text=True)
                            if str(helper_pid) not in check.stdout:
                                break
                            time.sleep(0.1)
                    except Exception:
                        pass
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertLess(elapsed, 6.0, "launch.ps1 waited for child process")
            self.assertTrue(helper_was_running, "GUI child update helper did not survive launcher exit")
            launch_log = install / ".local" / "logs" / "latest-launch-error.log"
            stderr_text = launch_log.read_text(encoding="utf-8", errors="replace")
            self.assertIn("large-stderr-start", stderr_text)
            self.assertIn("large-stderr-end", stderr_text)
            self.assertGreater(len(stderr_text), 262144)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_snapshot_and_evacuation_failures_preserve_old_runtime(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)
            runtime = install / ".venv"
            (runtime / "intact-marker.txt").write_text("must remain intact", encoding="utf-8")
            setup_status = install / ".local" / "setup-status.json"
            setup_status.parent.mkdir()
            setup_status.write_text(
                json.dumps({"status": "success", "app_version": "v0.1.0"}) + "\n",
                encoding="utf-8",
            )

            fake_installer = base / "fake-installer.cmd"
            fake_installer.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
            for fault_point in ("runtime-state-snapshot", "runtime-evacuation"):
                with self.subTest(fault_point=fault_point):
                    marker = base / f"{fault_point}.reached"
                    result_path = base / f"{fault_point}-result.json"
                    restart_marker.unlink(missing_ok=True)
                    result = self._run_installer_update(
                        powershell=powershell,
                        package=fake_installer,
                        install=install,
                        result_path=result_path,
                        test_fault_point=fault_point,
                        test_fault_marker=marker,
                    )

                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(marker.read_text(encoding="utf-8"), fault_point)
                    update_result = json.loads(result_path.read_text(encoding="utf-8"))
                    self.assertEqual(update_result["status"], "rollback", update_result)
                    self.assertTrue(update_result["rollback_restored"], update_result)
                    self.assertIn(f"Injected update test fault: {fault_point}", update_result["message"])
                    self._wait_for_path(restart_marker)
                    self.assertTrue((runtime / "intact-marker.txt").is_file())
                    self.assertEqual(
                        (runtime / "intact-marker.txt").read_text(encoding="utf-8"),
                        "must remain intact",
                    )
                    self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
                    self.assertEqual(
                        json.loads(setup_status.read_text(encoding="utf-8"))["app_version"],
                        "v0.1.0",
                    )
                    time.sleep(0.2)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_post_commit_cleanup_failure_preserves_new_version(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            restart_marker = base / "restart-marker.txt"
            self._seed_installer_distribution(install, restart_marker)

            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
                "[IO.File]::WriteAllText((Join-Path $root 'src\\app.py'), 'new app')\n"
                "exit 0\n",
            )
            result_path = base / "update-result.json"
            fault_marker = base / "post-commit-cleanup.reached"
            result = self._run_installer_update(
                powershell=powershell,
                package=fake_installer,
                install=install,
                result_path=result_path,
                test_fault_point="post-commit-cleanup",
                test_fault_marker=fault_marker,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self._wait_for_path(restart_marker)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(update_result["status"], "success", update_result)
            self.assertEqual(fault_marker.read_text(encoding="utf-8"), "post-commit-cleanup")
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v9.9.9")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "new app")
            helper_log = Path(update_result["log"]).read_text(encoding="utf-8", errors="replace")
            self.assertIn("post-commit cleanup warning", helper_log)
            recovery_points = list((install / ".local" / "update-recovery").iterdir())
            self.assertEqual(len(recovery_points), 1, recovery_points)
            time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
