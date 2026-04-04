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
    def test_all_teams_have_four_roles(self):
        for team in ("andrea", "bella"):
            roles = [r for t, r in TEAM_DOMAINS.values() if t == team]
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
        save_handoff("andrea", "scout", "Found opportunity in legal vertical")
        handoffs = load_handoffs("andrea", exclude_role="builder")
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0]["role"], "scout")
        self.assertIn("legal vertical", handoffs[0]["content"])

    def test_exclude_own_role(self):
        save_handoff("andrea", "scout", "Scout output")
        handoffs = load_handoffs("andrea", exclude_role="scout")
        self.assertEqual(len(handoffs), 0)

    def test_cross_team_isolation(self):
        save_handoff("andrea", "scout", "Andrea data")
        save_handoff("bella", "scout", "Bella data")
        andrea = load_handoffs("andrea")
        bella = load_handoffs("bella")
        self.assertEqual(len(andrea), 1)
        self.assertEqual(len(bella), 1)
        self.assertIn("Andrea", andrea[0]["content"])
        self.assertIn("Bella", bella[0]["content"])

    def test_content_cap(self):
        long_content = "x" * (MAX_CONTENT_LEN + 500)
        save_handoff("andrea", "scout", long_content)
        handoffs = load_handoffs("andrea")
        self.assertLessEqual(len(handoffs[0]["content"]), MAX_CONTENT_LEN)

    def test_clear_handoffs(self):
        save_handoff("andrea", "scout", "data1")
        save_handoff("andrea", "builder", "data2")
        cleared = clear_handoffs("andrea")
        self.assertEqual(cleared, 2)
        self.assertEqual(len(load_handoffs("andrea")), 0)

    def test_clear_does_not_affect_other_team(self):
        save_handoff("andrea", "scout", "andrea data")
        save_handoff("bella", "scout", "bella data")
        clear_handoffs("andrea")
        self.assertEqual(len(load_handoffs("bella")), 1)

    def test_expired_handoffs_pruned(self):
        save_handoff("andrea", "scout", "old data")
        # Manually backdate the file
        fpath = Path(self.tmpdir) / "andrea_scout.json"
        data = json.loads(fpath.read_text())
        data["timestamp"] = time.time() - TTL_SECONDS - 3600
        fpath.write_text(json.dumps(data))
        handoffs = load_handoffs("andrea")
        self.assertEqual(len(handoffs), 0)
        self.assertFalse(fpath.exists())

    def test_multiple_roles_sorted_newest_first(self):
        save_handoff("andrea", "scout", "first")
        time.sleep(0.05)
        save_handoff("andrea", "critic", "second")
        handoffs = load_handoffs("andrea", exclude_role="builder")
        self.assertEqual(handoffs[0]["role"], "critic")
        self.assertEqual(handoffs[1]["role"], "scout")

    def test_atomic_write(self):
        save_handoff("andrea", "scout", "data")
        # No .tmp files should remain
        tmp_files = list(Path(self.tmpdir).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

    def test_load_empty_dir(self):
        handoffs = load_handoffs("andrea")
        self.assertEqual(handoffs, [])

    def test_clear_empty_dir(self):
        cleared = clear_handoffs("andrea")
        self.assertEqual(cleared, 0)


if __name__ == "__main__":
    unittest.main()
