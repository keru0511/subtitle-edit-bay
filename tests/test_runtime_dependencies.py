import unittest
from unittest import mock

from src.runtime_dependencies import (
    RuntimeDependencyStatus,
    _ffmpeg_nvenc_available,
    check_runtime_dependencies,
    format_dependency_error,
)


class RuntimeDependencyTests(unittest.TestCase):
    @mock.patch("src.runtime_dependencies._ffmpeg_nvenc_available", return_value=True)
    @mock.patch("src.runtime_dependencies._torch_cuda_available", return_value=True)
    @mock.patch("src.runtime_dependencies.importlib.util.find_spec", return_value=object())
    @mock.patch("src.runtime_dependencies.shutil.which", return_value="tool.exe")
    def test_check_runtime_dependencies_reports_ready(
        self,
        _which: mock.Mock,
        _find_spec: mock.Mock,
        _cuda: mock.Mock,
        nvenc: mock.Mock,
    ) -> None:
        status = check_runtime_dependencies(probe_nvenc=True)

        self.assertTrue(status.ready)
        self.assertEqual(status.missing(), [])
        self.assertTrue(status.to_dict()["ready"])
        self.assertTrue(status.cuda)
        self.assertTrue(status.nvenc)
        nvenc.assert_called_once_with("tool.exe")

    @mock.patch("src.runtime_dependencies.subprocess.run")
    def test_nvenc_probe_encodes_a_real_frame(self, run: mock.Mock) -> None:
        run.return_value.returncode = 0

        self.assertTrue(_ffmpeg_nvenc_available("ffmpeg.exe"))
        command = run.call_args.args[0]
        self.assertIn("h264_nvenc", command)
        self.assertIn("color=c=black:s=256x144:r=1", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 8)

    @mock.patch("src.runtime_dependencies.subprocess.run", side_effect=OSError("failed"))
    def test_nvenc_probe_falls_back_when_encoder_cannot_start(self, _run: mock.Mock) -> None:
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


if __name__ == "__main__":
    unittest.main()
