import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


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

    def test_release_workflow_builds_and_publishes_versioned_tag(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn('tags:\n      - "v*"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("Release tag must use the vX.Y.Z format", workflow)
        self.assertIn("python -m unittest discover", workflow)
        self.assertIn("name: Test on Linux", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("name: Install Qt runtime dependencies", workflow)
        self.assertIn("libpulse0", workflow)
        self.assertIn("libegl1", workflow)
        self.assertIn("name: Build Windows installer", workflow)
        self.assertNotIn("needs: test", workflow)
        self.assertIn("needs:\n      - test\n      - build", workflow)
        self.assertIn("scripts/build_installer.ps1", workflow)
        self.assertIn("SubtitleEditBay-Setup.exe.sha256", workflow)
        self.assertIn("sha256sum --check", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("contents: write", workflow)

    def test_ci_cancels_only_superseded_automatic_runs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("concurrency:", workflow)
        self.assertIn("github.event.pull_request.number", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertIn("github.run_id", workflow)
        self.assertIn("github.ref", workflow)
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


if __name__ == "__main__":
    unittest.main()
