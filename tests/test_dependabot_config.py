from __future__ import annotations

from pathlib import Path
import unittest

import yaml
from src.data_boundary import is_object_list, is_object_mapping
from tests.typed_case import TypedTestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


class DependabotConfigTests(TypedTestCase):
    def test_dependabot_updates_cover_repository_dependency_sources(self) -> None:
        config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
        assert is_object_mapping(config)

        self.assertEqual(config["version"], 2)
        updates = config["updates"]
        assert is_object_list(updates)
        self.assertEqual(len(updates), 2)

        expected_group_names = {
            "pip": "python-minor-patch",
            "github-actions": "github-actions-minor-patch",
        }
        sources: set[tuple[str, str]] = set()
        for update in updates:
            assert is_object_mapping(update)
            ecosystem = update["package-ecosystem"]
            directory = update["directory"]
            assert isinstance(ecosystem, str)
            assert isinstance(directory, str)
            sources.add((ecosystem, directory))
            with self.subTest(ecosystem=ecosystem):
                self.assertEqual(update["schedule"], {"interval": "weekly"})
                self.assertEqual(update["open-pull-requests-limit"], 5)
                groups = update["groups"]
                assert is_object_mapping(groups)
                group_name = expected_group_names[ecosystem]
                self.assertEqual(
                    set(groups),
                    {group_name},
                )
                group = groups[group_name]
                assert is_object_mapping(group)
                self.assertEqual(
                    group["update-types"],
                    ["minor", "patch"],
                )
        self.assertEqual(sources, {("pip", "/"), ("github-actions", "/")})

        self.assertTrue((REPO_ROOT / "requirements.txt").is_file())
        self.assertTrue((REPO_ROOT / "requirements-dev.txt").is_file())
        self.assertTrue(any((REPO_ROOT / ".github" / "workflows").glob("*.yml")))


if __name__ == "__main__":
    unittest.main()
