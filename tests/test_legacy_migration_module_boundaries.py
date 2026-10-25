"""旧環境移行モジュールをどの順序で読み込んでも依存循環しないことを確認する。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class LegacyMigrationModuleBoundaryTests(unittest.TestCase):
    def test_feature_modules_can_be_imported_before_compatibility_api(self) -> None:
        for first in ("legacy_settings_migration", "legacy_cache_cleanup"):
            with self.subTest(first=first):
                script = (
                    "import importlib\n"
                    f"feature = importlib.import_module('src.{first}')\n"
                    "from src import legacy_migration as public_api\n"
                    "from src.legacy_migration_types import LegacyInventory\n"
                    "assert public_api.LegacyInventory is LegacyInventory\n"
                    "entrypoint = ('build_settings_migration_plan' "
                    "if feature.__name__.endswith('settings_migration') "
                    "else 'build_cache_cleanup_plan')\n"
                    "assert callable(getattr(feature, entrypoint))\n"
                )
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=REPOSITORY_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
