import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.release_contract import (
    CHECKSUM_NAME,
    INSTALLER_NAME,
    MANIFEST_NAME,
    ReleaseContractError,
    validate_source_version,
    verify_release_artifacts,
)
from tests.workflow_contracts import (
    WorkflowContractError,
    build_job_graph,
    job_ancestors,
    load_workflow,
    step_by_id,
    validate_publish_gate,
    validate_publish_permissions,
    validate_step_order,
)


ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_ASSET_NAMES = {
    INSTALLER_NAME,
    CHECKSUM_NAME,
    MANIFEST_NAME,
}


class ReleaseDistributionTests(unittest.TestCase):
    def test_installer_is_per_user_and_omits_local_data(self) -> None:
        definition = (ROOT / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8-sig")

        self.assertIn(r"DefaultDirName={localappdata}\Programs\Subtitle Edit Bay", definition)
        self.assertIn("PrivilegesRequired=lowest", definition)
        self.assertIn('Excludes: "speaker_colors.json"', definition)
        self.assertIn(r'Excludes: "__pycache__\*,*\__pycache__\*,*.pyc,*.pyo"', definition)
        self.assertNotIn(r'Source: "{#SourceRoot}\.venv', definition)
        self.assertNotIn(r'Source: "{#SourceRoot}\.gui', definition)
        self.assertNotIn(r'Source: "{#SourceRoot}\video_import', definition)
        self.assertIn(r'Type: filesandordirs; Name: "{app}\.venv"', definition)
        self.assertNotIn(r'Type: filesandordirs; Name: "{app}\video_import"', definition)

    def test_installer_provides_setup_launch_update_and_uninstall_shortcuts(self) -> None:
        definition = (ROOT / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8-sig")
        launcher = (ROOT / "installer" / "launch.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("初回セットアップ・修復", definition)
        self.assertIn("アップデート", definition)
        self.assertIn("アンインストール", definition)
        self.assertIn("-WindowStyle Hidden", definition)
        self.assertIn(r".venv\Scripts\pythonw.exe", launcher)
        self.assertIn("latest-launch-error.log", launcher)
        self.assertIn(r'Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs"', launcher)
        self.assertNotIn(r'Join-Path $env:LOCALAPPDATA "SubtitleEditBay\logs"', launcher)
        self.assertIn("setup.bat", launcher)
        self.assertIn("Test-CudaRepairRequired", launcher)
        self.assertIn("torch.cuda.is_available()", launcher)
        self.assertIn("GPU環境の修復", launcher)

    def test_build_script_has_stable_release_contract(self) -> None:
        build = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("[string]$Version", build)
        self.assertIn("[string]$OutputPath", build)
        self.assertIn("SubtitleEditBay.iss", build)
        self.assertIn("INNO_SETUP_COMPILER", build)
        self.assertIn("OutputPath must end with .exe", build)
        self.assertIn("Inno Setup completed without producing", build)

    def test_release_workflow_has_safe_publish_graph_and_permissions(self) -> None:
        workflow = load_workflow(RELEASE_WORKFLOW)
        triggers = workflow["on"]

        self.assertIsInstance(triggers, dict)
        self.assertIn("v*", triggers["push"]["tags"])
        self.assertIn("workflow_dispatch", triggers)
        validate_publish_gate(
            workflow,
            publish_job="publish",
            required_gates=("test", "build"),
        )
        validate_publish_permissions(workflow, publish_job="publish")
        graph = build_job_graph(workflow)
        self.assertTrue({"test", "build"}.issubset(job_ancestors(graph, "publish")))

    def test_release_workflow_validates_source_and_artifacts_before_publish(self) -> None:
        workflow = load_workflow(RELEASE_WORKFLOW)

        validate_step_order(
            workflow,
            "build",
            ("release", "source-version", "package", "upload"),
        )
        validate_step_order(
            workflow,
            "publish",
            ("download", "verify", "release"),
        )

        source_validation = step_by_id(workflow, "build", "source-version")
        self.assertIn("scripts/release_contract.py validate-source", source_validation["run"])
        artifact_verification = step_by_id(workflow, "publish", "verify")
        self.assertIn("scripts/release_contract.py verify-artifacts", artifact_verification["run"])

        upload = step_by_id(workflow, "build", "upload")
        self.assertTrue(str(upload["uses"]).startswith("actions/upload-artifact@"))
        uploaded_paths = str(upload["with"]["path"])
        self.assertTrue(all(asset_name in uploaded_paths for asset_name in RELEASE_ASSET_NAMES))

        download = step_by_id(workflow, "publish", "download")
        self.assertTrue(str(download["uses"]).startswith("actions/download-artifact@"))
        release = step_by_id(workflow, "publish", "release")
        published_assets = str(release["run"])
        self.assertTrue(all(asset_name in published_assets for asset_name in RELEASE_ASSET_NAMES))

    def test_ci_cancels_only_superseded_automatic_runs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        expected_group = (
            "group: ci-${{ github.workflow }}-${{ github.event_name }}-"
            "${{ github.event_name == 'pull_request' && github.event.pull_request.number || "
            "github.event_name == 'workflow_dispatch' && github.run_id || github.ref }}"
        )

        self.assertIn("concurrency:", workflow)
        self.assertIn(expected_group, workflow)
        self.assertIn("cancel-in-progress: true", workflow)

    def test_local_release_artifacts_are_ignored(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("dist/", ignore.splitlines())

    def test_readme_links_to_latest_installer(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        releasing = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8")
        direct_url = "releases/latest/download/SubtitleEditBay-Setup.exe"

        self.assertIn(direct_url, readme)
        self.assertIn(direct_url, releasing)
        self.assertIn("vX.Y.Z", releasing)
        self.assertIn("公開済みのタグを削除・付け替えしない", releasing)


class WorkflowContractHelperTests(unittest.TestCase):
    def _transitive_release_workflow(self) -> dict[str, Any]:
        return {
            "on": {"push": {"tags": ["v*"]}},
            "jobs": {
                "test": {"runs-on": "ubuntu-latest", "permissions": {"contents": "read"}},
                "build": {"runs-on": "windows-latest", "permissions": {"contents": "read"}},
                "candidate": {"needs": ["test", "build"], "runs-on": "ubuntu-latest"},
                "publish": {
                    "needs": "candidate",
                    "runs-on": "ubuntu-latest",
                    "permissions": {"contents": "write"},
                },
            },
        }

    def test_loader_preserves_on_and_yaml_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "workflow.yml"
            workflow_path.write_text(
                "on:\n  push:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    continue-on-error: false\n",
                encoding="utf-8",
            )

            workflow = load_workflow(workflow_path)

        self.assertIn("on", workflow)
        self.assertNotIn(True, workflow)
        self.assertIs(workflow["jobs"]["test"]["continue-on-error"], False)

    def test_transitive_publish_dependencies_are_accepted(self) -> None:
        workflow = self._transitive_release_workflow()

        validate_publish_gate(
            workflow,
            publish_job="publish",
            required_gates=("test", "build"),
        )
        validate_publish_permissions(workflow, publish_job="publish")

    def test_missing_gate_continue_on_error_and_always_are_rejected(self) -> None:
        mutations = []

        missing_test = self._transitive_release_workflow()
        missing_test["jobs"]["candidate"]["needs"] = ["build"]
        mutations.append((missing_test, "does not depend on required gates: test"))

        missing_build = self._transitive_release_workflow()
        missing_build["jobs"]["candidate"]["needs"] = ["test"]
        mutations.append((missing_build, "does not depend on required gates: build"))

        continue_on_error = self._transitive_release_workflow()
        continue_on_error["jobs"]["test"]["continue-on-error"] = True
        mutations.append((continue_on_error, "enables continue-on-error: test"))

        step_continue_on_error = self._transitive_release_workflow()
        step_continue_on_error["jobs"]["test"]["steps"] = [
            {
                "id": "run-tests",
                "run": "python -m unittest",
                "continue-on-error": True,
            }
        ]
        mutations.append((step_continue_on_error, "step enables continue-on-error: test/run-tests"))

        unconditional_publish = self._transitive_release_workflow()
        unconditional_publish["jobs"]["publish"]["if"] = "${{ always() }}"
        mutations.append((unconditional_publish, "must not use always"))

        for workflow, message in mutations:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(
                    WorkflowContractError,
                    message,
                ),
            ):
                validate_publish_gate(
                    workflow,
                    publish_job="publish",
                    required_gates=("test", "build"),
                )

    def test_missing_dependency_and_cycle_are_rejected(self) -> None:
        missing_dependency = self._transitive_release_workflow()
        missing_dependency["jobs"]["candidate"]["needs"] = ["missing"]
        with self.assertRaisesRegex(WorkflowContractError, "needs missing job missing"):
            build_job_graph(missing_dependency)

        cycle = self._transitive_release_workflow()
        cycle["jobs"]["test"]["needs"] = "publish"
        with self.assertRaisesRegex(WorkflowContractError, "dependency cycle"):
            build_job_graph(cycle)

    def test_write_permission_and_missing_verification_step_are_rejected(self) -> None:
        excessive_permissions = self._transitive_release_workflow()
        excessive_permissions["jobs"]["build"]["permissions"] = {"contents": "write"}
        with self.assertRaisesRegex(WorkflowContractError, "non-publish job grants"):
            validate_publish_permissions(excessive_permissions, publish_job="publish")

        workflow = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        workflow["jobs"]["publish"]["steps"] = [
            step for step in workflow["jobs"]["publish"]["steps"] if step.get("id") != "verify"
        ]
        with self.assertRaisesRegex(WorkflowContractError, "missing required steps: verify"):
            validate_step_order(
                workflow,
                "publish",
                ("download", "verify", "release"),
            )


class ReleaseArtifactContractTests(unittest.TestCase):
    def _write_release_artifacts(self, directory: Path, version: str = "1.2.3") -> str:
        installer = directory / INSTALLER_NAME
        installer.write_bytes(b"deterministic installer bytes")
        digest = hashlib.sha256(installer.read_bytes()).hexdigest()
        (directory / CHECKSUM_NAME).write_text(
            f"{digest}  {INSTALLER_NAME}",
            encoding="ascii",
        )
        (directory / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "package_type": "installer",
                    "app_version": version,
                    "asset_name": INSTALLER_NAME,
                    "sha256": digest,
                    "required_files": [
                        "VERSION",
                        "scripts/launch.ps1",
                        "scripts/apply_installer_update.ps1",
                    ],
                }
            ),
            encoding="utf-8",
        )
        return digest

    def test_source_tag_must_match_repository_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            version_file = Path(temp_dir) / "VERSION"
            version_file.write_text("v1.2.3\n", encoding="utf-8")

            self.assertEqual(validate_source_version("v1.2.3", version_file), "1.2.3")
            with self.assertRaisesRegex(ReleaseContractError, "does not match VERSION"):
                validate_source_version("v1.2.4", version_file)

    def test_artifact_checksum_manifest_and_version_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            digest = self._write_release_artifacts(directory)

            self.assertEqual(verify_release_artifacts(directory, "1.2.3"), digest)

    def test_tampered_installer_and_wrong_manifest_version_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self._write_release_artifacts(directory)
            (directory / INSTALLER_NAME).write_bytes(b"tampered")
            with self.assertRaisesRegex(ReleaseContractError, "SHA-256 mismatch"):
                verify_release_artifacts(directory, "1.2.3")

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self._write_release_artifacts(directory, version="1.2.4")
            with self.assertRaisesRegex(ReleaseContractError, "app_version mismatch"):
                verify_release_artifacts(directory, "1.2.3")


if __name__ == "__main__":
    unittest.main()
