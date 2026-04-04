# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
"""Tests for handle_tweetdraft callback and tweet_idea_cron helpers."""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tweet_idea_cron import (
    _item_key, _load_seen, _save_seen, _filter_seen, _format_message,
)


class TestItemKey(unittest.TestCase):
    def test_uses_url_when_present(self):
        key = _item_key("Some Title", "https://example.com/story")
        expected = hashlib.md5("https://example.com/story".encode()).hexdigest()[:12]
        self.assertEqual(key, expected)

    def test_falls_back_to_title(self):
        key = _item_key("Some Title", "")
        expected = hashlib.md5("Some Title".encode()).hexdigest()[:12]
        self.assertEqual(key, expected)

    def test_consistent(self):
        self.assertEqual(_item_key("T", "U"), _item_key("T", "U"))

    def test_different_urls_different_keys(self):
        self.assertNotEqual(
            _item_key("T", "https://a.com"),
            _item_key("T", "https://b.com"),
        )


class TestSeenCache(unittest.TestCase):
    def setUp(self):
        self.tmpfile = tempfile.mktemp(suffix=".json")

    def tearDown(self):
        if os.path.exists(self.tmpfile):
            os.unlink(self.tmpfile)

    def test_load_missing_returns_empty(self):
        seen = _load_seen.__wrapped__(self.tmpfile) if hasattr(_load_seen, '__wrapped__') else {}
        self.assertIsInstance(seen, dict)

    def test_save_prunes_expired(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        seen = {"old_key": old_ts, "fresh_key": fresh_ts}
        with patch("tweet_idea_cron.SEEN_CACHE", self.tmpfile):
            _save_seen(seen)
            with open(self.tmpfile) as f:
                saved = json.load(f)
        self.assertNotIn("old_key", saved)
        self.assertIn("fresh_key", saved)

    def test_roundtrip(self):
        ts = datetime.now(timezone.utc).isoformat()
        seen = {"abc123": ts, "item:abc123": {"title": "Test"}}
        with patch("tweet_idea_cron.SEEN_CACHE", self.tmpfile):
            _save_seen(seen)
            with patch("tweet_idea_cron.SEEN_CACHE", self.tmpfile):
                loaded = _load_seen()
        self.assertIn("abc123", loaded)


class TestFilterSeen(unittest.TestCase):
    def _make_item(self, key):
        return {"key": key, "title": "T", "type": "news", "url": "", "summary": "",
                "source": "s", "source_count": 3, "ts": ""}

    def test_filters_known_keys(self):
        seen = {"abc": "2026-01-01T00:00:00+00:00"}
        items = [self._make_item("abc"), self._make_item("xyz")]
        result = _filter_seen(items, seen)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["key"], "xyz")

    def test_empty_seen_returns_all(self):
        items = [self._make_item("a"), self._make_item("b")]
        result = _filter_seen(items, {})
        self.assertEqual(len(result), 2)

    def test_all_seen_returns_empty(self):
        seen = {"a": "t", "b": "t"}
        items = [self._make_item("a"), self._make_item("b")]
        self.assertEqual(_filter_seen(items, seen), [])


class TestFormatMessage(unittest.TestCase):
    def _item(self, item_type="news", source_count=3, url="https://x.com"):
        return {
            "type": item_type,
            "title": "Big AI breakthrough",
            "summary": "Something happened with AI",
            "url": url,
            "source": "BBC",
            "source_count": source_count,
        }

    def test_news_shows_source_count(self):
        msg = _format_message(self._item("news", source_count=5))
        self.assertIn("5 sources", msg)

    def test_evolution_shows_robot_emoji(self):
        msg = _format_message(self._item("evolution"))
        self.assertIn("🤖", msg)

    def test_url_included(self):
        msg = _format_message(self._item(url="https://example.com/story"))
        self.assertIn("https://example.com/story", msg)

    def test_no_url_no_crash(self):
        msg = _format_message(self._item(url=""))
        self.assertIsInstance(msg, str)

    def test_title_always_present(self):
        msg = _format_message(self._item())
        self.assertIn("Big AI breakthrough", msg)


class TestHandleTweetdraft(unittest.IsolatedAsyncioTestCase):
    async def test_unauthorized_user_rejected(self):
        from admin_bot.callbacks import handle_tweetdraft
        query = MagicMock()
        query.from_user.id = 999999  # not ADMIN_USER_ID
        query.answer = AsyncMock()
        query.data = "tweetdraft:abc123"
        update = MagicMock()
        update.callback_query = query
        await handle_tweetdraft(update, MagicMock())
        query.answer.assert_called_once_with("Not authorized")

    async def test_missing_item_sends_error(self):
        from admin_bot.callbacks import handle_tweetdraft
        from admin_bot.config import ADMIN_USER_ID
        query = MagicMock()
        query.from_user.id = ADMIN_USER_ID
        query.answer = AsyncMock()
        query.data = "tweetdraft:nonexistent"
        query.message = MagicMock()
        query.message.reply_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query

        with patch("builtins.open", side_effect=FileNotFoundError):
            await handle_tweetdraft(update, MagicMock())

        query.message.reply_text.assert_called_once()
        call_args = query.message.reply_text.call_args[0][0]
        self.assertIn("not found", call_args.lower())


if __name__ == "__main__":
    unittest.main()
