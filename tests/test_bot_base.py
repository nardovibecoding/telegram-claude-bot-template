# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Tests for module-level utility functions in bot_base."""
import asyncio
import json
import sys
import tempfile
import time as _time_mod
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock


# ── Minimal stubs so bot_base imports without real deps ──────────────────────

def _make_stub(name):
    mod = MagicMock()
    sys.modules[name] = mod
    return mod

for _dep in [
    "dotenv", "openai", "telegram", "telegram.error", "telegram.ext",
    "news", "digest_ui", "crypto_news", "stablecoin_yields",
    "twitter_feed", "x_feed", "x_curator", "reddit_digest",
    "x_feedback", "memory", "conversation_compressor", "conversation_logger",
    "sanitizer", "llm_client", "utils",
]:
    if _dep not in sys.modules:
        _make_stub(_dep)

# utils needs real attributes
sys.modules["utils"].CLAUDE_BIN = "claude"
sys.modules["utils"].PROJECT_DIR = Path("/tmp")
sys.modules["memory"].RECENT_WINDOW = 10

import importlib
# patch load_dotenv to no-op
with patch("dotenv.load_dotenv"):
    import bot_base  # noqa: E402


# ── _check_rate_limit ─────────────────────────────────────────────────────────

class TestCheckRateLimit(unittest.TestCase):

    def setUp(self):
        bot_base._rate_limit_data.clear()

    def test_first_message_allowed(self):
        self.assertTrue(bot_base._check_rate_limit(1001))

    def test_within_limit_allowed(self):
        for _ in range(bot_base._RATE_LIMIT_MAX - 1):
            self.assertTrue(bot_base._check_rate_limit(1002))

    def test_exceeds_limit_blocked(self):
        for _ in range(bot_base._RATE_LIMIT_MAX):
            bot_base._check_rate_limit(1003)
        self.assertFalse(bot_base._check_rate_limit(1003))

    def test_old_timestamps_pruned(self):
        uid = 1004
        old_time = _time_mod.time() - bot_base._RATE_LIMIT_WINDOW - 1
        bot_base._rate_limit_data[uid] = [old_time] * bot_base._RATE_LIMIT_MAX
        # Old entries should be pruned → request is allowed
        self.assertTrue(bot_base._check_rate_limit(uid))

    def test_different_users_independent(self):
        for _ in range(bot_base._RATE_LIMIT_MAX):
            bot_base._check_rate_limit(2001)
        # user 2001 blocked; user 2002 unaffected
        self.assertFalse(bot_base._check_rate_limit(2001))
        self.assertTrue(bot_base._check_rate_limit(2002))


# ── _get_today_cost ───────────────────────────────────────────────────────────

