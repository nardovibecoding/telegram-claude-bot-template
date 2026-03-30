# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub out Telegram dependencies so auto_reply.py is importable without a VPS install
import types
for _mod in ["telethon", "telethon.tl", "telethon.tl.types", "telethon.tl.functions",
             "telethon.tl.functions.channels", "telethon.events", "telethon.errors",
             "telethon.errors.rpcerrorlist"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
# Minimal stubs for names auto_reply imports from telethon
sys.modules["telethon"].TelegramClient = object
sys.modules["telethon"].events = sys.modules["telethon.events"]
sys.modules["telethon.events"].NewMessage = lambda **kw: (lambda f: f)
sys.modules["telethon.errors.rpcerrorlist"].FloodWaitError = Exception
sys.modules["telethon.tl.types"].Channel = object
sys.modules["telethon.tl.functions.channels"].GetFullChannelRequest = object


class TestEmitReward(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rewards_path = os.path.join(self.tmpdir, "rewards.jsonl")

    def _call(self, signals):
        with patch("outreach.auto_reply.os.path.dirname", return_value=self.tmpdir):
            from outreach.auto_reply import emit_reward
            return emit_reward(111, 222, "minimax", signals)

    def test_baseline_no_signals(self):
        score = self._call({})
        self.assertAlmostEqual(score, 0.5)

    def test_continued_adds_score(self):
        score = self._call({"continued": True})
        self.assertAlmostEqual(score, 0.7)

    def test_deal_signal_adds_score(self):
        score = self._call({"deal_signal": True})
        self.assertAlmostEqual(score, 0.7)

    def test_silence_reduces_score(self):
        score = self._call({"silence_after": True})
        self.assertAlmostEqual(score, 0.3)

    def test_hard_violation_reduces_score(self):
        score = self._call({"hard_violation": True})
        self.assertAlmostEqual(score, 0.2)

    def test_score_clamped_to_one(self):
        score = self._call({"continued": True, "deal_signal": True})
        self.assertLessEqual(score, 1.0)

    def test_score_clamped_to_zero(self):
        score = self._call({"silence_after": True, "hard_violation": True})
        self.assertGreaterEqual(score, 0.0)

    def test_writes_jsonl(self):
        with patch("outreach.auto_reply.os.path.dirname", return_value=self.tmpdir):
            from outreach.auto_reply import emit_reward
            emit_reward(111, 222, "minimax", {"deal_signal": True})
        path = os.path.join(self.tmpdir, "rewards.jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            entry = json.loads(f.read().strip())
        self.assertEqual(entry["chat_id"], 111)
        self.assertEqual(entry["model"], "minimax")
        self.assertIn("reward", entry)

    def test_never_raises(self):
        # Even with a bad path it should not raise
        with patch("outreach.auto_reply.os.path.dirname", return_value="/nonexistent/path"):
            from outreach.auto_reply import emit_reward
            score = emit_reward(1, 2, "model", {})
        self.assertIsNotNone(score)


class TestLogTelemetry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_writes_jsonl(self):
        with patch("outreach.auto_reply.os.path.dirname", return_value=self.tmpdir):
            from outreach.auto_reply import _log_telemetry
            _log_telemetry("minimax", 340.5, True, 0, False)
        path = os.path.join(self.tmpdir, "telemetry.jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            entry = json.loads(f.read().strip())
        self.assertEqual(entry["model"], "minimax")
        self.assertEqual(entry["latency_ms"], 341)
        self.assertTrue(entry["success"])
        self.assertEqual(entry["violations"], 0)
        self.assertFalse(entry["retried"])

    def test_never_raises(self):
        with patch("outreach.auto_reply.os.path.dirname", return_value="/nonexistent"):
            from outreach.auto_reply import _log_telemetry
            _log_telemetry("model", 100, True, 0, False)  # must not raise


class TestRewardAggregator(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.rewards_path = os.path.join(self.tmpdir, "rewards.jsonl")
        entries = [
            {"ts": 1, "chat_id": 1, "msg_id": 1, "model": "minimax",
             "signals": {"deal_signal": True}, "reward": 0.7},
            {"ts": 2, "chat_id": 2, "msg_id": 2, "model": "minimax",
             "signals": {"hard_violation": True}, "reward": 0.2},
            {"ts": 3, "chat_id": 3, "msg_id": 3, "model": "kimi",
             "signals": {}, "reward": 0.5},
        ]
        with open(self.rewards_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_load_rewards(self):
        with patch("outreach.reward_aggregator.REWARDS_PATH", self.rewards_path):
            from outreach.reward_aggregator import load_rewards
            rewards = load_rewards()
        self.assertEqual(len(rewards), 3)

    def test_aggregate_runs_without_error(self):
        with patch("outreach.reward_aggregator.REWARDS_PATH", self.rewards_path):
            with patch("outreach.reward_aggregator.VERSIONS_PATH", "/nonexistent"):
                from outreach.reward_aggregator import aggregate
                aggregate()  # should not raise


class TestTelemetryReport(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tel_path = os.path.join(self.tmpdir, "telemetry.jsonl")
        entries = [
            {"ts": 1, "model": "minimax", "latency_ms": 300, "success": True,
             "violations": 0, "retried": False},
            {"ts": 2, "model": "minimax", "latency_ms": 500, "success": True,
             "violations": 1, "retried": True},
            {"ts": 3, "model": "kimi", "latency_ms": 200, "success": False,
             "violations": 0, "retried": False},
        ]
        with open(self.tel_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_report_runs_without_error(self):
        with patch("outreach.telemetry_report.TELEMETRY_PATH", self.tel_path):
            from outreach.telemetry_report import report
            report()  # should not raise

    def test_empty_telemetry(self):
        empty = os.path.join(self.tmpdir, "empty.jsonl")
        with patch("outreach.telemetry_report.TELEMETRY_PATH", empty):
            from outreach.telemetry_report import report
            report()  # should not raise on missing file


if __name__ == "__main__":
    unittest.main()
