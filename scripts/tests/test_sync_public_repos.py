# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
"""Tests for sync_public_repos.py pure functions — stdlib unittest only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import sync_public_repos as app


class TestSanitize(unittest.TestCase):
    def test_strips_users_path(self):
        result = app._sanitize("path: ~/foo")
        self.assertNotIn("~/", result)
        self.assertIn("~/foo", result)

    def test_strips_home_path(self):
        result = app._sanitize("~/bot")
        self.assertNotIn("~/", result)
        self.assertIn("~/bot", result)

    def test_strips_vps_ip(self):
        result = app._sanitize("user: <user>@<vps-ip>")
        self.assertNotIn("<vps-ip>", result)
        self.assertIn("<user>@<vps-ip>", result)

    def test_strips_github_user(self):
        result = app._sanitize("repo: <github-user>/my-repo")
        self.assertNotIn("<github-user>", result)
        self.assertIn("<github-user>/my-repo", result)

    def test_clean_content_unchanged(self):
        content = "# Public hook\nNo private data here."
        self.assertEqual(app._sanitize(content), content)

    def test_multiple_patterns_in_one_file(self):
        content = "~/bot at <user>@<vps-ip>"
        result = app._sanitize(content)
        self.assertNotIn("~/", result)
        self.assertNotIn("<vps-ip>", result)


class TestCheckPrivacy(unittest.TestCase):
    def test_detects_users_path(self):
        violations = app._check_privacy("~/secret", "test.py")
        self.assertTrue(len(violations) > 0)

    def test_detects_vps_ip(self):
        violations = app._check_privacy("host = '<vps-ip>'", "config.py")
        self.assertTrue(len(violations) > 0)

    def test_detects_email(self):
        violations = app._check_privacy(
            "email = '<your-email>'", "config.py"
        )
        self.assertTrue(len(violations) > 0)

    def test_clean_content_no_violations(self):
        violations = app._check_privacy(
            "# Public hook\nPATH = '~/.claude/hooks'", "hook.py"
        )
        self.assertEqual(violations, [])

    def test_violation_includes_filename(self):
        violations = app._check_privacy("~/x", "my_file.py")
        self.assertTrue(any("my_file.py" in v for v in violations))


if __name__ == "__main__":
    unittest.main()
