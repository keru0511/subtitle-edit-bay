from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("runtime_contract", ROOT / "scripts" / "runtime_contract.py")
assert SPEC and SPEC.loader
RUNTIME_CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNTIME_CONTRACT)


class RuntimeContractTests(unittest.TestCase):
    def test_release_profiles_lock_the_complete_hashed_graph(self) -> None:
        profiles = RUNTIME_CONTRACT.validate_contract(ROOT)

        self.assertGreater(len(profiles["cpu"]), 100)
        self.assertGreater(len(profiles["cu128"]), 100)
        self.assertEqual(profiles["cpu"]["torch"], "2.8.0")
        self.assertEqual(profiles["cu128"]["torch"], "2.8.0+cu128")
        self.assertEqual(set(profiles["cpu"]), set(profiles["cu128"]))

    def test_installer_contains_contract_locks_and_verifier(self) -> None:
        installer = (ROOT / "installer" / "SubtitleEditBay.iss").read_text(encoding="utf-8-sig")

        self.assertIn(r"scripts\runtime_contract.py", installer)
        self.assertIn(r"runtime\*", installer)

    def test_release_ci_reconstructs_a_fresh_hashed_runtime(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-prepare.yml").read_text(encoding="utf-8")

        self.assertIn("Reconstruct locked CPU runtime in a fresh environment", workflow)
        self.assertIn("choco install ffmpeg", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertIn("verify-runtime", workflow)


if __name__ == "__main__":
    unittest.main()
