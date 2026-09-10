import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell is required")
class RuntimeActivationTests(unittest.TestCase):
    def _run(self, root: Path, body: str) -> subprocess.CompletedProcess[str]:
        script = root / "activate-test.ps1"
        helper = str(ROOT / "scripts" / "runtime_activation.ps1").replace("'", "''")
        script.write_text(f". '{helper}'\n{body}", encoding="utf-8-sig")
        return subprocess.run(
            [
                shutil.which("powershell.exe") or "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_partial_old_runtime_cleanup_failure_keeps_new_runtime_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_runtime = root / "old-runtime"
            new_runtime = root / "new-runtime"
            old_runtime.mkdir()
            new_runtime.mkdir()
            (old_runtime / "required.dll").write_text("old", encoding="ascii")
            (new_runtime / "required.dll").write_text("new", encoding="ascii")
            active = root / "runtime-manifest.json"
            candidate = root / "candidate.json"
            active.write_text('{"runtime_directory":"old-runtime"}\n', encoding="utf-8")
            candidate.write_text('{"runtime_directory":"new-runtime"}\n', encoding="utf-8")

            result = self._run(
                root,
                "$remove = { param($path) Remove-Item -LiteralPath (Join-Path $path 'required.dll') -Force; throw 'injected cleanup failure' }\n"
                "Set-ActiveRuntimeGeneration -NewRuntimeDirectory '.\\new-runtime' -CandidateManifestPath '.\\candidate.json' "
                "-ActiveManifestPath '.\\runtime-manifest.json' -VerifyScript { } -CleanupDirectories @('.\\old-runtime') "
                "-RemoveDirectoryScript $remove\n",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("new-runtime", active.read_text(encoding="utf-8"))
            self.assertTrue((new_runtime / "required.dll").is_file())
            self.assertFalse((old_runtime / "required.dll").exists())
            self.assertIn("obsolete runtime cleanup failed", result.stdout + result.stderr)

    def test_failed_post_switch_verification_restores_previous_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "old-runtime").mkdir()
            (root / "new-runtime").mkdir()
            active = root / "runtime-manifest.json"
            candidate = root / "candidate.json"
            active.write_text('{"runtime_directory":"old-runtime"}\n', encoding="utf-8")
            candidate.write_text('{"runtime_directory":"new-runtime"}\n', encoding="utf-8")

            result = self._run(
                root,
                "Set-ActiveRuntimeGeneration -NewRuntimeDirectory '.\\new-runtime' -CandidateManifestPath '.\\candidate.json' "
                "-ActiveManifestPath '.\\runtime-manifest.json' -VerifyScript { throw 'verification failed' }\n",
            )

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("old-runtime", active.read_text(encoding="utf-8"))
            self.assertTrue((root / "new-runtime").is_dir())


if __name__ == "__main__":
    unittest.main()