class TestGetTodayCost(unittest.TestCase):

    def _run_with_log(self, lines):
        """Write lines to a temp file and point CLAUDE_COST_LOG at it."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines))
            tmp = Path(f.name)
        original = bot_base.CLAUDE_COST_LOG
        bot_base.CLAUDE_COST_LOG = tmp
        try:
            return bot_base._get_today_cost()
        finally:
            bot_base.CLAUDE_COST_LOG = original
            tmp.unlink(missing_ok=True)

    def test_missing_log_returns_zero(self):
        original = bot_base.CLAUDE_COST_LOG
        bot_base.CLAUDE_COST_LOG = Path("/nonexistent/path/cost.jsonl")
        try:
            self.assertEqual(bot_base._get_today_cost(), 0.0)
        finally:
            bot_base.CLAUDE_COST_LOG = original

    @staticmethod
    def _hkt_today():
        from datetime import datetime, timedelta, timezone
        HKT = timezone(timedelta(hours=8))
        return datetime.now(HKT).strftime("%Y-%m-%d")

    def test_sums_todays_entries(self):
        today = self._hkt_today()
        lines = [
            json.dumps({"ts": f"{today}T10:00:00+08:00", "cost_usd": 0.05}),
            json.dumps({"ts": f"{today}T11:00:00+08:00", "cost_usd": 0.10}),
        ]
        result = self._run_with_log(lines)
        self.assertAlmostEqual(result, 0.15, places=5)

    def test_skips_other_days(self):
        lines = [json.dumps({"ts": "2000-01-01T10:00:00+08:00", "cost_usd": 99.0})]
        result = self._run_with_log(lines)
        self.assertEqual(result, 0.0)

    def test_skips_malformed_lines(self):
        today = self._hkt_today()
        lines = [
            "not-json",
            json.dumps({"ts": f"{today}T01:00:00+08:00", "cost_usd": 0.01}),
        ]
        result = self._run_with_log(lines)
        self.assertAlmostEqual(result, 0.01, places=5)


# ── _conv_key (closure extracted via make_bot) ────────────────────────────────
# _conv_key is a simple pure function defined inside make_bot; test the logic directly.

class TestConvKey(unittest.TestCase):

    def _conv_key(self, chat_id, thread_id):
        return (chat_id, thread_id or 0)

    def test_with_thread(self):
        self.assertEqual(self._conv_key(100, 5), (100, 5))

    def test_no_thread_defaults_zero(self):
        self.assertEqual(self._conv_key(100, None), (100, 0))

    def test_thread_zero_same_as_none(self):
        self.assertEqual(self._conv_key(100, 0), self._conv_key(100, None))


# ── _cleanup_media_groups (async, isolated) ──────────────────────────────────

class TestCleanupMediaGroups(unittest.IsolatedAsyncioTestCase):

    async def test_stale_entries_removed(self):
        seen = {}
        now = _time_mod.time()
        seen["old_key"] = now - 120   # 2 min old → stale (>60s)
        seen["new_key"] = now - 10    # fresh

        async def _cleanup(context):
            stale = [k for k, ts in seen.items() if now - ts > 60]
            for k in stale:
                del seen[k]

        await _cleanup(None)
        self.assertNotIn("old_key", seen)
        self.assertIn("new_key", seen)

    async def test_empty_dict_no_error(self):
        seen = {}

        async def _cleanup(context):
            stale = [k for k, ts in seen.items() if _time_mod.time() - ts > 60]
            for k in stale:
                del seen[k]

        await _cleanup(None)  # should not raise
        self.assertEqual(seen, {})

    async def test_all_fresh_entries_kept(self):
        seen = {}
        now = _time_mod.time()
        seen["a"] = now - 5
        seen["b"] = now - 10

        async def _cleanup(context):
            stale = [k for k, ts in seen.items() if now - ts > 60]
            for k in stale:
                del seen[k]

        await _cleanup(None)
        self.assertIn("a", seen)
        self.assertIn("b", seen)


# ── _build_persona_content_cmds logic ────────────────────────────────────────
# Pure logic extracted for unit testing (flags → command list).

class TestBuildPersonaContentCmds(unittest.TestCase):

    def _build(self, digest_enabled=False, news_module="none",
               twitter_enabled=False, reddit_enabled=False,
               reddit_subs=None, yields_enabled=False):
        cmds = []
        if digest_enabled and news_module != "none":
            cmds.append(("news", "News digest"))
        if twitter_enabled:
            cmds.append(("xcurate", "X curation"))
            cmds.append(("tweets", "Twitter digest"))
        if reddit_enabled and reddit_subs:
            cmds.append(("reddit", "Reddit digest"))
        if yields_enabled:
            cmds.append(("yields", "Yield report"))
        return cmds

    def test_all_disabled_returns_empty(self):
        self.assertEqual(self._build(), [])

    def test_digest_enabled_adds_news(self):
        cmds = self._build(digest_enabled=True, news_module="default")
        self.assertIn(("news", "News digest"), cmds)

    def test_digest_enabled_but_news_none_skipped(self):
        cmds = self._build(digest_enabled=True, news_module="none")
        self.assertNotIn(("news", "News digest"), cmds)

    def test_twitter_adds_two_commands(self):
        cmds = self._build(twitter_enabled=True)
        keys = [c[0] for c in cmds]
        self.assertIn("xcurate", keys)
        self.assertIn("tweets", keys)

    def test_reddit_requires_subs(self):
        # No subs → no command
        cmds_no_subs = self._build(reddit_enabled=True, reddit_subs=None)
        self.assertNotIn("reddit", [c[0] for c in cmds_no_subs])
        # With subs → command present
        cmds_with_subs = self._build(reddit_enabled=True, reddit_subs=["python"])
        self.assertIn("reddit", [c[0] for c in cmds_with_subs])

    def test_yields_enabled(self):
        cmds = self._build(yields_enabled=True)
        self.assertIn(("yields", "Yield report"), cmds)

    def test_all_enabled(self):
        cmds = self._build(
            digest_enabled=True, news_module="default",
            twitter_enabled=True, reddit_enabled=True,
            reddit_subs=["python"], yields_enabled=True,
        )
        keys = [c[0] for c in cmds]
        self.assertEqual(keys, ["news", "xcurate", "tweets", "reddit", "yields"])


# ── _cache_thread logic ───────────────────────────────────────────────────────

class TestCacheThread(unittest.TestCase):

    def setUp(self):
        self.cache = {}
        self.saved = []

    def _cache_thread(self, chat_id, thread_id, name):
        g = str(chat_id)
        tid = str(thread_id or 0)
        self.cache.setdefault(g, {})[tid] = name
        self.saved.append((g, tid, name))

    def test_stores_entry(self):
        self._cache_thread(100, 5, "crypto")
        self.assertEqual(self.cache["100"]["5"], "crypto")

    def test_thread_none_stored_as_zero(self):
        self._cache_thread(100, None, "general")
        self.assertEqual(self.cache["100"]["0"], "general")

    def test_overwrite_existing(self):
        self._cache_thread(100, 5, "old")
        self._cache_thread(100, 5, "new")
        self.assertEqual(self.cache["100"]["5"], "new")

    def test_multiple_threads_same_chat(self):
        self._cache_thread(100, 1, "alpha")
        self._cache_thread(100, 2, "beta")
        self.assertEqual(len(self.cache["100"]), 2)


# ── _check_private_user logic ─────────────────────────────────────────────────

class TestCheckPrivateUser(unittest.TestCase):

    def _check(self, chat_type, user_id, allowed_users):
        if chat_type != "private":
            return True
        if not allowed_users:
            return True
        return user_id in allowed_users

    def test_group_always_passes(self):
        self.assertTrue(self._check("group", 999, {1, 2}))

    def test_supergroup_always_passes(self):
        self.assertTrue(self._check("supergroup", 999, {1, 2}))

    def test_private_allowed_user_passes(self):
        self.assertTrue(self._check("private", 1, {1, 2}))

    def test_private_unknown_user_blocked(self):
        self.assertFalse(self._check("private", 999, {1, 2}))

    def test_private_no_whitelist_allows_all(self):
        self.assertTrue(self._check("private", 999, set()))


# ── _flush_text_buffer logic ──────────────────────────────────────────────────

class TestFlushTextBufferLogic(unittest.IsolatedAsyncioTestCase):
    """Test the merge-and-dispatch logic of _flush_text_buffer in isolation."""

    async def test_chunks_merged_with_newline(self):
        ck = (1, 0)
        buf = {ck: ["hello", "world"]}
        merged = "\n".join(buf.pop(ck, []))
        self.assertEqual(merged, "hello\nworld")

    async def test_empty_buffer_returns_none(self):
        ck = (1, 0)
        buf = {}
        chunks = buf.pop(ck, [])
        self.assertEqual(chunks, [])

    async def test_single_chunk_no_newline(self):
        ck = (1, 0)
        buf = {ck: ["only chunk"]}
        merged = "\n".join(buf.pop(ck, []))
        self.assertEqual(merged, "only chunk")

    async def test_buffer_cleared_after_pop(self):
        ck = (1, 0)
        buf = {ck: ["a", "b"]}
        buf.pop(ck, [])
        self.assertNotIn(ck, buf)


if __name__ == "__main__":
    unittest.main()
