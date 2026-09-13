from __future__ import annotations

from pathlib import Path
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


class DependabotConfigTests(unittest.TestCase):
    def test_dependabot_updates_cover_repository_dependency_sources(self) -> None:
        config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))

        self.assertEqual(config["version"], 2)
        updates = config["updates"]
        self.assertEqual(
            {(update["package-ecosystem"], update["directory"]) for update in updates},
            {("pip", "/"), ("github-actions", "/")},
        )
        self.assertEqual(len(updates), 2)

        expected_group_names = {
            "pip": "python-minor-patch",
            "github-actions": "github-actions-minor-patch",
        }
        for update in updates:
            with self.subTest(ecosystem=update["package-ecosystem"]):
                self.assertEqual(update["schedule"], {"interval": "weekly"})
                self.assertEqual(update["open-pull-requests-limit"], 5)
                self.assertEqual(
                    set(update["groups"]),
                    {expected_group_names[update["package-ecosystem"]]},
                )
                self.assertEqual(
                    update["groups"][expected_group_names[update["package-ecosystem"]]]["update-types"],
                    ["minor", "patch"],
                )

        self.assertTrue((REPO_ROOT / "requirements.txt").is_file())
        self.assertTrue((REPO_ROOT / "requirements-dev.txt").is_file())
        self.assertTrue(list((REPO_ROOT / ".github" / "workflows").glob("*.yml")))


if __name__ == "__main__":
    unittest.main()
