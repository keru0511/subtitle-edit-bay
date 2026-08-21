from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.application_logging import ApplicationLogger, redact_text


class ApplicationLoggingTests(unittest.TestCase):
    def test_redacts_credentials_and_optional_local_paths(self) -> None:
        value = "token=secret-value Authorization: Bearer private-token C:\\Users\\name\\video.mkv"

        self.assertNotIn("secret-value", redact_text(value))
        self.assertNotIn("private-token", redact_text(value))
        self.assertIn("<local-path>", redact_text(value, paths=True))

    def test_redacts_json_credentials_and_paths_containing_spaces(self) -> None:
        value = (
            '{"access_token":"json-secret","password": "json-password", '
            '"authorization":"Bearer json-bearer"} '
            r'C:\Users\Alice\My Projects\video.mkv '
            "/home/Alice/My Projects/clip.mp4"
        )

        redacted = redact_text(value, paths=True)

        for secret in ("json-secret", "json-password", "json-bearer"):
            self.assertNotIn(secret, redacted)
        self.assertNotIn("My Projects", redacted)
        self.assertNotIn("C:\\Users\\Alice", redacted)
        self.assertNotIn("/home/Alice", redacted)

    def test_structured_file_and_bounded_memory_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ApplicationLogger(
                temp_dir,
                log_directory=Path(temp_dir) / "logs",
                max_memory_chars=1_000,
            )
            logger.append(
                "render started",
                component="ffmpeg",
                job="render",
                stage="ENCODE",
                process_id=42,
            )
            logger.append("token=do-not-store " + ("x" * 2_000), severity="ERROR")

            self.assertLessEqual(len(logger.text), 1_000)
            records = [
                json.loads(line)
                for line in logger.log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["component"], "ffmpeg")
            self.assertEqual(records[0]["process_id"], 42)
            self.assertNotIn("do-not-store", logger.log_path.read_text(encoding="utf-8"))

    def test_diagnostic_contains_required_context_without_paths_or_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ApplicationLogger(
                temp_dir,
                log_directory=Path(temp_dir) / "logs",
                application_info={"version": "1.2.3", "distribution": "installer"},
            )
            logger.append("Authorization: Bearer hidden C:\\private\\input.mkv")

            diagnostic = logger.diagnostic_text(
                status="処理に失敗しました",
                stage="ENCODE",
                exit_code=7,
                runtime={"ffmpeg": "7.1", "input": "C:\\private\\input.mkv"},
            )

            self.assertIn("1.2.3", diagnostic)
            self.assertIn("ENCODE", diagnostic)
            self.assertIn("終了コード: 7", diagnostic)
            self.assertIn("完全ログ:", diagnostic)
            self.assertIn("完全ログ: <local-path>", diagnostic)
            self.assertNotIn("hidden", diagnostic)
            self.assertNotIn("C:\\private", diagnostic)
            self.assertNotIn(str(Path(temp_dir)), diagnostic)


if __name__ == "__main__":
    unittest.main()
