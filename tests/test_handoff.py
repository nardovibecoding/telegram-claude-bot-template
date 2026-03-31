# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
import json
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_bot.handoff import (
    save_handoff, load_handoffs, clear_handoffs,
    TEAM_DOMAINS, MAX_CONTENT_LEN, TTL_SECONDS,
)


class TestTeamDomains(unittest.TestCase):
    def test_team_a_has_four_roles(self):
        roles = [r for t, r in TEAM_DOMAINS.values() if t == "team_a"]
        self.assertEqual(sorted(roles), ["builder", "critic", "growth", "scout"])

    def test_domain_format(self):
        for domain, (team, role) in TEAM_DOMAINS.items():
            self.assertEqual(domain, f"{team}:{role}")


class TestHandoffIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._patch = patch("admin_bot.handoff.HANDOFF_DIR", Path(self.tmpdir))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        for f in Path(self.tmpdir).glob("*"):
            f.unlink()
        os.rmdir(self.tmpdir)

    def test_save_and_load(self):
        save_handoff("team_a", "scout", "Found opportunity in legal vertical")
        handoffs = load_handoffs("team_a", exclude_role="builder")
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0]["role"], "scout")
        self.assertIn("legal vertical", handoffs[0]["content"])

    def test_exclude_own_role(self):
        save_handoff("team_a", "scout", "Scout output")
        handoffs = load_handoffs("team_a", exclude_role="scout")
        self.assertEqual(len(handoffs), 0)

    def test_content_cap(self):
        long_content = "x" * (MAX_CONTENT_LEN + 500)
        save_handoff("team_a", "scout", long_content)
        handoffs = load_handoffs("team_a")
        self.assertLessEqual(len(handoffs[0]["content"]), MAX_CONTENT_LEN)

    def test_clear_handoffs(self):
        save_handoff("team_a", "scout", "data1")
        save_handoff("team_a", "builder", "data2")
        cleared = clear_handoffs("team_a")
        self.assertEqual(cleared, 2)
        self.assertEqual(len(load_handoffs("team_a")), 0)

    def test_expired_handoffs_pruned(self):
        save_handoff("team_a", "scout", "old data")
        fpath = Path(self.tmpdir) / "team_a_scout.json"
        data = json.loads(fpath.read_text())
        data["timestamp"] = time.time() - TTL_SECONDS - 3600
        fpath.write_text(json.dumps(data))
        handoffs = load_handoffs("team_a")
        self.assertEqual(len(handoffs), 0)
        self.assertFalse(fpath.exists())

    def test_multiple_roles_sorted_newest_first(self):
        save_handoff("team_a", "scout", "first")
        time.sleep(0.05)
        save_handoff("team_a", "critic", "second")
        handoffs = load_handoffs("team_a", exclude_role="builder")
        self.assertEqual(handoffs[0]["role"], "critic")
        self.assertEqual(handoffs[1]["role"], "scout")

    def test_atomic_write(self):
        save_handoff("team_a", "scout", "data")
        tmp_files = list(Path(self.tmpdir).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

    def test_load_empty_dir(self):
        self.assertEqual(load_handoffs("team_a"), [])

    def test_clear_empty_dir(self):
        self.assertEqual(clear_handoffs("team_a"), 0)


if __name__ == "__main__":
    unittest.main()
