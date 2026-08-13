import sys
import tempfile
import unittest
from pathlib import Path

from src.application_info import (
    DEVELOPMENT_VERSION,
    build_application_info_payload,
    resolve_application_info,
    resolve_application_version,
)


class ApplicationInfoTests(unittest.TestCase):
    def test_missing_version_file_uses_development_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertEqual(resolve_application_version(root), DEVELOPMENT_VERSION)

    def test_version_file_without_prefix_is_formatted_as_semver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "VERSION").write_text("0.2.8", encoding="utf-8")

            self.assertEqual(resolve_application_version(root), "v0.2.8")

    def test_version_file_with_prefix_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "VERSION").write_text("v0.2.9", encoding="utf-8")

            self.assertEqual(resolve_application_version(root), "v0.2.9")

    def test_application_info_contains_version_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "VERSION").write_text("v1.3.0", encoding="utf-8")

            info = resolve_application_info(root)
            payload = build_application_info_payload(root)

            self.assertEqual(info["version"], "v1.3.0")
            self.assertEqual(info["applicationPath"], str(root))
            self.assertEqual(info["executablePath"], str(Path(sys.executable).resolve()))
            self.assertIn("Version: v1.3.0", payload)
            self.assertIn("Executable:", payload)
            self.assertIn("Application path:", payload)


if __name__ == "__main__":
    unittest.main()
