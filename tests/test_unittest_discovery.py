from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

from scripts.check_unittest_discovery import audit_test_modules


class UnittestDiscoveryCheckerTests(unittest.TestCase):
    def _audit_fixture(self, files: dict[str, str]):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        package_name = f"discovery_fixture_{uuid.uuid4().hex}"
        tests_dir = root / package_name
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")
        for relative_path, source in files.items():
            (tests_dir / relative_path).write_text(textwrap.dedent(source), encoding="utf-8")
        self.addCleanup(self._remove_modules, package_name)
        return audit_test_modules(tests_dir, package_name=package_name)

    @staticmethod
    def _remove_modules(package_name: str) -> None:
        for module_name in list(sys.modules):
            if module_name == package_name or module_name.startswith(f"{package_name}."):
                sys.modules.pop(module_name, None)

    def test_skipped_test_case_method_is_discovered(self) -> None:
        results = self._audit_fixture(
            {
                "test_valid.py": """
                    import unittest

                    class ValidTests(unittest.TestCase):
                        @unittest.skip("different platform")
                        def test_example(self):
                            self.assertTrue(True)
                """,
            }
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].discovered_count, 1)
        self.assertEqual(results[0].errors, ())

    def test_module_level_test_function_is_rejected(self) -> None:
        results = self._audit_fixture(
            {
                "test_function.py": """
                    def test_not_discovered():
                        assert True
                """,
            }
        )

        self.assertIn("module-level tests are not supported: test_not_discovered", results[0].errors)
        self.assertIn("unittest discovered 0 tests", results[0].errors)

    def test_zero_test_module_is_rejected(self) -> None:
        results = self._audit_fixture({"test_empty.py": "VALUE = 1\n"})

        self.assertEqual(results[0].discovered_count, 0)
        self.assertIn("unittest discovered 0 tests", results[0].errors)

    def test_import_error_is_rejected(self) -> None:
        results = self._audit_fixture({"test_broken.py": "raise RuntimeError('broken import')\n"})

        self.assertEqual(results[0].discovered_count, 0)
        self.assertTrue(
            any(error.startswith("import failed: RuntimeError: broken import") for error in results[0].errors)
        )

    def test_non_test_helper_is_ignored(self) -> None:
        results = self._audit_fixture(
            {
                "helper.py": "raise RuntimeError('must not be imported')\n",
                "test_valid.py": """
                    import unittest

                    class ValidTests(unittest.TestCase):
                        def test_example(self):
                            self.assertTrue(True)
                """,
            }
        )

        self.assertEqual([result.module_name.rsplit(".", 1)[-1] for result in results], ["test_valid"])
        self.assertEqual(results[0].errors, ())


if __name__ == "__main__":
    unittest.main()
