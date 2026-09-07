import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

from scripts.release_contract import (
    CHECKSUM_NAME,
    INSTALLER_NAME,
    MANIFEST_NAME,
    PREPARATION_NAME,
    ReleaseContractError,
    validate_source_version,
    verify_release_artifacts,
)
from scripts.release_readiness import (
    ReleaseReadinessError,
    assert_preparation_results,
    assert_readiness_result,
    classify_changes,
    classify_values,
)
from scripts.release_state import (
    GitHubReleaseState,
    ReleaseStateError,
    git_tag_exists,
    github_release_state,
    publication_action,
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
RELEASE_REQUEST_WORKFLOW = ROOT / ".github" / "workflows" / "release-request.yml"
RELEASE_PREPARE_WORKFLOW = ROOT / ".github" / "workflows" / "release-prepare.yml"
RELEASE_READINESS_WORKFLOW = ROOT / ".github" / "workflows" / "release-readiness.yml"
RELEASE_ASSET_NAMES = {
    INSTALLER_NAME,
    CHECKSUM_NAME,
    MANIFEST_NAME,
    PREPARATION_NAME,
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
        self.assertNotIn("skipifsourcedoesntexist", definition)
        self.assertNotIn("LauncherExecutable", definition)
        self.assertIn(r'Filename: "{app}\SubtitleEditBayLauncher.exe"', definition)
        self.assertIn('Parameters: "--setup"', definition)
        self.assertIn('Parameters: "--update"', definition)
        self.assertNotIn(r'Filename: "{app}\setup.bat"', definition)
        self.assertNotIn(r'Filename: "{app}\update.bat"', definition)
        self.assertIn(r".venv\Scripts\pythonw.exe", launcher)
        self.assertIn("latest-launch-error.log", launcher)
        self.assertIn(r'Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs"', launcher)
        self.assertNotIn(r'Join-Path $env:LOCALAPPDATA "SubtitleEditBay\logs"', launcher)
        self.assertIn("setup.bat", launcher)
        self.assertIn("Test-CudaRepairRequired", launcher)
        self.assertIn("torch.cuda.is_available()", launcher)
        self.assertIn("GPU環境の修復", launcher)
        self.assertIn("Test-SetupComplete", launcher)
        self.assertIn("setup-status.json", launcher)
        self.assertIn('[string]$status.status -eq "success"', launcher)

        setup = (ROOT / "scripts" / "setup.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('Write-SetupStatus -Status "running"', setup)
        self.assertIn('Write-SetupStatus -Status "failed"', setup)
        self.assertIn('Write-SetupStatus -Status "success"', setup)

    def test_build_script_has_stable_release_contract(self) -> None:
        build = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8-sig")
        package = (ROOT / "scripts" / "build_release_package.ps1").read_text(encoding="utf-8-sig")
        smoke = (ROOT / "scripts" / "test_installer.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("[string]$Version", build)
        self.assertIn("[string]$OutputPath", build)
        self.assertIn("SubtitleEditBay.iss", build)
        self.assertIn("INNO_SETUP_COMPILER", build)
        self.assertIn("OutputPath must end with .exe", build)
        self.assertIn("Inno Setup completed without producing", build)
        self.assertIn("[string]$ProjectRoot", build)
        self.assertIn("source_sha", package)
        self.assertIn("release-preparation.json", package)
        self.assertIn("$ExpectedVersion.Substring(1)", smoke)
        self.assertIn("Installed VERSION mismatch", smoke)
        self.assertIn('"SubtitleEditBayLauncher.exe"', smoke)
        self.assertIn('Start-Process -FilePath $launcher', smoke)
        self.assertIn('"--probe-setup"', smoke)
        self.assertNotIn("installed-gui-smoke.py", smoke)

    def test_installer_requires_x64_native_launcher(self) -> None:
        build = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8-sig")
        launcher_build = (ROOT / "scripts" / "build_launcher.ps1").read_text(encoding="utf-8-sig")
        manifest_build = (ROOT / "scripts" / "build_release_package.ps1").read_text(encoding="utf-8-sig")

        self.assertNotIn("AllowMissingCompiler", build)
        self.assertNotIn("AllowMissingCompiler", launcher_build)
        self.assertIn("/MACHINE:X64", launcher_build)
        self.assertIn("Launcher build did not produce the required executable", build)
        self.assertIn('"SubtitleEditBayLauncher.exe"', manifest_build)

    def test_release_workflow_has_safe_publish_graph_and_permissions(self) -> None:
        workflow = load_workflow(RELEASE_WORKFLOW)
        triggers = workflow["on"]

        self.assertEqual(set(triggers), {"workflow_call"})
        self.assertEqual(set(triggers["workflow_call"]["inputs"]), {"source_sha", "release_version"})
        validate_publish_gate(
            workflow,
            publish_job="publish",
            required_gates=("prepare", "tag"),
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["tag"]["permissions"], {"contents": "write"})
        self.assertEqual(
            workflow["jobs"]["publish"]["permissions"],
            {"actions": "read", "contents": "write"},
        )
        graph = build_job_graph(workflow)
        self.assertTrue({"prepare", "tag"}.issubset(job_ancestors(graph, "publish")))
        self.assertEqual(workflow["jobs"]["prepare"]["uses"], "./.github/workflows/release-prepare.yml")

    def test_release_entrypoint_passes_exact_source_and_version(self) -> None:
        release = load_workflow(RELEASE_WORKFLOW)
        request = load_workflow(RELEASE_REQUEST_WORKFLOW)
        reusable_release = request["jobs"]["release"]

        self.assertEqual(
            release["concurrency"]["group"],
            "release-${{ inputs.release_version }}-${{ inputs.source_sha }}",
        )
        self.assertEqual(reusable_release["uses"], "./.github/workflows/release.yml")
        self.assertEqual(reusable_release["with"]["source_sha"], "${{ needs.validate.outputs.source_sha }}")
        self.assertEqual(
            reusable_release["with"]["release_version"],
            "${{ needs.validate.outputs.release_version }}",
        )
        self.assertFalse(
            (ROOT / ".github" / "workflows" / "release-tag.yml").exists(),
            "direct tag push must not be a publishing entrypoint",
        )

    def test_release_workflow_validates_source_and_artifacts_before_publish(self) -> None:
        workflow = load_workflow(RELEASE_WORKFLOW)
        preparation = load_workflow(RELEASE_PREPARE_WORKFLOW)

        validate_step_order(
            preparation,
            "build",
            ("upload",),
        )
        upload = step_by_id(preparation, "build", "upload")
        self.assertTrue(str(upload["uses"]).startswith("actions/upload-artifact@"))
        uploaded_paths = str(upload["with"]["path"])
        self.assertTrue(all(asset_name in uploaded_paths for asset_name in RELEASE_ASSET_NAMES))
        published_assets = str(step_by_id(workflow, "publish", "release")["run"])
        self.assertTrue(all(asset_name in published_assets for asset_name in RELEASE_ASSET_NAMES))
        self.assertNotIn("--clobber", published_assets)
        self.assertIn("cmp dist/SubtitleEditBay-Setup.exe.sha256", published_assets)

    def test_existing_release_must_be_published_or_a_verified_draft(self) -> None:
        workflow = load_workflow(RELEASE_WORKFLOW)
        publish = workflow["jobs"]["publish"]
        state = step_by_id(workflow, "publish", "state")
        release = step_by_id(workflow, "publish", "release")
        published = step_by_id(workflow, "publish", "published")
        command = str(release["run"])

        self.assertIn("release_state.py inspect-release", str(state["run"]))
        self.assertIn('steps.state.outputs.action }}" == "reuse"', command)
        self.assertIn('steps.state.outputs.action }}" == "publish-draft"', command)
        self.assertIn('gh release edit "$RELEASE_VERSION"', command)
        self.assertIn("--draft=false", command)
        self.assertLess(command.index("verify-artifacts"), command.index("--draft=false"))
        self.assertLess(command.index("cmp dist/SubtitleEditBay-Setup.exe.sha256"), command.index("--draft=false"))
        self.assertNotIn("--clobber", command)
        self.assertIn("release_state.py assert-published", str(published["run"]))
        self.assertEqual(publish["steps"][-1]["id"], "published")

    def test_release_request_uses_actual_merge_sha_and_supports_fixed_target_retry(self) -> None:
        workflow = load_workflow(RELEASE_REQUEST_WORKFLOW)
        triggers = workflow["on"]["push"]
        release_step = step_by_id(workflow, "validate", "release")
        command = str(release_step["run"])

        self.assertEqual(triggers["branches"], ["main"])
        self.assertEqual(triggers["paths"], ["VERSION"])
        self.assertIn("workflow_dispatch", workflow["on"])
        for guard in (
            'source_sha="$EVENT_SOURCE_SHA"',
            'source_sha="$REQUESTED_SOURCE_SHA"',
            "git merge-base --is-ancestor",
            "scripts/release_readiness.py",
            '[[ "$kind" == "release" ]]',
            'classified_version="$(sed -n \'s/^release_version=//p\' "$output_file")"',
            'release_version="$classified_version"',
            'echo "source_sha=$source_sha"',
        ):
            self.assertIn(guard, command)
        self.assertNotIn("remote_main", command)
        self.assertNotIn("tr -d", command)
        self.assertEqual(command.count('git show "$source_sha:VERSION"'), 0)

    def test_release_readiness_is_unskippable_and_fail_closed(self) -> None:
        workflow = load_workflow(RELEASE_READINESS_WORKFLOW)
        triggers = workflow["on"]
        aggregate = workflow["jobs"]["readiness"]
        command = str(aggregate["steps"][-1]["run"])

        self.assertIn("pull_request", triggers)
        self.assertIn("merge_group", triggers)
        self.assertNotIn("paths", triggers["pull_request"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(aggregate["if"], "always()")
        self.assertEqual(set(aggregate["needs"]), {"classify", "prepare"})
        self.assertIn("scripts/release_readiness.py assert-readiness", command)
        self.assertIn("needs.classify.result", command)
        self.assertIn("needs.prepare.result", command)
        self.assertNotIn("continue-on-error", RELEASE_READINESS_WORKFLOW.read_text(encoding="utf-8"))

        prepare = workflow["jobs"]["prepare"]
        self.assertEqual(prepare["uses"], "./.github/workflows/release-prepare.yml")
        self.assertEqual(prepare["with"]["source_sha"], "${{ github.sha }}")
        workflow_text = RELEASE_READINESS_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("pull_request_target", workflow_text)
        self.assertNotIn("git tag", workflow_text)
        self.assertNotIn("gh release create", workflow_text)
        collision = str(workflow["jobs"]["classify"]["steps"][-1]["run"])
        self.assertIn("release_state.py ensure-available", collision)
        self.assertNotIn("gh release view", collision)

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
            validate_step_order(workflow, "publish", ("download", "verify", "state", "release", "published"))

    def test_skipped_verification_and_failure_publish_are_rejected(self) -> None:
        mutations = []

        skipped_verification = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(skipped_verification, "publish", "verify")["if"] = "${{ false }}"
        mutations.append((skipped_verification, "publish/verify"))

        failure_publish = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(failure_publish, "publish", "release")["if"] = "${{ failure() }}"
        mutations.append((failure_publish, "publish/release"))

        for workflow, location in mutations:
            with (
                self.subTest(location=location),
                self.assertRaisesRegex(
                    WorkflowContractError,
                    location,
                ),
            ):
                validate_step_order(
                    workflow,
                    "publish",
                    ("download", "verify", "state", "release", "published"),
                    adjacent_pairs=(("verify", "state"), ("state", "release"), ("release", "published")),
                )

    def test_steps_cannot_mutate_artifacts_after_validation(self) -> None:
        mutations = []

        before_upload = copy.deepcopy(load_workflow(RELEASE_PREPARE_WORKFLOW))
        upload_index = before_upload["jobs"]["build"]["steps"].index(step_by_id(before_upload, "build", "upload"))
        before_upload["jobs"]["build"]["steps"].insert(
            upload_index,
            {"id": "mutate-package", "run": "echo mutate package"},
        )
        mutations.append(
            (
                before_upload,
                "build",
                ("package", "verify", "upload"),
                (("verify", "upload"),),
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
                ("download", "verify", "state", "release", "published"),
                (("verify", "state"), ("state", "release"), ("release", "published")),
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

        wrong_artifact_version = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(wrong_artifact_version, "publish", "verify")["run"] = (
            "python scripts/release_contract.py verify-artifacts --directory dist --expected-version 0.0.0"
        )
        mutations.append((wrong_artifact_version, "publish", "verify", "bash"))

        custom_shell = copy.deepcopy(load_workflow(RELEASE_WORKFLOW))
        step_by_id(custom_shell, "publish", "verify")["shell"] = "bash {0}"
        mutations.append((custom_shell, "publish", "verify", "bash"))

        expected_tokens = {
            "verify": (
                "python",
                "scripts/release_contract.py",
                "verify-artifacts",
                "--directory",
                "dist",
                "--expected-version",
                "${{ inputs.release_version }}",
                "--expected-source-sha",
                "${{ inputs.source_sha }}",
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
    def _write_release_artifacts(
        self,
        directory: Path,
        version: str = "1.2.3",
        source_sha: str = "a" * 40,
    ) -> str:
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
                    "source_sha": source_sha,
                    "asset_name": INSTALLER_NAME,
                    "sha256": digest,
                    "required_files": [
                        "SubtitleEditBayLauncher.exe",
                        "VERSION",
                        "scripts/launch.ps1",
                        "scripts/apply_installer_update.ps1",
                    ],
                }
            ),
            encoding="utf-8",
        )
        (directory / PREPARATION_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_sha": source_sha,
                    "release_version": f"v{version}",
                    "artifact_name": f"subtitle-edit-bay-{version}-windows-installer-{source_sha}",
                    "asset_name": INSTALLER_NAME,
                    "sha256": digest,
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

    def _rewrite_preparation(self, directory: Path, **changes: object) -> None:
        preparation_path = directory / PREPARATION_NAME
        preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
        preparation.update(changes)
        preparation_path.write_text(json.dumps(preparation), encoding="utf-8")

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

            self.assertEqual(verify_release_artifacts(directory, "v1.2.3", "a" * 40), digest)

    def test_artifact_source_identity_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            self._write_release_artifacts(directory)
            with self.assertRaisesRegex(ReleaseContractError, "source_sha mismatch"):
                verify_release_artifacts(directory, "1.2.3", "b" * 40)

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

    def test_preparation_identity_is_verified(self) -> None:
        mutations = (
            ({"source_sha": "b" * 40}, "preparation source_sha mismatch"),
            ({"release_version": "v1.2.4"}, "preparation release_version mismatch"),
            ({"artifact_name": "other"}, "preparation artifact_name mismatch"),
            ({"sha256": "0" * 64}, "preparation sha256 mismatch"),
        )
        for changes, message in mutations:
            with tempfile.TemporaryDirectory() as temp_dir, self.subTest(changes=changes):
                directory = Path(temp_dir)
                self._write_release_artifacts(directory)
                self._rewrite_preparation(directory, **changes)
                with self.assertRaisesRegex(ReleaseContractError, message):
                    verify_release_artifacts(directory, "1.2.3", "a" * 40)

    def test_installer_checksum_and_manifest_are_all_required(self) -> None:
        for asset_name in RELEASE_ASSET_NAMES:
            with tempfile.TemporaryDirectory() as temp_dir, self.subTest(asset_name=asset_name):
                directory = Path(temp_dir)
                self._write_release_artifacts(directory)
                (directory / asset_name).unlink()
                with self.assertRaisesRegex(ReleaseContractError, "artifacts are missing"):
                    verify_release_artifacts(directory, "1.2.3")


class ReleaseReadinessClassificationTests(unittest.TestCase):
    def test_version_only_increase_is_a_release(self) -> None:
        result = classify_values(("VERSION",), "v1.2.3", "v1.2.4")

        self.assertEqual(result.kind, "release")
        self.assertTrue(result.requires_preparation)
        self.assertEqual(result.release_version, "v1.2.4")

    def test_normal_change_does_not_require_release_preparation(self) -> None:
        result = classify_values(("src/gui.py",), "v1.2.3", "v1.2.3")

        self.assertEqual(result.kind, "normal")
        self.assertFalse(result.requires_preparation)

    def test_release_infrastructure_change_is_prepared_without_publishing(self) -> None:
        result = classify_values((".github/workflows/release.yml",), "v1.2.3", "v1.2.3")

        self.assertEqual(result.kind, "infrastructure")
        self.assertTrue(result.requires_preparation)

    def test_invalid_release_changes_fail_instead_of_becoming_normal(self) -> None:
        cases = (
            (("VERSION", "src/gui.py"), "v1.2.3", "v1.2.4", "must change only VERSION"),
            (("VERSION",), "v1.2.3", "v1.2.3", "without changing its value"),
            (("VERSION",), "v1.2.3", "v1.2.2", "must increase"),
            (("VERSION",), "v1.2.3", "1.2.4", "source VERSION is invalid"),
        )
        for changed, previous, requested, message in cases:
            with (
                self.subTest(changed=changed, requested=requested),
                self.assertRaisesRegex(ReleaseReadinessError, message),
            ):
                classify_values(changed, previous, requested)

    def test_deleted_version_file_fails_classification(self) -> None:
        with (
            patch(
                "scripts.release_readiness._git",
                side_effect=("D\tVERSION\n", ReleaseReadinessError("VERSION is missing")),
            ),
            self.assertRaisesRegex(ReleaseReadinessError, "VERSION is missing"),
        ):
            classify_changes("a" * 40, "b" * 40)

    def test_version_whitespace_is_normalized_once_for_preparation_and_publish(self) -> None:
        with patch(
            "scripts.release_readiness._git",
            side_effect=("M\tVERSION\n", "v1.2.4 \r\n", "v1.2.3\n"),
        ):
            result = classify_changes("a" * 40, "b" * 40)

        self.assertEqual(result.kind, "release")
        self.assertEqual(result.release_version, "v1.2.4")

    def test_aggregate_accepts_only_expected_success_or_normal_skip(self) -> None:
        assert_readiness_result("release", "success", True, "success")
        assert_readiness_result("infrastructure", "success", True, "success")
        assert_readiness_result("normal", "success", False, "skipped")

        failures = (
            ("release", "failure", True, "success"),
            ("release", "success", True, "failure"),
            ("release", "success", True, "cancelled"),
            ("release", "success", True, "skipped"),
            ("normal", "success", False, "success"),
            ("unexpected", "success", False, "skipped"),
        )
        for values in failures:
            with self.subTest(values=values), self.assertRaises(ReleaseReadinessError):
                assert_readiness_result(*values)

    def test_preparation_aggregate_rejects_failure_cancel_and_skip(self) -> None:
        assert_preparation_results(("success", "success", "success", "success"))
        for bad_result in ("failure", "cancelled", "skipped"):
            with self.subTest(result=bad_result), self.assertRaises(ReleaseReadinessError):
                assert_preparation_results(("success", bad_result, "success", "success"))


class ReleaseStateTests(unittest.TestCase):
    def test_publication_action_separates_new_published_and_draft_releases(self) -> None:
        self.assertEqual(publication_action(None), "create")
        self.assertEqual(publication_action(GitHubReleaseState(draft=False, published=True)), "reuse")
        self.assertEqual(publication_action(GitHubReleaseState(draft=True, published=False)), "publish-draft")
        with self.assertRaisesRegex(ReleaseStateError, "neither a draft nor published"):
            publication_action(GitHubReleaseState(draft=False, published=False))

    def test_tag_query_distinguishes_missing_existing_and_errors(self) -> None:
        cases = ((0, True), (2, False))
        for returncode, expected in cases:
            completed = subprocess.CompletedProcess(("git",), returncode, stdout="", stderr="")
            with (
                self.subTest(returncode=returncode),
                patch("scripts.release_state.subprocess.run", return_value=completed),
            ):
                self.assertIs(git_tag_exists("origin", "v1.2.3"), expected)

        failed = subprocess.CompletedProcess(("git",), 128, stdout="", stderr="authentication failed")
        with (
            patch("scripts.release_state.subprocess.run", return_value=failed),
            self.assertRaisesRegex(ReleaseStateError, "authentication failed"),
        ):
            git_tag_exists("origin", "v1.2.3")

    def _response(self, payload: object):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    def _not_found(self) -> HTTPError:
        return HTTPError("https://api.github.com", 404, "Not Found", {}, None)

    def test_release_query_distinguishes_published_draft_and_missing(self) -> None:
        published_payload = {"tag_name": "v1.2.3", "draft": False, "published_at": "2026-09-07T00:00:00Z"}
        draft_payload = {"tag_name": "v1.2.3", "draft": True, "published_at": None}
        with patch("scripts.release_state.urllib.request.urlopen", return_value=self._response(published_payload)):
            published = github_release_state("owner/repo", "v1.2.3", "token")
        with patch(
            "scripts.release_state.urllib.request.urlopen",
            side_effect=(self._not_found(), self._response([draft_payload])),
        ):
            draft = github_release_state("owner/repo", "v1.2.3", "token")
        with patch(
            "scripts.release_state.urllib.request.urlopen",
            side_effect=(self._not_found(), self._response([])),
        ):
            absent = github_release_state("owner/repo", "v1.2.3", "token")

        self.assertIsNotNone(published)
        self.assertTrue(published.published)
        self.assertFalse(published.draft)
        self.assertIsNotNone(draft)
        self.assertTrue(draft.draft)
        self.assertFalse(draft.published)
        self.assertIsNone(absent)

    def test_release_query_pages_through_authenticated_draft_search(self) -> None:
        first_page = [
            {"tag_name": f"v0.0.{index}", "draft": False, "published_at": "2026-09-07T00:00:00Z"}
            for index in range(100)
        ]
        draft_payload = {"tag_name": "v1.2.3", "draft": True, "published_at": None}
        with patch(
            "scripts.release_state.urllib.request.urlopen",
            side_effect=(self._not_found(), self._response(first_page), self._response([draft_payload])),
        ) as urlopen:
            state = github_release_state("owner/repo", "v1.2.3", "token")

        self.assertEqual(publication_action(state), "publish-draft")
        self.assertEqual(urlopen.call_count, 3)
        self.assertIn("page=2", urlopen.call_args.args[0].full_url)

    def test_release_query_rejects_api_authentication_and_transport_errors(self) -> None:
        failures = (
            HTTPError("https://api.github.com", 404, "Not Found", {}, None),
            HTTPError("https://api.github.com", 401, "Unauthorized", {}, None),
            HTTPError("https://api.github.com", 500, "Server Error", {}, None),
            URLError("network unavailable"),
        )
        for failure in failures:
            with (
                self.subTest(failure=failure),
                patch(
                    "scripts.release_state.urllib.request.urlopen",
                    side_effect=(self._not_found(), failure),
                ),
                self.assertRaises(ReleaseStateError),
            ):
                github_release_state("owner/repo", "v1.2.3", "token")


if __name__ == "__main__":
    unittest.main()
