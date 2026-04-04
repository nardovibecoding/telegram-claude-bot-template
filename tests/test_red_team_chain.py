# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from outreach.red_team_chain import (
    _write_prompt_version,
    rollback_prompt,
    _build_summary,
    _CRITIQUE_TEMPLATES,
    _APPLY_TEMPLATES,
)


class TestWritePromptVersion(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.versions_path = os.path.join(self.tmpdir, "versions.jsonl")
        self.current_path = os.path.join(self.tmpdir, "stevie_current.md")

        # Patch paths
        import outreach.red_team_chain as chain_mod
        self._orig_root = chain_mod._PROJECT_ROOT
        # Patch by monkey-patching the function's globals — use a temp prompts dir
        self._prompts_dir = self.tmpdir
        chain_mod._PROJECT_ROOT = os.path.dirname(self.tmpdir)

        # Create a mock prompts dir matching expected structure
        self._mock_prompts = os.path.join(os.path.dirname(self.tmpdir), "outreach", "prompts")
        os.makedirs(self._mock_prompts, exist_ok=True)
        self._versions_path = os.path.join(self._mock_prompts, "versions.jsonl")
        self._current_path = os.path.join(self._mock_prompts, "stevie_current.md")
        with open(self._current_path, "w") as f:
            f.write("You are Stevie.")

    def tearDown(self):
        import outreach.red_team_chain as chain_mod
        chain_mod._PROJECT_ROOT = self._orig_root
        import shutil
        shutil.rmtree(os.path.dirname(self.tmpdir), ignore_errors=True)

    def test_creates_versions_jsonl(self):
        _write_prompt_version(0.68, "initial test")
        self.assertTrue(os.path.exists(self._versions_path))

    def test_first_version_is_1(self):
        _write_prompt_version(0.68, "initial test")
        with open(self._versions_path) as f:
            entry = json.loads(f.read().strip())
        self.assertEqual(entry["version"], 1)

    def test_increments_version(self):
        _write_prompt_version(0.68, "first")
        _write_prompt_version(0.75, "second")
        with open(self._versions_path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["version"], 2)

    def test_stores_pass_rate(self):
        _write_prompt_version(0.85, "test fix")
        with open(self._versions_path) as f:
            entry = json.loads(f.read().strip())
        self.assertAlmostEqual(entry["pass_rate"], 0.85)

    def test_stores_dims(self):
        dims = {"safety": 0.9, "helpfulness": 0.8, "persona_match": 0.7, "deal_quality": 0.6}
        _write_prompt_version(0.75, "with dims", dims=dims)
        with open(self._versions_path) as f:
            entry = json.loads(f.read().strip())
        self.assertEqual(entry["dims"], dims)

    def test_snapshots_current_prompt(self):
        _write_prompt_version(0.68, "snapshot test")
        versioned = os.path.join(self._mock_prompts, "stevie_v001.md")
        self.assertTrue(os.path.exists(versioned))
        with open(versioned) as f:
            self.assertEqual(f.read(), "You are Stevie.")


class TestRollbackPrompt(unittest.TestCase):
    def setUp(self):
        import outreach.red_team_chain as chain_mod
        self._orig_root = chain_mod._PROJECT_ROOT
        self._mock_root = tempfile.mkdtemp()
        self._mock_prompts = os.path.join(self._mock_root, "outreach", "prompts")
        os.makedirs(self._mock_prompts, exist_ok=True)
        chain_mod._PROJECT_ROOT = self._mock_root

        # Create v001
        self._v001 = os.path.join(self._mock_prompts, "stevie_v001.md")
        with open(self._v001, "w") as f:
            f.write("original prompt")
        self._current = os.path.join(self._mock_prompts, "stevie_current.md")
        with open(self._current, "w") as f:
            f.write("modified prompt")

    def tearDown(self):
        import outreach.red_team_chain as chain_mod
        chain_mod._PROJECT_ROOT = self._orig_root
        import shutil
        shutil.rmtree(self._mock_root, ignore_errors=True)

    def test_rollback_restores_content(self):
        result = rollback_prompt(1)
        self.assertTrue(result)
        with open(self._current) as f:
            self.assertEqual(f.read(), "original prompt")

    def test_rollback_missing_version_returns_false(self):
        result = rollback_prompt(99)
        self.assertFalse(result)


class TestBuildSummary(unittest.TestCase):
    def test_pass_rate_calculation(self):
        results = [
            {"grade": "PASS"}, {"grade": "PASS"}, {"grade": "FAIL"},
            {"grade": "BORDERLINE"}, {"grade": "PASS"},
        ]
        summary = _build_summary(results, "R1")
        self.assertEqual(summary["pass"], 3)
        self.assertEqual(summary["fail"], 1)
        self.assertEqual(summary["borderline"], 1)
        self.assertAlmostEqual(summary["pass_rate"], 60.0)

    def test_empty_results(self):
        summary = _build_summary([], "R1")
        self.assertEqual(summary["pass_rate"], 0)
        self.assertEqual(summary["total"], 0)


class TestTemplates(unittest.TestCase):
    def test_critique_templates_count(self):
        self.assertEqual(len(_CRITIQUE_TEMPLATES), 3)

    def test_apply_templates_count(self):
        self.assertEqual(len(_APPLY_TEMPLATES), 2)

    def test_critique_templates_have_placeholders(self):
        for t in _CRITIQUE_TEMPLATES:
            self.assertIn("{failures}", t)
            self.assertIn("{prompt}", t)

    def test_apply_templates_have_placeholders(self):
        for t in _APPLY_TEMPLATES:
            self.assertIn("{proposals}", t)


if __name__ == "__main__":
    unittest.main()
