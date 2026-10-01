import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

from src.runtime_dependencies import (
    RuntimeDependencyStatus,
    _ffmpeg_nvenc_available,
    check_runtime_dependencies,
    format_dependency_error,
    runtime_diagnostic_info,
)
from tests.typed_case import TypedTestCase


class RuntimeDependencyTests(TypedTestCase):
    def test_check_runtime_dependencies_reports_ready(self) -> None:
        with (
            mock.patch("src.runtime_dependencies._ffmpeg_nvenc_available", return_value=True) as nvenc,
            mock.patch("src.runtime_dependencies._torch_cuda_available", return_value=True),
            mock.patch("src.runtime_dependencies._module_importable", return_value=True),
            mock.patch("src.runtime_dependencies.shutil.which", return_value="tool.exe"),
        ):
            status = check_runtime_dependencies(probe_nvenc=True)

        self.assertTrue(status.ready)
        self.assertEqual(status.missing(), [])
        self.assertTrue(status.to_dict()["ready"])
        self.assertTrue(status.cuda)
        self.assertTrue(status.nvenc)
        nvenc.assert_called_once_with("tool.exe")

    def test_nvenc_probe_encodes_a_real_frame(self) -> None:
        command_seen: list[str] = []
        timeout_seen: object = None

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            nonlocal command_seen, timeout_seen
            command_seen = command
            timeout_seen = kwargs.get("timeout")
            return subprocess.CompletedProcess(command, 0)

        with mock.patch("src.runtime_dependencies.subprocess.run", side_effect=fake_run):
            self.assertTrue(_ffmpeg_nvenc_available("ffmpeg.exe"))
        self.assertIn("h264_nvenc", command_seen)
        self.assertIn("color=c=black:s=256x144:r=1", command_seen)
        self.assertEqual(timeout_seen, 8)

    def test_nvenc_probe_falls_back_when_encoder_cannot_start(self) -> None:
        with mock.patch("src.runtime_dependencies.subprocess.run", side_effect=OSError("failed")):
            self.assertFalse(_ffmpeg_nvenc_available("ffmpeg.exe"))
            self.assertFalse(_ffmpeg_nvenc_available(None))

    def test_format_dependency_error_includes_install_hints(self) -> None:
        status = RuntimeDependencyStatus(ffmpeg=False, ffprobe=False, whisperx=False)

        message = format_dependency_error(status)

        self.assertIn("ffmpeg", message)
        self.assertIn("ffprobe", message)
        self.assertIn("whisperx", message)
        self.assertIn("pip install whisperx", message)

    def test_cuda_device_requires_cuda_enabled_pytorch(self) -> None:
        status = RuntimeDependencyStatus(ffmpeg=True, ffprobe=True, whisperx=True, cuda=False)

        message = format_dependency_error(status, device="cuda")

        self.assertIn("CUDA-enabled PyTorch", message)
        self.assertIn("setup.bat", message)

    def test_dry_run_does_not_require_whisperx(self) -> None:
        status = RuntimeDependencyStatus(ffmpeg=True, ffprobe=True, whisperx=False)

        self.assertEqual(format_dependency_error(status, require_whisperx=False), "")

    def test_runtime_diagnostic_reports_torch_cuda_and_ffmpeg_versions(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(
                command, 0, stdout="ffmpeg version 7.1-full_build\nconfiguration...", stderr=""
            )

        def get_device_name(_index: int) -> str:
            return "NVIDIA GeForce RTX 4070"

        fake_torch = ModuleType("torch")
        setattr(fake_torch, "__version__", "2.8.0+cu128")
        setattr(fake_torch, "version", SimpleNamespace(cuda="12.8"))
        setattr(fake_torch, "cuda", SimpleNamespace(is_available=lambda: True, get_device_name=get_device_name))

        previous_torch = sys.modules.get("torch")
        sys.modules["torch"] = fake_torch
        try:
            with (
                mock.patch("src.runtime_dependencies.importlib.util.find_spec", return_value=object()),
                mock.patch("src.runtime_dependencies.shutil.which", return_value="ffmpeg.exe"),
                mock.patch("src.runtime_dependencies.subprocess.run", side_effect=fake_run),
            ):
                diagnostic = runtime_diagnostic_info()
        finally:
            if previous_torch is None:
                sys.modules.pop("torch", None)
            else:
                sys.modules["torch"] = previous_torch

        self.assertEqual(diagnostic["ffmpeg"], "ffmpeg version 7.1-full_build")
        self.assertEqual(diagnostic["pytorch"], "2.8.0+cu128")
        self.assertEqual(diagnostic["pytorch_cuda_build"], "12.8")
        self.assertTrue(diagnostic["cuda_available"])
        self.assertEqual(diagnostic["cuda_device"], "NVIDIA GeForce RTX 4070")
        self.assertEqual(commands[0], ["ffmpeg.exe", "-version"])

    def test_broken_whisperx_import_is_not_reported_as_ready(self) -> None:
        with (
            mock.patch("src.runtime_dependencies.shutil.which", return_value="tool.exe"),
            mock.patch("src.runtime_dependencies._torch_cuda_available", return_value=False),
            mock.patch("src.runtime_dependencies.importlib.import_module", side_effect=OSError("broken DLL")),
        ):
            self.assertFalse(check_runtime_dependencies().whisperx)

    def test_runtime_diagnostic_includes_saved_manifest(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch("src.runtime_dependencies.importlib.util.find_spec", return_value=None),
        ):
            root = Path(temp_dir)
            (root / ".local").mkdir()
            expected = {"profile": "cpu", "lock_sha256": "a" * 64}
            (root / ".local" / "runtime-manifest.json").write_text(json.dumps(expected), encoding="utf-8")

            self.assertEqual(runtime_diagnostic_info(root)["runtime_manifest"], expected)


if __name__ == "__main__":
    unittest.main()
