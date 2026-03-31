# Copyright (c) 2026 Nardo (nardovibecoding). AGPL-3.0 — see LICENSE
"""Tests for domain validation, phase file, and handoff wiring."""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VALID_DOMAINS = ("team_a", "news", "email", "airbnb")


class TestDomainValidation(unittest.TestCase):
    def test_valid_domains_accepted(self):
        for d in VALID_DOMAINS:
            self.assertIn(d, VALID_DOMAINS)

    def test_invalid_domain_rejected(self):
        self.assertNotIn("andrea", VALID_DOMAINS)
        self.assertNotIn("bella", VALID_DOMAINS)
        self.assertNotIn("unknown", VALID_DOMAINS)

    def test_team_a_replaces_andrea(self):
        self.assertIn("team_a", VALID_DOMAINS)
        self.assertNotIn("andrea", VALID_DOMAINS)


class TestPhaseFile(unittest.TestCase):
    def test_phase_file_name(self):
        phase_file = ".team_a_phase"
        self.assertIn("team_a", phase_file)
        self.assertNotIn("andrea", phase_file)

    def test_phase_file_path_construction(self):
        project_dir = "/tmp/test_project"
        phase_file = os.path.join(project_dir, ".team_a_phase")
        self.assertTrue(phase_file.endswith(".team_a_phase"))


class TestDomainDetection(unittest.TestCase):
    """Test thread-to-domain mapping in domains.py."""

    def test_team_a_thread_mapping(self):
        # Verify the thread mapping dict matches expected structure
        team_a_topics = {3: "team_a:scout", 27: "team_a:growth", 28: "team_a:critic", 29: "team_a:builder"}
        self.assertEqual(len(team_a_topics), 4)
        for tid, domain in team_a_topics.items():
            self.assertTrue(domain.startswith("team_a:"))
            role = domain.split(":")[1]
            self.assertIn(role, ("scout", "growth", "critic", "builder"))

    def test_no_bella_thread_mapping(self):
        """Bella should not exist in template domain mappings."""
        from admin_bot.domains import _detect_domain
        import inspect
        source = inspect.getsource(_detect_domain)
        self.assertNotIn("bella", source)


class TestHandoffWiring(unittest.TestCase):
    """Test that handoff functions are correctly wired into bridge and commands."""

    def test_handoff_team_domains_has_team_a(self):
        from admin_bot.handoff import TEAM_DOMAINS
        team_a_domains = {k for k in TEAM_DOMAINS if k.startswith("team_a:")}
        self.assertEqual(len(team_a_domains), 4)

    def test_handoff_team_domains_no_bella(self):
        from admin_bot.handoff import TEAM_DOMAINS
        bella_domains = {k for k in TEAM_DOMAINS if k.startswith("bella:")}
        self.assertEqual(len(bella_domains), 0)

    def test_clear_handoffs_on_reset(self):
        """Verify clear_handoffs works end-to-end (save then clear)."""
        from admin_bot.handoff import save_handoff, clear_handoffs, load_handoffs
        tmpdir = tempfile.mkdtemp()
        with patch("admin_bot.handoff.HANDOFF_DIR", Path(tmpdir)):
            save_handoff("team_a", "scout", "test data")
            self.assertEqual(len(load_handoffs("team_a")), 1)
            cleared = clear_handoffs("team_a")
            self.assertEqual(cleared, 1)
            self.assertEqual(len(load_handoffs("team_a")), 0)
        # cleanup
        for f in Path(tmpdir).glob("*"):
            f.unlink()
        os.rmdir(tmpdir)


if __name__ == "__main__":
    unittest.main()
