import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
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
from scripts.release_candidate import (
    CI_VALIDATION_JOB_NAME,
    REQUIRED_CI_JOB_NAMES,
    REQUIRED_JOB_NAMES,
    RELEASE_ARTIFACT_FILES,
    ReleaseCandidateError,
    extract_verified_artifact,
    resolve_release_candidate,
    select_release_candidate,
    verify_ci_validation_binding,
    verify_preparation_binding,
    verify_promotion_record,
    write_ci_validation_identity,
    write_promotion_record,
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
    validate_publish_gate,
    validate_publish_permissions,
    validate_step_order,
)


ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_REQUEST_WORKFLOW = ROOT / ".github" / "workflows" / "release-request.yml"
RELEASE_PREPARE_WORKFLOW = ROOT / ".github" / "workflows" / "release-prepare.yml"
RELEASE_READINESS_WORKFLOW = ROOT / ".github" / "workflows" / "release-readiness.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
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
        self.assertIn("-WindowStyle Hidden", definition)
        self.assertIn("Resolve-ActiveRuntimeDirectory", launcher)
        self.assertIn('Join-Path $runtimeDirectory "Scripts\\pythonw.exe"', launcher)
        self.assertIn("latest-launch-error.log", launcher)
        self.assertIn(r'Join-Path $env:LOCALAPPDATA "Subtitle Edit Bay\logs"', launcher)
        self.assertNotIn(r'Join-Path $env:LOCALAPPDATA "SubtitleEditBay\logs"', launcher)
        self.assertIn("setup.bat", launcher)
        self.assertIn("Test-CudaRepairRequired", launcher)
        self.assertIn("torch.cuda.is_available()", launcher)
        self.assertIn("GPU環境の修復", launcher)

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
        self.assertIn("pull_request_number", package)
        self.assertIn("workflow_run_id", package)
        self.assertIn("[long]$ProducerWorkflowRunId = 0", package)
        self.assertIn("[int]$ProducerWorkflowRunAttempt = 0", package)
        self.assertIn("$ExpectedVersion.Substring(1)", smoke)
        self.assertIn("Installed VERSION mismatch", smoke)
        self.assertIn("scripts\\setup.ps1", smoke)
        self.assertIn("runtime-manifest.json", smoke)
        self.assertIn("Scripts\\pip.exe", smoke)
        self.assertNotIn("pip install -r", smoke)
        self.assertIn("engine.rootObjects()", smoke)

    def test_release_workflow_has_safe_publish_graph_and_permissions(self) -> None:
        workflow = load_workflow(RELEASE_WORKFLOW)
        triggers = workflow["on"]

        self.assertEqual(set(triggers), {"workflow_call"})
        self.assertEqual(set(triggers["workflow_call"]["inputs"]), {"release_commit_sha", "release_version"})
        validate_publish_gate(
            workflow,
            publish_job="publish",
            required_gates=("candidate", "tag"),
        )
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["jobs"]["tag"]["permissions"], {"contents": "write"})
        self.assertEqual(
            workflow["jobs"]["publish"]["permissions"],
            {"actions": "read", "contents": "write"},
        )
        graph = build_job_graph(workflow)
        self.assertTrue({"candidate", "tag"}.issubset(job_ancestors(graph, "publish")))
        self.assertNotIn("prepare", workflow["jobs"])
        self.assertEqual(
            workflow["jobs"]["candidate"]["permissions"],
            {"actions": "read", "contents": "read", "pull-requests": "read"},
        )

    def test_release_entrypoint_passes_exact_source_and_version(self) -> None:
        release = load_workflow(RELEASE_WORKFLOW)
        request = load_workflow(RELEASE_REQUEST_WORKFLOW)
        reusable_release = request["jobs"]["release"]

        self.assertEqual(
            release["concurrency"]["group"],
            "release-${{ inputs.release_version }}-${{ inputs.release_commit_sha }}",
        )
        self.assertEqual(reusable_release["uses"], "./.github/workflows/release.yml")
        self.assertEqual(
            reusable_release["with"]["release_commit_sha"],
            "${{ needs.validate.outputs.source_sha }}",
        )
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
        validation_command = str(step_by_id(preparation, "validate", "contract")["run"])
        self.assertNotIn("artifact_name", validation_command)
        identity_command = str(step_by_id(preparation, "build", "identity")["run"])
        self.assertIn("-attempt-$env:GITHUB_RUN_ATTEMPT", identity_command)
        self.assertEqual(upload["with"]["name"], "${{ steps.identity.outputs.artifact_name }}")
        smoke_download = step_by_id(preparation, "smoke", "download")
        self.assertEqual(smoke_download["with"]["artifact-ids"], "${{ needs.build.outputs.artifact_id }}")
        self.assertEqual(
            preparation["jobs"]["readiness"]["outputs"]["artifact_name"],
            "${{ needs.build.outputs.artifact_name }}",
        )
        uploaded_paths = str(upload["with"]["path"])
        self.assertTrue(all(asset_name in uploaded_paths for asset_name in RELEASE_ASSET_NAMES))
        published_assets = str(step_by_id(workflow, "publish", "release")["run"])
        self.assertTrue(all(asset_name in published_assets for asset_name in RELEASE_ASSET_NAMES))
        self.assertIn("release-promotion.json", published_assets)
        self.assertNotIn("--clobber", published_assets)
        self.assertIn('cmp "dist/$asset" "existing-release/$asset"', published_assets)

        candidate = workflow["jobs"]["candidate"]
        self.assertNotIn("release-prepare.yml", RELEASE_WORKFLOW.read_text(encoding="utf-8"))
        select_command = str(step_by_id(workflow, "candidate", "select")["run"])
        self.assertIn("release_candidate.py resolve", select_command)
        self.assertIn("--decision-artifact-name", select_command)
        download = step_by_id(workflow, "candidate", "download")
        download_command = str(download["run"])
        self.assertIn("release_candidate.py download-artifact", download_command)
        self.assertIn('--artifact-id "${{ steps.select.outputs.artifact_id }}"', download_command)
        self.assertIn('--artifact-digest "${{ steps.select.outputs.artifact_digest }}"', download_command)
        self.assertNotIn("actions/download-artifact", str(download))
        upload = next(step for step in candidate["steps"] if step.get("name") == "Upload promotion record")
        self.assertEqual(upload["if"], "steps.select.outputs.decision_reused != 'true'")
        self.assertNotIn("continue-on-error", candidate)

        ci_download = next(
            step for step in candidate["steps"] if step.get("name") == "Download the CI validation identity"
        )
        self.assertIn('--artifact-id "${{ steps.select.outputs.ci_artifact_id }}"', str(ci_download["run"]))
        self.assertIn(
            '--artifact-digest "${{ steps.select.outputs.ci_artifact_digest }}"',
            str(ci_download["run"]),
        )

    def test_normal_ci_records_the_exact_validated_merge_identity(self) -> None:
        workflow = load_workflow(CI_WORKFLOW)
        result = workflow["jobs"]["validation-result"]

        self.assertEqual(result["name"], "CI validation result")
        self.assertEqual(
            set(result["needs"]),
            {
                "python-quality",
                "portable-tests",
                "windows-tests",
                "windows-launcher-tests",
                "ffmpeg6-compat",
                "windows-installer-smoke",
            },
        )
        record = next(step for step in result["steps"] if step.get("name") == "Record validated source identity")
        command = str(record["run"])
        self.assertIn("write-ci-validation", command)
        self.assertIn("git rev-parse HEAD", command)
        self.assertIn("git rev-parse 'HEAD^{tree}'", command)
        self.assertIn("--pull-request-base-sha", command)
        upload = next(step for step in result["steps"] if step.get("name") == "Upload immutable CI validation identity")
        self.assertIn("${{ github.sha }}-attempt-${{ github.run_attempt }}", upload["with"]["name"])

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
        verify = str(step_by_id(workflow, "publish", "verify")["run"])
        self.assertIn("verify-artifacts", verify)
        self.assertIn("verify-promotion", verify)
        candidate_download = str(step_by_id(workflow, "publish", "download")["run"])
        self.assertIn("release_candidate.py download-artifact", candidate_download)
        decision_download = next(
            step for step in publish["steps"] if step.get("name") == "Download frozen promotion decision"
        )
        self.assertIn("download-run-artifact", str(decision_download["run"]))
        self.assertIn('cmp "dist/$asset" "existing-release/$asset"', command)
        self.assertLess(command.index("cmp"), command.index("--draft=false"))
        self.assertIn('[[ "${#missing[@]}" -eq 0 ]]', command)
        self.assertIn("gh release upload", command)
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
        workflow = load_workflow(RELEASE_WORKFLOW)
        command = str(step_by_id(workflow, "publish", "verify")["run"])

        self.assertIn('--expected-version "${{ inputs.release_version }}"', command)
        self.assertIn('--expected-source-sha "$CANDIDATE_SOURCE_SHA"', command)
        self.assertIn('[[ "$actual_sha256" == "$INSTALLER_SHA256" ]]', command)
        self.assertNotIn("|| true", command)
        self.assertEqual(step_by_id(workflow, "publish", "verify")["shell"], "bash")


