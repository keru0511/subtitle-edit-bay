import unittest
from unittest import mock

from src.runtime_dependencies import (
    RuntimeDependencyStatus,
    check_runtime_dependencies,
    format_dependency_error,
)


class RuntimeDependencyTests(unittest.TestCase):
    @mock.patch("src.runtime_dependencies.importlib.util.find_spec", return_value=object())
    @mock.patch("src.runtime_dependencies.shutil.which", return_value="tool.exe")
    def test_check_runtime_dependencies_reports_ready(self, _which: mock.Mock, _find_spec: mock.Mock) -> None:
        status = check_runtime_dependencies()

        self.assertTrue(status.ready)
        self.assertEqual(status.missing(), [])
        self.assertTrue(status.to_dict()["ready"])

    def test_format_dependency_error_includes_install_hints(self) -> None:
        status = RuntimeDependencyStatus(ffmpeg=False, ffprobe=False, whisperx=False)

        message = format_dependency_error(status)

        self.assertIn("ffmpeg", message)
        self.assertIn("ffprobe", message)
        self.assertIn("whisperx", message)
        self.assertIn("pip install whisperx", message)

    def test_dry_run_does_not_require_whisperx(self) -> None:
        status = RuntimeDependencyStatus(ffmpeg=True, ffprobe=True, whisperx=False)

        self.assertEqual(format_dependency_error(status, require_whisperx=False), "")


if __name__ == "__main__":
    unittest.main()
