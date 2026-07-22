import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from core.version_control import find_repository, get_status, save_version


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


class VersionControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        _git(self.repo, "init")
        _git(self.repo, "config", "user.name", "test-user")
        _git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / ".gitignore").write_text(".env\ncache/\n", encoding="utf-8")
        (self.repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
        _git(self.repo, "add", ".gitignore", "app.py")
        _git(self.repo, "commit", "-m", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def test_save_version_stages_tracked_and_new_code_only(self):
        (self.repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
        (self.repo / "new_feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "business_data.csv").write_text("id,value\n1,2\n", encoding="utf-8")
        (self.repo / ".env").write_text("TOKEN=hidden\n", encoding="utf-8")

        result = save_version(self.repo, "新增版本管理", push=False)

        self.assertTrue(result.ok, result.details)
        committed = _git(self.repo, "show", "--pretty=", "--name-only", "HEAD").splitlines()
        self.assertIn("app.py", committed)
        self.assertIn("new_feature.py", committed)
        self.assertNotIn("business_data.csv", committed)
        self.assertNotIn(".env", committed)

    def test_status_and_repository_discovery(self):
        nested = self.repo / "nested"
        nested.mkdir()
        self.assertEqual(find_repository(nested), self.repo.resolve())
        status = get_status(self.repo)
        self.assertFalse(status.error)
        self.assertTrue(status.branch)
        self.assertTrue(status.recent_commits)


if __name__ == "__main__":
    unittest.main()