class ReleaseCandidateTests(unittest.TestCase):
    RELEASE_SHA = "d" * 40
    CANDIDATE_SHA = "c" * 40
    HEAD_SHA = "b" * 40
    BASE_SHA = "a" * 40
    TREE_SHA = "e" * 40

    def _api(
        self,
        *,
        run_conclusion: str = "success",
        run_status: str = "completed",
        job_conclusions: dict[str, str] | None = None,
        ci_job_conclusions: dict[str, str] | None = None,
        readiness_job_attempts: dict[str, int] | None = None,
        artifact_attempt: int | None = None,
        additional_artifact_attempts: tuple[int, ...] = (),
        include_inherited_success_records: bool = False,
        artifact_expired: bool = False,
        candidate_tree: str | None = None,
        candidate_parents: tuple[str, ...] | None = None,
        ci_source_sha: str | None = None,
        ci_source_tree: str | None = None,
        ci_source_parents: tuple[str, ...] | None = None,
        include_older_success: bool = False,
        include_newer_in_progress: bool = False,
        include_failed_test_attempt: bool = False,
        empty_pull_requests: bool = False,
        contradictory_pull_requests: bool = False,
    ) -> MagicMock:
        api = MagicMock()
        api.repository = "owner/repo"
        pull = {
            "number": 42,
            "merged": True,
            "merged_at": "2026-09-07T00:00:00Z",
            "merge_commit_sha": self.RELEASE_SHA,
            "head": {
                "sha": self.HEAD_SHA,
                "ref": "release/v1.2.3",
                "repo": {"full_name": "owner/repo"},
            },
            "base": {"sha": self.BASE_SHA, "ref": "main"},
        }
        latest_run = {
            "id": 200,
            "run_attempt": 2,
            "created_at": "2026-09-07T02:00:00Z",
            "status": run_status,
            "conclusion": run_conclusion,
            "event": "pull_request",
            "path": ".github/workflows/release-readiness.yml",
            "head_sha": self.HEAD_SHA,
            "head_branch": "release/v1.2.3",
            "head_repository": {"full_name": "owner/repo"},
            "pull_requests": (
                [{"number": 99}] if contradictory_pull_requests else [] if empty_pull_requests else [{"number": 42}]
            ),
        }
        readiness_runs = [latest_run]
        if include_older_success:
            older = dict(
                latest_run,
                id=100,
                run_attempt=1,
                created_at="2026-09-07T01:00:00Z",
                status="completed",
                conclusion="success",
            )
            readiness_runs.append(older)
        if include_newer_in_progress:
            readiness_runs.append(
                dict(
                    latest_run,
                    id=250,
                    run_attempt=1,
                    created_at="2026-09-07T03:00:00Z",
                    status="in_progress",
                    conclusion=None,
                )
            )
        readiness_jobs = [
            {
                "name": (
                    f"Prepare merge candidate / {name}"
                    if name not in {"Classify merge candidate", "Release readiness"}
                    else name
                ),
                "status": "completed",
                "conclusion": (job_conclusions or {}).get(name, "success"),
                "run_attempt": (readiness_job_attempts or {}).get(name, 2),
            }
            for name in REQUIRED_JOB_NAMES
        ]
        if include_failed_test_attempt:
            readiness_jobs.append(
                {
                    "name": "Prepare merge candidate / Release tests on Linux",
                    "status": "completed",
                    "conclusion": "failure",
                    "run_attempt": 1,
                }
            )
        if include_inherited_success_records:
            for inherited_name in (
                "Build and verify Windows installer",
                "Install and start prepared package",
            ):
                readiness_jobs.append(
                    {
                        "name": f"Prepare merge candidate / {inherited_name}",
                        "status": "completed",
                        "conclusion": "success",
                        "run_attempt": 1,
                        "started_at": "2026-09-07T01:15:00Z",
                        "completed_at": "2026-09-07T01:45:00Z",
                    }
                )
                for job in readiness_jobs:
                    if job["name"].endswith(inherited_name):
                        job.setdefault("started_at", "2026-09-07T01:15:00Z")
                        job.setdefault("completed_at", "2026-09-07T01:45:00Z")
        ci_run = dict(
            latest_run,
            id=201,
            run_attempt=1,
            path=".github/workflows/ci.yml",
            status="completed",
            conclusion="success",
        )
        ci_jobs = [
            {
                "name": name,
                "status": "completed",
                "conclusion": (ci_job_conclusions or {}).get(name, "success"),
                "run_attempt": 1,
            }
            for name in (*REQUIRED_CI_JOB_NAMES, CI_VALIDATION_JOB_NAME)
        ]
        artifact = {
            "id": 300,
            "name": (
                f"subtitle-edit-bay-1.2.3-windows-installer-{self.CANDIDATE_SHA}"
                "-attempt-"
                f"{artifact_attempt or (readiness_job_attempts or {}).get('Build and verify Windows installer', 2)}"
            ),
            "digest": "sha256:" + "f" * 64,
            "expired": artifact_expired,
            "expires_at": "2026-09-21T00:00:00Z",
        }
        prepared_artifacts = [artifact]
        for prior_artifact_attempt in additional_artifact_attempts:
            prepared_artifacts.append(
                dict(
                    artifact,
                    id=300 + prior_artifact_attempt,
                    name=(
                        f"subtitle-edit-bay-1.2.3-windows-installer-{self.CANDIDATE_SHA}"
                        f"-attempt-{prior_artifact_attempt}"
                    ),
                )
            )
        selected_ci_source_sha = ci_source_sha or self.CANDIDATE_SHA
        ci_artifact = {
            "id": 301,
            "name": f"ci-validation-identity-{selected_ci_source_sha}-attempt-1",
            "digest": "sha256:" + "1" * 64,
            "expired": False,
            "expires_at": "2026-09-21T00:00:00Z",
        }

        def get(path: str, query: dict[str, object] | None = None) -> object:
            if path.endswith("/pulls/42"):
                return pull
            if path.endswith(f"/git/commits/{self.CANDIDATE_SHA}"):
                return {
                    "tree": {"sha": candidate_tree or self.TREE_SHA},
                    "parents": [{"sha": sha} for sha in (candidate_parents or (self.BASE_SHA, self.HEAD_SHA))],
                }
            if ci_source_sha and path.endswith(f"/git/commits/{ci_source_sha}"):
                return {
                    "tree": {"sha": ci_source_tree or self.TREE_SHA},
                    "parents": [{"sha": sha} for sha in (ci_source_parents or (self.BASE_SHA, self.HEAD_SHA))],
                }
            if path.endswith(f"/git/commits/{self.RELEASE_SHA}"):
                return {"tree": {"sha": self.TREE_SHA}, "parents": [{"sha": self.BASE_SHA}]}
            raise AssertionError(path)

        def pages(
            path: str,
            key: str | None = None,
            query: dict[str, object] | None = None,
        ) -> list[object]:
            if path.endswith(f"/commits/{self.RELEASE_SHA}/pulls"):
                return [pull]
            if path.endswith("/pulls/42/files"):
                return [{"filename": "VERSION"}]
            if path.endswith("/release-readiness.yml/runs"):
                return readiness_runs
            if path.endswith("/ci.yml/runs"):
                return [ci_run]
            if path.endswith("/runs/200/jobs"):
                return readiness_jobs
            if path.endswith("/runs/201/jobs"):
                return ci_jobs
            if path.endswith("/runs/200/artifacts"):
                return prepared_artifacts
            if path.endswith("/runs/201/artifacts"):
                return [ci_artifact]
            raise AssertionError(path)

        api.get.side_effect = get
        api.pages.side_effect = pages
        return api

    def test_selects_exact_successful_candidate_and_records_distinct_release_commit(self) -> None:
        candidate = select_release_candidate(self._api(), self.RELEASE_SHA, "v1.2.3")

        self.assertEqual(candidate.pull_request_number, 42)
        self.assertEqual(candidate.candidate_source_sha, self.CANDIDATE_SHA)
        self.assertEqual(candidate.release_commit_sha, self.RELEASE_SHA)
        self.assertEqual(candidate.candidate_source_tree, candidate.release_commit_tree)
        self.assertEqual(candidate.workflow_run_id, 200)
        self.assertEqual(candidate.workflow_run_attempt, 2)
        self.assertEqual(candidate.artifact_workflow_run_attempt, 2)
        self.assertEqual(candidate.installer_smoke_workflow_run_attempt, 2)
        self.assertEqual(candidate.ci_workflow_run_id, 201)
        self.assertEqual(candidate.ci_workflow_run_attempt, 1)
        self.assertEqual(candidate.ci_candidate_source_sha, self.CANDIDATE_SHA)
        self.assertEqual(candidate.ci_candidate_source_tree, self.TREE_SHA)
        self.assertEqual(candidate.ci_artifact_id, 301)
        self.assertEqual(candidate.artifact_id, 300)

    def test_accepts_cleared_post_merge_pull_association_with_full_identity(self) -> None:
        candidate = select_release_candidate(
            self._api(empty_pull_requests=True),
            self.RELEASE_SHA,
            "v1.2.3",
        )

        self.assertEqual(candidate.pull_request_number, 42)

    def test_rejects_contradictory_nonempty_pull_association(self) -> None:
        with self.assertRaisesRegex(ReleaseCandidateError, "no release-readiness.yml run"):
            select_release_candidate(
                self._api(contradictory_pull_requests=True),
                self.RELEASE_SHA,
                "v1.2.3",
            )

    def test_does_not_fall_back_when_latest_matching_run_failed(self) -> None:
        api = self._api(run_conclusion="failure", include_older_success=True)

        with self.assertRaisesRegex(ReleaseCandidateError, "latest matching.*did not succeed"):
            select_release_candidate(api, self.RELEASE_SHA, "v1.2.3")

    def test_does_not_fall_back_when_latest_matching_run_is_in_progress(self) -> None:
        api = self._api(include_newer_in_progress=True)

        with self.assertRaisesRegex(ReleaseCandidateError, "in_progress/None"):
            select_release_candidate(api, self.RELEASE_SHA, "v1.2.3")
        readiness_queries = [
            call.kwargs["query"]
            for call in api.pages.call_args_list
            if call.args and str(call.args[0]).endswith("/release-readiness.yml/runs")
        ]
        self.assertEqual(readiness_queries, [{"event": "pull_request"}])

    def test_rejects_failed_required_normal_ci_job(self) -> None:
        with self.assertRaisesRegex(ReleaseCandidateError, "Windows runtime tests"):
            select_release_candidate(
                self._api(ci_job_conclusions={"Windows runtime tests": "failure"}),
                self.RELEASE_SHA,
                "v1.2.3",
            )

    def test_rejects_ci_that_validated_same_head_against_an_old_base(self) -> None:
        old_ci_sha = "f" * 40
        with self.assertRaisesRegex(ReleaseCandidateError, "CI source is not the final merge"):
            select_release_candidate(
                self._api(
                    ci_source_sha=old_ci_sha,
                    ci_source_tree="9" * 40,
                    ci_source_parents=("8" * 40, self.HEAD_SHA),
                ),
                self.RELEASE_SHA,
                "v1.2.3",
            )

    def test_rejects_missing_verification_stale_head_tree_and_expiry(self) -> None:
        cases = (
            (self._api(job_conclusions={"Install and start prepared package": "skipped"}), "did not succeed"),
            (self._api(candidate_parents=(self.BASE_SHA, "f" * 40)), "final tested merge"),
            (self._api(candidate_tree="f" * 40), "source trees differ"),
            (self._api(artifact_expired=True), "expired"),
        )
        for api, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ReleaseCandidateError, message):
                select_release_candidate(api, self.RELEASE_SHA, "v1.2.3")

    def test_promotion_record_is_stable_and_detects_mismatch(self) -> None:
        candidate = select_release_candidate(self._api(), self.RELEASE_SHA, "v1.2.3")
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            candidate_path = directory / "candidate.json"
            candidate_path.write_text(json.dumps(candidate.__dict__), encoding="utf-8")
            expected = directory / "expected.json"
            actual = directory / "actual.json"
            write_promotion_record(expected, candidate_path, "1" * 64)
            write_promotion_record(actual, candidate_path, "1" * 64)
            verify_promotion_record(actual, expected)
            payload = json.loads(actual.read_text(encoding="utf-8"))
            payload["installer_sha256"] = "2" * 64
            actual.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ReleaseCandidateError, "does not match"):
                verify_promotion_record(actual, expected)

    def test_generated_preparation_is_bound_to_pr_and_readiness_run(self) -> None:
        candidate = select_release_candidate(self._api(), self.RELEASE_SHA, "v1.2.3")
        producer = {
            "repository": candidate.repository,
            "event_name": "pull_request",
            "pull_request_number": candidate.pull_request_number,
            "pull_request_head_sha": candidate.pull_request_head_sha,
            "pull_request_base_sha": candidate.pull_request_base_sha,
            "pull_request_head_branch": candidate.pull_request_head_branch,
            "workflow_path": candidate.workflow_path,
            "workflow_run_id": candidate.workflow_run_id,
            "workflow_run_attempt": candidate.artifact_workflow_run_attempt,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            candidate_path = directory / "candidate.json"
            preparation_path = directory / "release-preparation.json"
            candidate_path.write_text(json.dumps(candidate.__dict__), encoding="utf-8")
            preparation_path.write_text(json.dumps({"producer": producer}), encoding="utf-8")
            verify_preparation_binding(candidate_path, preparation_path)
            producer["workflow_run_id"] = 999
            preparation_path.write_text(json.dumps({"producer": producer}), encoding="utf-8")

            with self.assertRaisesRegex(ReleaseCandidateError, "does not match"):
                verify_preparation_binding(candidate_path, preparation_path)

    def test_partial_rerun_reuses_successful_build_artifact_from_prior_attempt(self) -> None:
        prior_attempt_jobs = {
            "Classify merge candidate": 1,
            "Validate source and version": 1,
        }
        api = self._api(
            readiness_job_attempts=prior_attempt_jobs,
            artifact_attempt=1,
            include_inherited_success_records=True,
            include_failed_test_attempt=True,
        )
        candidate = select_release_candidate(
            api,
            self.RELEASE_SHA,
            "v1.2.3",
        )
        self.assertEqual(candidate.workflow_run_attempt, 2)
        self.assertEqual(candidate.artifact_workflow_run_attempt, 1)
        self.assertEqual(candidate.installer_smoke_workflow_run_attempt, 2)
        self.assertEqual(candidate.artifact_id, 300)
        self.assertTrue(candidate.artifact_name.endswith("-attempt-1"))
        job_queries = [
            call.kwargs["query"]
            for call in api.pages.call_args_list
            if call.args and str(call.args[0]).endswith("/jobs")
        ]
        self.assertEqual(job_queries, [{"filter": "all"}, {"filter": "all"}])

        producer = {
            "repository": candidate.repository,
            "event_name": "pull_request",
            "pull_request_number": candidate.pull_request_number,
            "pull_request_head_sha": candidate.pull_request_head_sha,
            "pull_request_base_sha": candidate.pull_request_base_sha,
            "pull_request_head_branch": candidate.pull_request_head_branch,
            "workflow_path": candidate.workflow_path,
            "workflow_run_id": candidate.workflow_run_id,
            "workflow_run_attempt": 1,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            candidate_path = directory / "candidate.json"
            preparation_path = directory / "release-preparation.json"
            candidate_path.write_text(json.dumps(candidate.__dict__), encoding="utf-8")
            preparation_path.write_text(json.dumps({"producer": producer}), encoding="utf-8")

            verify_preparation_binding(candidate_path, preparation_path)

    def test_build_only_rerun_selects_the_new_artifact_created_by_build(self) -> None:
        candidate = select_release_candidate(
            self._api(
                readiness_job_attempts={
                    "Classify merge candidate": 1,
                    "Validate source and version": 1,
                },
                artifact_attempt=2,
                additional_artifact_attempts=(1,),
                include_inherited_success_records=True,
            ),
            self.RELEASE_SHA,
            "v1.2.3",
        )

        self.assertEqual(candidate.artifact_workflow_run_attempt, 2)
        self.assertEqual(candidate.artifact_id, 300)
        self.assertTrue(candidate.artifact_name.endswith("-attempt-2"))

    def test_ci_identity_record_must_match_selected_merge_content(self) -> None:
        candidate = select_release_candidate(self._api(), self.RELEASE_SHA, "v1.2.3")
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            candidate_path = directory / "candidate.json"
            identity_path = directory / "ci-validation-identity.json"
            candidate_path.write_text(json.dumps(candidate.__dict__), encoding="utf-8")
            write_ci_validation_identity(
                identity_path,
                candidate.repository,
                candidate.pull_request_number,
                candidate.pull_request_head_sha,
                candidate.pull_request_base_sha,
                candidate.pull_request_head_branch,
                candidate.ci_candidate_source_sha,
                candidate.ci_candidate_source_tree,
                candidate.ci_workflow_run_id,
                candidate.ci_artifact_workflow_run_attempt,
            )
            verify_ci_validation_binding(candidate_path, identity_path)
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["pull_request_base_sha"] = "9" * 40
            identity_path.write_text(json.dumps(identity), encoding="utf-8")

            with self.assertRaisesRegex(ReleaseCandidateError, "does not match"):
                verify_ci_validation_binding(candidate_path, identity_path)

    def test_archive_digest_rejects_self_consistent_replacement(self) -> None:
        def write_archive(path: Path, installer: bytes) -> None:
            installer_digest = hashlib.sha256(installer).hexdigest()
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(INSTALLER_NAME, installer)
                archive.writestr(CHECKSUM_NAME, f"{installer_digest}  {INSTALLER_NAME}")
                archive.writestr(MANIFEST_NAME, json.dumps({"sha256": installer_digest}))
                archive.writestr(PREPARATION_NAME, json.dumps({"sha256": installer_digest}))

        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            archive_path = directory / "candidate.zip"
            write_archive(archive_path, b"approved installer")
            approved_digest = f"sha256:{hashlib.sha256(archive_path.read_bytes()).hexdigest()}"
            write_archive(archive_path, b"replacement installer")

            with self.assertRaisesRegex(ReleaseCandidateError, "archive digest mismatch"):
                extract_verified_artifact(
                    archive_path,
                    directory / "dist",
                    approved_digest,
                    RELEASE_ARTIFACT_FILES,
                )

    def test_rerun_reuses_saved_immutable_promotion_decision(self) -> None:
        candidate = select_release_candidate(self._api(), self.RELEASE_SHA, "v1.2.3")
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            candidate_path = directory / "selected-candidate.json"
            candidate_path.write_text(json.dumps(candidate.__dict__), encoding="utf-8")
            promotion_path = directory / "release-promotion.json"
            write_promotion_record(promotion_path, candidate_path, "1" * 64)
            archive_path = directory / "decision.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(candidate_path, candidate_path.name)
                archive.write(promotion_path, promotion_path.name)
            archive_bytes = archive_path.read_bytes()
            archive_digest = f"sha256:{hashlib.sha256(archive_bytes).hexdigest()}"
            api = MagicMock()
            api.repository = "owner/repo"
            api.pages.return_value = [
                {
                    "id": 400,
                    "name": "release-promotion-decision",
                    "digest": archive_digest,
                    "expired": False,
                }
            ]

            def download(_artifact_id: int, destination: Path, _digest: str) -> None:
                destination.write_bytes(archive_bytes)

            api.download_artifact.side_effect = download
            restored, reused = resolve_release_candidate(
                api,
                self.RELEASE_SHA,
                "v1.2.3",
                500,
                "release-promotion-decision",
                directory / "restored",
                directory / "restored-candidate.json",
            )

            self.assertTrue(reused)
            self.assertEqual(restored, candidate)
            api.pages.assert_called_once()


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
                        "VERSION",
                        "scripts/launch.ps1",
                        "scripts/apply_installer_update.ps1",
                        "scripts/runtime_activation.ps1",
                        "scripts/runtime_contract.py",
                        "runtime/runtime-contract.json",
                        "runtime/requirements-windows-cpu.lock",
                        "runtime/requirements-windows-cu128.lock",
                    ],
                    "runtime_contract": {
                        "contract_sha256": "1" * 64,
                        "cpu_lock_sha256": "2" * 64,
                        "cu128_lock_sha256": "3" * 64,
                    },
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
            ({"runtime_contract": {}}, "runtime_contract hashes are incomplete"),
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
        for changed_file in (".github/workflows/release.yml", "scripts/release_candidate.py"):
            with self.subTest(changed_file=changed_file):
                result = classify_values((changed_file,), "v1.2.3", "v1.2.3")

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
