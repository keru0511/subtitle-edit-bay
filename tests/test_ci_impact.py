"""CIの選択漏れと意図しないスキップを検証する。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.ci_impact import JOBS, changed_paths, main, plan_changes
from scripts.release_readiness import ReleaseReadinessError, assert_ci_validation_results


class CiImpactTests(unittest.TestCase):
    def test_documentation_avoids_expensive_jobs(self):
        plan = plan_changes(["README.md", "docs/CI_TEST_GROUPS.md", "AGENTS.md"])
        self.assertEqual([job for job, enabled in plan.items() if enabled], ["python-quality"])

    def test_application_keeps_runtime_checks_but_avoids_installer(self):
        plan = plan_changes(["src/transcription_context.py"])
        self.assertTrue(all(plan[job] for job in JOBS if job != "windows-installer-smoke"))
        self.assertFalse(plan["windows-installer-smoke"])

    def test_unknown_and_shared_changes_run_everything(self):
        for path in [
            "new-tool.toml",
            "tests/helpers.py",
            "tests/test_deleted.py",
            "scripts/setup.ps1",
            "requirements.txt",
            "VERSION",
            ".github/workflows/ci.yml",
            "installer/launch.ps1",
            "launcher/SubtitleEditBayLauncher.c",
            "runtime/runtime-contract.json",
            "docs/helper.py",
        ]:
            with self.subTest(path=path):
                self.assertTrue(all(plan_changes([path]).values()))

    def test_test_group_mapping_includes_cross_platform_selectors(self):
        plan = plan_changes(["tests/test_audio_mix_semantic_e2e.py"])
        self.assertTrue(plan["portable-tests"])
        self.assertTrue(plan["windows-tests"])
        self.assertFalse(plan["windows-installer-smoke"])
        self.assertFalse(plan["windows-launcher-tests"])
        plan = plan_changes(["tests/test_installer_migration.py"])
        self.assertTrue(plan["portable-tests"])
        self.assertTrue(plan["windows-launcher-tests"])

    def test_multiple_areas_are_combined(self):
        plan = plan_changes(["docs/USAGE.md", "tests/test_transcription_context.py", "tests/test_windows_launchers.py"])
        self.assertTrue(plan["portable-tests"])
        self.assertTrue(plan["windows-launcher-tests"])
        self.assertFalse(plan["windows-tests"])

    def test_manual_run_and_missing_push_base_run_everything(self):
        for event, base in [("workflow_dispatch", "abc"), ("schedule", "abc"), ("push", "0" * 40), ("push", "")]:
            with self.subTest(event=event, base=base), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                with patch(
                    "sys.argv",
                    [
                        "ci_impact",
                        "--source-sha",
                        "HEAD",
                        "--base-sha",
                        base,
                        "--event-name",
                        event,
                        "--github-output",
                        str(output),
                    ],
                ):
                    main()
                lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertTrue(all(json.loads(lines["impact_plan"]).values()))
                self.assertEqual(lines["codeql"], "true")

    def test_unavailable_diff_falls_back_to_full_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with (
                patch("scripts.ci_impact.changed_paths", side_effect=subprocess.CalledProcessError(128, "git")),
                patch(
                    "sys.argv",
                    [
                        "ci_impact",
                        "--source-sha",
                        "HEAD",
                        "--base-sha",
                        "missing",
                        "--event-name",
                        "push",
                        "--github-output",
                        str(output),
                    ],
                ),
            ):
                main()
            lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertTrue(all(json.loads(lines["impact_plan"]).values()))

    def test_git_rename_includes_both_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

            git("init")
            git("config", "user.name", "CIテスト")
            git("config", "user.email", "ci@example.invalid")
            (root / "old.py").write_text("example")
            git("add", ".")
            git("commit", "-m", "初期状態")
            (root / "old.py").rename(root / "new.py")
            git("add", ".")
            git("commit", "-m", "改名")
            with patch("scripts.ci_impact.ROOT", root):
                self.assertEqual(set(changed_paths("HEAD^", "HEAD")), {"old.py", "new.py"})

    def test_gate_accepts_only_planned_skips(self):
        plan = plan_changes(["README.md"])
        required = ["success", "skipped", "skipped", "skipped"]
        delegated = ["skipped", "skipped"]
        assert_ci_validation_results(False, False, "success", required, delegated, plan)
        for status in ["failure", "cancelled", "skipped"]:
            with self.subTest(status=status), self.assertRaises(ReleaseReadinessError):
                assert_ci_validation_results(False, False, "success", [status, *required[1:]], delegated, plan)
        for status in ["failure", "cancelled"]:
            with self.subTest(status=status), self.assertRaises(ReleaseReadinessError):
                assert_ci_validation_results(False, False, status, required, delegated, plan)
        with self.assertRaises(ReleaseReadinessError):
            assert_ci_validation_results(
                False, False, "success", ["success"] * 4, ["skipped"] * 2, plan_changes(["src/gui.py"])
            )

    def test_release_preparation_cannot_use_reduced_plan(self):
        with self.assertRaises(ReleaseReadinessError):
            assert_ci_validation_results(
                True, True, "success", ["success"] * 4, ["skipped"] * 2, plan_changes(["README.md"])
            )
        assert_ci_validation_results(True, True, "success", ["success"] * 4, ["skipped"] * 2, plan_changes(["VERSION"]))

    def test_invalid_plan_is_rejected(self):
        for plan in [{}, dict.fromkeys(JOBS, "false"), dict.fromkeys(JOBS, False)]:
            with self.subTest(plan=plan), self.assertRaises(ReleaseReadinessError):
                assert_ci_validation_results(False, False, "success", ["success"] * 4, ["success"] * 2, plan)

    def test_each_selected_job_rejects_failure_cancellation_and_skip(self):
        for paths in [["README.md"], ["src/gui.py"], ["VERSION"], ["tests/test_windows_launchers.py"]]:
            plan = plan_changes(paths)
            expected = ["success" if plan[job] else "skipped" for job in JOBS]
            assert_ci_validation_results(False, False, "success", expected[:4], expected[4:], plan)
            for index, job in enumerate(JOBS):
                if not plan[job]:
                    continue
                for status in ["failure", "cancelled", "skipped"]:
                    actual = expected.copy()
                    actual[index] = status
                    with self.subTest(paths=paths, job=job, status=status), self.assertRaises(ReleaseReadinessError):
                        assert_ci_validation_results(False, False, "success", actual[:4], actual[4:], plan)

    def test_pr_and_push_use_the_supplied_diff_and_emit_skips(self):
        for event in ["pull_request", "push"]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                with (
                    patch("scripts.ci_impact.changed_paths", return_value=["README.md"]) as diff,
                    patch(
                        "sys.argv",
                        [
                            "ci_impact",
                            "--source-sha",
                            "candidate",
                            "--base-sha",
                            "previous",
                            "--event-name",
                            event,
                            "--github-output",
                            str(output),
                        ],
                    ),
                ):
                    main()
                diff.assert_called_once_with("previous", "candidate")
                lines = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertEqual(lines["codeql"], "false")
                self.assertEqual(lines["windows-installer-smoke"], "false")
                self.assertEqual(json.loads(lines["impact_plan"]), plan_changes(["README.md"]))
