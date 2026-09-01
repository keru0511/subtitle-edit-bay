from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_TEST_SCRIPT = REPO_ROOT / "scripts" / "run_ci_tests.py"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_GROUPS_DOC = REPO_ROOT / "docs" / "CI_TEST_GROUPS.md"


def load_ci_test_module():
    spec = importlib.util.spec_from_file_location("run_ci_tests_under_test", CI_TEST_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {CI_TEST_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CI_TESTS = load_ci_test_module()


def create_manifest(modules: list[str] | None = None) -> dict[str, object]:
    groups = {
        group_name: {"modules": [], "selectors": []}
        for group_name in CI_TESTS.REQUIRED_GROUPS
    }
    groups["portable-unit"]["modules"] = list(modules or [])
    return {"schema_version": 1, "groups": groups}


class CiTestGroupManifestTests(unittest.TestCase):
    def test_repository_manifest_classifies_every_test_module_once(self) -> None:
        groups = CI_TESTS.load_manifest()

        classified = [
            module_name
            for group in groups.values()
            for module_name in group["modules"]
        ]
        discovered = CI_TESTS.discover_test_modules(REPO_ROOT / "tests")
        self.assertEqual(sorted(classified), discovered)
        self.assertEqual(len(classified), len(set(classified)))

    def test_unclassified_module_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tests_dir = Path(temp_dir)
            (tests_dir / "test_new_feature.py").write_text("", encoding="utf-8")

            with self.assertRaisesRegex(
                CI_TESTS.ManifestError,
                "unclassified test modules: test_new_feature",
            ):
                CI_TESTS.validate_manifest(create_manifest(), tests_dir)

    def test_module_assigned_to_multiple_groups_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tests_dir = Path(temp_dir)
            (tests_dir / "test_shared.py").write_text("", encoding="utf-8")
            manifest = create_manifest(["test_shared"])
            manifest["groups"]["qt-gui"]["modules"] = ["test_shared"]

            with self.assertRaisesRegex(
                CI_TESTS.ManifestError,
                "test module is assigned to multiple groups: test_shared",
            ):
                CI_TESTS.validate_manifest(manifest, tests_dir)

    def test_stale_manifest_module_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                CI_TESTS.ManifestError,
                "manifest modules not found on disk: test_removed",
            ):
                CI_TESTS.validate_manifest(
                    create_manifest(["test_removed"]),
                    Path(temp_dir),
                )

    def test_windows_ffmpeg_rerun_uses_an_explicit_selector(self) -> None:
        groups = CI_TESTS.load_manifest()

        self.assertEqual(
            groups["windows-ffmpeg-runtime"]["selectors"],
            [
                "tests.test_short_video_ass.ShortVideoRenderE2ETests."
                "test_project_renders_all_fits_crossfade_bgm_and_faststart_in_unicode_workspace"
            ],
        )
        self.assertIn("test_short_video_ass", groups["ffmpeg-runtime"]["modules"])

    def test_top_level_function_selector_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tests_dir = Path(temp_dir)
            (tests_dir / "test_shared.py").write_text("", encoding="utf-8")
            manifest = create_manifest(["test_shared"])
            manifest["groups"]["windows-runtime"]["selectors"] = [
                "tests.test_shared.test_windows_path"
            ]

            groups = CI_TESTS.validate_manifest(manifest, tests_dir)

        self.assertEqual(
            groups["windows-runtime"]["selectors"],
            ["tests.test_shared.test_windows_path"],
        )

    def test_selector_without_test_attribute_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tests_dir = Path(temp_dir)
            (tests_dir / "test_shared.py").write_text("", encoding="utf-8")
            manifest = create_manifest(["test_shared"])
            manifest["groups"]["windows-runtime"]["selectors"] = [
                "tests.test_shared"
            ]

            with self.assertRaisesRegex(
                CI_TESTS.ManifestError,
                "invalid unittest selector: tests.test_shared",
            ):
                CI_TESTS.validate_manifest(manifest, tests_dir)


class CiTestRunnerTests(unittest.TestCase):
    def test_top_level_test_functions_are_executed(self) -> None:
        module = ModuleType("synthetic_ci_test_module")
        exec(
            "executed = False\n"
            "def test_top_level():\n"
            "    global executed\n"
            "    executed = True\n",
            module.__dict__,
        )
        suite = unittest.TestSuite()

        CI_TESTS.add_top_level_test_functions(module, suite)
        result = unittest.TestResult()
        suite.run(result)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(module.executed)

    def test_top_level_function_selector_is_executed(self) -> None:
        module_name = "tests.test_synthetic_ci_selector"
        module = ModuleType(module_name)
        exec(
            "executed = False\n"
            "def test_selected():\n"
            "    global executed\n"
            "    executed = True\n",
            module.__dict__,
        )
        sys.modules[module_name] = module
        self.addCleanup(sys.modules.pop, module_name, None)

        suite = CI_TESTS.load_selector_suite(
            f"{module_name}.test_selected",
            unittest.TestLoader(),
        )
        result = unittest.TestResult()
        suite.run(result)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertTrue(module.executed)

    def test_summary_reports_skip_count_and_reason(self) -> None:
        test = unittest.FunctionTestCase(lambda: None)
        result = unittest.TestResult()
        result.startTest(test)
        result.addSkip(test, "runtime dependency unavailable")
        result.stopTest(test)

        summary = CI_TESTS._format_summary(["portable-unit"], result, 1.25)

        self.assertIn("| Tests run | 1 |", summary)
        self.assertIn("| Skipped | 1 |", summary)
        self.assertIn("1 × `runtime dependency unavailable`", summary)


class CiWorkflowContractTests(unittest.TestCase):
    def test_ci_uses_classified_groups_without_windows_full_discovery(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")

        for expected in (
            "portable-tests:",
            "--group portable-unit",
            "--group qt-gui",
            "--group ffmpeg-runtime",
            "--group windows-runtime",
            "--group windows-launcher-runtime",
            "--group windows-ffmpeg-runtime",
            "--group ffmpeg6-compat",
            "Start GUI on Windows",
            "windows-launcher-tests:",
            "QT_FFMPEG_DECODING_HW_DEVICE_TYPES: \",\"",
            "actions/cache/restore@v5",
            "actions/cache/save@v5",
            "windows-ffmpeg-9.0.1-v1",
            "windows-installer-smoke:",
        ):
            self.assertIn(expected, workflow)
        self.assertNotIn(
            'python -m unittest discover -s tests -p "test_*.py" -v',
            workflow,
        )

    def test_group_documentation_explains_ownership_and_reruns(self) -> None:
        documentation = CI_GROUPS_DOC.read_text(encoding="utf-8")

        for expected in (
            "portable-unit",
            "windows-runtime",
            "qt-gui",
            "ffmpeg-runtime",
            "selectors",
            "--validate",
        ):
            self.assertIn(expected, documentation)


if __name__ == "__main__":
    unittest.main()
