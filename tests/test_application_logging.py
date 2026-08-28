from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.application_logging import (
    ApplicationLogger,
    ProcessDiagnosticSnapshot,
    default_log_directory,
    redact_text,
)


class ApplicationLoggingTests(unittest.TestCase):
    def test_default_directory_matches_installer_launcher_contract(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/LocalAppData"}):
            self.assertEqual(
                default_log_directory("C:/workspace"),
                Path("C:/LocalAppData") / "Subtitle Edit Bay" / "logs",
            )

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

    def test_redacts_full_authorization_header_and_quoted_secret_with_spaces(self) -> None:
        value = (
            "Authorization: Basic Zm9vOmJhcg==\n"
            'password="two words"\n'
            "token=url-secret&next=ok"
        )

        redacted = redact_text(value)

        for secret in ("Zm9vOmJhcg==", "two words", "url-secret"):
            self.assertNotIn(secret, redacted)
        self.assertIn("next=ok", redacted)

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

    def test_file_write_failure_keeps_in_memory_log_and_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            blocked_log_directory = Path(temp_dir) / "logs"
            blocked_log_directory.write_text("not a directory", encoding="utf-8")
            logger = ApplicationLogger(temp_dir, log_directory=blocked_log_directory)

            logger.append("memory fallback")

            self.assertIn("memory fallback", logger.text)
            self.assertTrue(logger.write_error)

    def test_structured_file_rotates_without_losing_boundary_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ApplicationLogger(
                temp_dir,
                log_directory=Path(temp_dir) / "logs",
                max_file_bytes=1_024,
            )
            logger.append("first " + ("a" * 700))
            logger.append("second " + ("b" * 700))
            logger.append("third")

            rotated = logger.log_path.with_suffix(".1.jsonl")
            self.assertTrue(rotated.is_file())
            combined = rotated.read_text(encoding="utf-8") + logger.log_path.read_text(
                encoding="utf-8"
            )
            for marker in ("first", "second", "third"):
                self.assertIn(marker, combined)

    def test_old_session_logs_are_removed_during_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_directory = Path(temp_dir) / "logs"
            log_directory.mkdir()
            old_log = log_directory / "session-old.jsonl"
            old_log.write_text("{}\n", encoding="utf-8")
            old_timestamp = time.time() - (3 * 24 * 60 * 60)
            os.utime(old_log, (old_timestamp, old_timestamp))

            ApplicationLogger(
                temp_dir,
                log_directory=log_directory,
                retention_days=1,
            )

            self.assertFalse(old_log.exists())

    def test_preserved_system_entries_survive_large_process_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ApplicationLogger(
                temp_dir,
                log_directory=Path(temp_dir) / "logs",
                max_memory_chars=1_000,
            )
            logger.append(
                "startup diagnostics",
                component="startup",
                stage="STARTUP",
                preserve_in_memory=True,
                pin_in_memory=True,
            )
            logger.append("process output " + ("x" * 2_000), component="render")

            self.assertIn("startup diagnostics", logger.text)
            self.assertNotIn("process output", logger.text)
            self.assertLessEqual(len(logger.text), 1_000)

    def test_pinned_startup_entry_survives_preserved_status_flood(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ApplicationLogger(
                temp_dir,
                log_directory=Path(temp_dir) / "logs",
                max_memory_chars=1_000,
            )
            logger.append(
                "startup diagnostics",
                component="startup",
                stage="STARTUP",
                preserve_in_memory=True,
                pin_in_memory=True,
            )
            for index in range(80):
                logger.append(
                    f"GUI state {index:03d} " + ("x" * 80),
                    component="gui",
                    preserve_in_memory=True,
                )

            self.assertIn("startup diagnostics", logger.text)
            self.assertNotIn("GUI state 000", logger.text)
            self.assertIn("GUI state 079", logger.text)
            self.assertLessEqual(len(logger.text), 1_000)

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

    def test_diagnostic_snapshot_preserves_process_result_and_related_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = ApplicationLogger(
                temp_dir,
                log_directory=Path(temp_dir) / "logs",
                application_info={"version": "v1.2.3", "distribution": "installer"},
            )
            logger.append("newer GUI status")
            snapshot = ProcessDiagnosticSnapshot(
                occurred_at="2026-08-21T22:19:34+09:00",
                job="transcribe",
                component="transcribe",
                stage="ERROR",
                status="処理が終了しました（終了コード 7）",
                outcome="failed",
                exit_code=7,
                process_error="process crashed",
                log_text=r"WhisperX failed token=private C:\private\audio.wav",
                related_log_tail="CUDA out of memory",
                runtime={"pytorch": "2.8.0+cu128", "cuda_available": True},
            )

            diagnostic = logger.diagnostic_text(snapshot=snapshot)

            self.assertIn("2026-08-21T22:19:34+09:00", diagnostic)
            self.assertIn("配布形態: installer", diagnostic)
            self.assertIn("job: transcribe", diagnostic)
            self.assertIn("結果: 異常終了 (failed)", diagnostic)
            self.assertIn("終了コード: 7", diagnostic)
            self.assertIn("QProcessエラー: process crashed", diagnostic)
            self.assertIn("CUDA out of memory", diagnostic)
            self.assertNotIn("newer GUI status", diagnostic)
            self.assertNotIn("private", diagnostic)
            self.assertNotIn(r"C:\private", diagnostic)


if __name__ == "__main__":
    unittest.main()
