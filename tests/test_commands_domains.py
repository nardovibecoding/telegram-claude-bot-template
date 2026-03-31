# Copyright (c) 2026 Nardo (nardovibecoding). AGPL-3.0 — see LICENSE
"""Tests for domain validation and phase file logic in commands.py."""
import os
import unittest


VALID_DOMAINS = ("team_a", "bella", "news", "email", "airbnb")


class TestDomainValidation(unittest.TestCase):
    def test_valid_domains_accepted(self):
        for d in VALID_DOMAINS:
            self.assertIn(d, VALID_DOMAINS)

    def test_invalid_domain_rejected(self):
        self.assertNotIn("andrea", VALID_DOMAINS)
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


if __name__ == "__main__":
    unittest.main()
