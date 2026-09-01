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
    validate_step_command,
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
            adjacent_pairs=(("package", "upload"),),
        )
        validate_step_order(
            workflow,
            "publish",
            ("download", "verify", "release"),
            adjacent_pairs=(("verify", "release"),),
        )

        validate_step_command(
            workflow,
            "build",
            "source-version",
            expected_shell="pwsh",
            expected_tokens=(
                "python",
                "scripts/release_contract.py",
                "validate-source",
                "--tag",
                "${{ steps.release.outputs.tag }}",
                "--version-file",
                "VERSION",
            ),
        )
        validate_step_command(
            workflow,
            "publish",
            "verify",
            expected_shell="bash",
            expected_tokens=(
                "python",
                "scripts/release_contract.py",
                "verify-artifacts",
                "--directory",
                "dist",
                "--expected-version",
                "${{ needs.build.outputs.version }}",
            ),
        )

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
            "permissions": {"contents": "read"},
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
        workflow["jobs"]["publish"]["if"] = "${{ success() }}"
        workflow["jobs"]["candidate"]["continue-on-error"] = "${{   false   }}"

        validate_publish_gate(
            workflow,
            publish_job="publish",
            required_gates=("test", "build"),
        )
        validate_publish_permissions(workflow, publish_job="publish")

    def test_missing_gate_continue_on_error_and_unsafe_conditions_are_rejected(self) -> None:
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

        for condition in (
            "${{ always() }}",
            "${{ !success() }}",
            "${{ success() || true }}",
        ):
            unconditional_publish = self._transitive_release_workflow()
            unconditional_publish["jobs"]["publish"]["if"] = condition
            mutations.append((unconditional_publish, "must use the default success condition"))

        transitive_bypass = self._transitive_release_workflow()
        transitive_bypass["jobs"]["candidate"]["if"] = "${{ always() }}"
        mutations.append((transitive_bypass, "dependency job must use the default success condition"))

        transitive_continue_on_error = self._transitive_release_workflow()
        transitive_continue_on_error["jobs"]["candidate"]["continue-on-error"] = True
        mutations.append((transitive_continue_on_error, "job enables continue-on-error: candidate"))

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
        with self.assertRaisesRegex(WorkflowContractError, "non-publish job grants write"):
            validate_publish_permissions(excessive_permissions, publish_job="publish")

        excessive_publish_permissions = self._transitive_release_workflow()
        excessive_publish_permissions["jobs"]["publish"]["permissions"]["packages"] = "write"
        with self.assertRaisesRegex(WorkflowContractError, "unexpected write"):
            validate_publish_permissions(
                excessive_publish_permissions,
                publish_job="publish",
            )

        implicit_permissions = self._transitive_release_workflow()
        del implicit_permissions["permissions"]
        del implicit_permissions["jobs"]["build"]["permissions"]
        with self.assertRaisesRegex(WorkflowContractError, "inherits an implicit"):
            validate_publish_permissions(implicit_permissions, publish_job="publish")

        global_write = self._transitive_release_workflow()
        global_write["permissions"] = {"contents": "write"}
        with self.assertRaisesRegex(WorkflowContractError, "scoped to the publish job"):
            validate_publish_permissions(global_write, publish_job="publish")

        global_other_write = self._transitive_release_workflow()
        global_other_write["permissions"] = {"contents": "read", "actions": "write"}
        with self.assertRaisesRegex(WorkflowContractError, "workflow must not grant write"):
            validate_publish_permissions(global_other_write, publish_job="publish")

        malformed_permissions = self._transitive_release_workflow()
        malformed_permissions["permissions"] = {"contents": ["read"]}
        with self.assertRaisesRegex(WorkflowContractError, "invalid permission entries"):
            validate_publish_permissions(malformed_permissions, publish_job="publish")

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

    def test_skipped_verification_and_failure_publish_are_rejected(self) -> None:
        mutations = []

        skipped_verification = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(skipped_verification, "publish", "verify")["if"] = "${{ false }}"
        mutations.append((skipped_verification, "publish/verify"))

        failure_publish = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(failure_publish, "publish", "release")["if"] = "${{ failure() }}"
        mutations.append((failure_publish, "publish/release"))

        skipped_source_validation = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(skipped_source_validation, "build", "source-version")["if"] = False
        mutations.append((skipped_source_validation, "build/source-version"))

        for workflow, location in mutations:
            with (
                self.subTest(location=location),
                self.assertRaisesRegex(
                    WorkflowContractError,
                    location,
                ),
            ):
                if location.startswith("build/"):
                    validate_step_order(
                        workflow,
                        "build",
                        ("release", "source-version", "package", "upload"),
                        adjacent_pairs=(("package", "upload"),),
                    )
                else:
                    validate_step_order(
                        workflow,
                        "publish",
                        ("download", "verify", "release"),
                        adjacent_pairs=(("verify", "release"),),
                    )

    def test_steps_cannot_mutate_artifacts_after_validation(self) -> None:
        mutations = []

        before_upload = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        upload_index = before_upload["jobs"]["build"]["steps"].index(step_by_id(before_upload, "build", "upload"))
        before_upload["jobs"]["build"]["steps"].insert(
            upload_index,
            {"id": "mutate-package", "run": "echo mutate package"},
        )
        mutations.append(
            (
                before_upload,
                "build",
                ("release", "source-version", "package", "upload"),
                (("package", "upload"),),
            )
        )

        before_release = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        release_index = before_release["jobs"]["publish"]["steps"].index(
            step_by_id(before_release, "publish", "release")
        )
        before_release["jobs"]["publish"]["steps"].insert(
            release_index,
            {"id": "mutate-verified-artifact", "run": "echo mutate artifact"},
        )
        mutations.append(
            (
                before_release,
                "publish",
                ("download", "verify", "release"),
                (("verify", "release"),),
            )
        )

        for workflow, job_id, required_steps, adjacent_pairs in mutations:
            with (
                self.subTest(job_id=job_id),
                self.assertRaisesRegex(WorkflowContractError, "requires adjacent steps"),
            ):
                validate_step_order(
                    workflow,
                    job_id,
                    required_steps,
                    adjacent_pairs=adjacent_pairs,
                )

    def test_release_contract_commands_cannot_mask_failures_or_change_inputs(self) -> None:
        mutations = []

        masked_source_validation = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(masked_source_validation, "build", "source-version")["run"] += "; exit 0"
        mutations.append((masked_source_validation, "build", "source-version", "pwsh"))

        wrong_artifact_version = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(wrong_artifact_version, "publish", "verify")["run"] = (
            "python scripts/release_contract.py verify-artifacts --directory dist --expected-version 0.0.0"
        )
        mutations.append((wrong_artifact_version, "publish", "verify", "bash"))

        custom_shell = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(custom_shell, "publish", "verify")["shell"] = "bash {0}"
        mutations.append((custom_shell, "publish", "verify", "bash"))

        expected_tokens = {
            "source-version": (
                "python",
                "scripts/release_contract.py",
                "validate-source",
                "--tag",
                "${{ steps.release.outputs.tag }}",
                "--version-file",
                "VERSION",
            ),
            "verify": (
                "python",
                "scripts/release_contract.py",
                "verify-artifacts",
                "--directory",
                "dist",
                "--expected-version",
                "${{ needs.build.outputs.version }}",
            ),
        }
        for workflow, job_id, step_id, shell in mutations:
            with self.subTest(job_id=job_id, step_id=step_id), self.assertRaises(WorkflowContractError):
                validate_step_command(
                    workflow,
                    job_id,
                    step_id,
                    expected_shell=shell,
                    expected_tokens=expected_tokens[step_id],
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

    def _rewrite_manifest(self, directory: Path, **changes: object) -> None:
        manifest_path = directory / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(changes)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

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

    def test_every_manifest_contract_field_is_verified(self) -> None:
        mutations = (
            ({"schema_version": 2}, "schema_version mismatch"),
            ({"package_type": "archive"}, "package_type mismatch"),
            ({"asset_name": "other.exe"}, "asset_name mismatch"),
            ({"sha256": "0" * 64}, "sha256 mismatch"),
            ({"required_files": ["VERSION"]}, "required_files is incomplete"),
        )

        for changes, message in mutations:
            with tempfile.TemporaryDirectory() as temp_dir, self.subTest(changes=changes):
                directory = Path(temp_dir)
                self._write_release_artifacts(directory)
                self._rewrite_manifest(directory, **changes)
                with self.assertRaisesRegex(ReleaseContractError, message):
                    verify_release_artifacts(directory, "1.2.3")

    def test_installer_checksum_and_manifest_are_all_required(self) -> None:
        for asset_name in RELEASE_ASSET_NAMES:
            with tempfile.TemporaryDirectory() as temp_dir, self.subTest(asset_name=asset_name):
                directory = Path(temp_dir)
                self._write_release_artifacts(directory)
                (directory / asset_name).unlink()
                with self.assertRaisesRegex(ReleaseContractError, "artifacts are missing"):
                    verify_release_artifacts(directory, "1.2.3")


if __name__ == "__main__":
    unittest.main()
