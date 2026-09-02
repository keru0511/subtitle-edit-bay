import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WindowsLauncherTests(unittest.TestCase):
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

    def _seed_installer_distribution(self, install: Path) -> None:
        (install / "scripts").mkdir(parents=True)
        (install / "src").mkdir()
        (install / ".gui").mkdir()
        (install / "VERSION").write_text("v0.1.0\n", encoding="utf-8")
        (install / "scripts" / "launch.ps1").write_text("old launcher", encoding="utf-8")
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
        restart_executable: Path,
        result_path: Path,
        expected_version: str = "v9.9.9",
        expected_sha256: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        digest = expected_sha256 or hashlib.sha256(package.read_bytes()).hexdigest()
        return subprocess.run(
            [
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
                "-1",
                "-InstallRoot",
                str(install),
                "-RestartExecutable",
                str(restart_executable),
                "-ExpectedVersion",
                expected_version,
                "-ExpectedSha256",
                digest,
                "-ResultPath",
                str(result_path),
            ],
            cwd=install,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def _write_restart_command(self, command: Path, marker: Path) -> None:
        escaped_marker = str(marker).replace("%", "%%")
        command.write_text(
            f'@echo off\r\n> "{escaped_marker}" echo started\r\nexit /b 0\r\n',
            encoding="utf-8",
        )

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
        self.assertIn('"Python.Python.3.10"', setup)
        self.assertIn('"Gyan.FFmpeg"', setup)
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
        self.assertIn("https://download.pytorch.org/whl/cu128", setup)
        self.assertIn('$whisperXVersion = "3.8.6"', setup)
        self.assertIn('$torchVersion = "2.8.0"', setup)
        self.assertIn("--force-reinstall", setup)
        self.assertIn("--no-deps", setup)
        self.assertIn("-m pip check", setup)
        self.assertIn('$ErrorActionPreference = "Continue"', setup)
        self.assertIn('$PSDefaultParameterValues["*:ErrorAction"] = "Stop"', setup)

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
            outside = Path(temp_dir) / "unrelated working directory"
            outside.mkdir()
            config_path = root / ".gui" / "runtime_config.json"
            config_path.parent.mkdir(parents=True)
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
                *, device: str, python: Path, pythonw: Path = gui, use_default_config: bool = False
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
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

            run_launcher(device="cpu", python=unavailable_python, pythonw=root / "missing pythonw.exe")
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
            run_launcher(device="cpu", python=unavailable_python)
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

    def test_git_update_never_uses_destructive_reset(self) -> None:
        updater = (ROOT / "scripts" / "update.ps1").read_text(encoding="utf-8")

        self.assertNotIn("reset --hard", updater)

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_zip_update_preserves_local_data_and_runs_new_setup(self) -> None:
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

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
            self._seed_installer_distribution(install)
            restart_marker = base / "restart-marker.txt"
            restart_executable = install / "restart.cmd"
            self._write_restart_command(restart_executable, restart_marker)

            fake_installer = self._write_fake_installer(
                base,
                powershell,
                "$root = (Get-Location).Path\n"
                "[IO.File]::WriteAllText((Join-Path $root 'VERSION'), 'v9.9.9')\n"
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
                restart_executable=restart_executable,
                result_path=result_path,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deadline = time.monotonic() + 5
            while not restart_marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v9.9.9")
            self.assertEqual((install / "scripts" / "launch.ps1").read_text(encoding="utf-8"), "new launcher")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "new app")
            self.assertEqual((install / "src" / "new.py").read_text(encoding="utf-8"), "new file")
            self.assertEqual((install / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertTrue(restart_marker.is_file(), update_result)
            self.assertEqual(update_result["status"], "success", update_result)
            self.assertEqual(update_result["old_version"], "v0.1.0", update_result)
            self.assertEqual(update_result["new_version"], "v9.9.9", update_result)
            self.assertEqual(update_result["restart_mode"], "native", update_result)
            self.assertEqual(Path(update_result["log"]).resolve(), result_path.resolve())

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_rejects_checksum_before_starting_installer(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            self._seed_installer_distribution(install)
            installer_marker = base / "installer-started.txt"
            restart_marker = base / "restart-marker.txt"
            restart_executable = install / "restart.cmd"
            self._write_restart_command(restart_executable, restart_marker)

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
                restart_executable=restart_executable,
                result_path=result_path,
                expected_sha256="0" * 64,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertFalse(installer_marker.exists(), update_result)
            self.assertFalse(restart_marker.exists(), update_result)
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
            self._seed_installer_distribution(install)
            restart_marker = base / "restart-marker.txt"
            restart_executable = install / "restart.cmd"
            self._write_restart_command(restart_executable, restart_marker)

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
                restart_executable=restart_executable,
                result_path=result_path,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertFalse(restart_marker.exists(), update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((install / "scripts" / "launch.ps1").read_text(encoding="utf-8"), "old launcher")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertFalse((install / "src" / "new.py").exists())
            self.assertEqual((install / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual(update_result["status"], "rollback", update_result)
            self.assertTrue(update_result["rollback_restored"])
            self.assertIn("does not match v9.9.9", update_result["message"])

    @unittest.skipUnless(os.name == "nt", "Windows is required")
    def test_installer_helper_restores_snapshot_after_partial_failure(self) -> None:
        powershell = self._require_windows_powershell()
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            install = base / "distribution"
            self._seed_installer_distribution(install)
            restart_marker = base / "restart-marker.txt"
            restart_executable = install / "restart.cmd"
            self._write_restart_command(restart_executable, restart_marker)

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
                restart_executable=restart_executable,
                result_path=result_path,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            update_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertFalse(restart_marker.exists(), update_result)
            self.assertEqual((install / "VERSION").read_text(encoding="utf-8"), "v0.1.0\n")
            self.assertEqual((install / "scripts" / "launch.ps1").read_text(encoding="utf-8"), "old launcher")
            self.assertEqual((install / "src" / "app.py").read_text(encoding="utf-8"), "old app")
            self.assertFalse((install / "src" / "new.py").exists())
            self.assertEqual((install / ".gui" / "runtime_config.json").read_text(encoding="utf-8"), "old gui")
            self.assertEqual(update_result["status"], "rollback", update_result)
            self.assertTrue(update_result["rollback_restored"])
            self.assertIn("Installer exited with code 1", update_result["message"])


if __name__ == "__main__":
    unittest.main()
