from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from src.codex_runtime import classify_distribution, redact_codex_diagnostic
from tests.typed_case import TypedTestCase


class CodexRuntimeTests(TypedTestCase):
    def test_distribution_classification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(classify_distribution(root), "zip")
            (root / "SubtitleEditBay.exe").touch()
            self.assertEqual(classify_distribution(root), "installer")
            (root / ".git").mkdir()
            self.assertEqual(classify_distribution(root), "git")

    def test_diagnostic_redacts_secret_and_local_path(self) -> None:
        redacted = redact_codex_diagnostic("token=secret C:\\Users\\name\\project.json Bearer private")
        self.assertNotIn("secret", redacted)
        self.assertNotIn("private", redacted)
        self.assertIn("<local-path>", redacted)

    def test_diagnostic_redacts_json_credentials_and_paths_with_spaces(self) -> None:
        value = (
            '{"access_token":"json-secret","authorization":"Bearer json-bearer"} '
            r"C:\Users\Alice\My Projects\codex.json "
            "/home/Alice/My Projects/codex.json"
        )
        redacted = redact_codex_diagnostic(value)
        for secret in ("json-secret", "json-bearer"):
            self.assertNotIn(secret, redacted)
        self.assertNotIn("My Projects", redacted)
        self.assertNotIn("C:\\Users\\Alice", redacted)
        self.assertNotIn("/home/Alice", redacted)

    def test_diagnostic_redacts_basic_authorization_and_quoted_password(self) -> None:
        redacted = redact_codex_diagnostic('Authorization: Basic Zm9vOmJhcg== password="two words"')

        self.assertNotIn("Zm9vOmJhcg==", redacted)
        self.assertNotIn("two words", redacted)


if __name__ == "__main__":
    unittest.main()
