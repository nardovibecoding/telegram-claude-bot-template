# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
import sys
import os
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from news import pick_top_stories, process_category, generate_full_digest, Article


def _make_articles(n: int) -> list[Article]:
    return [
        Article(title=f"Headline {i}", summary="summary", url=f"http://example.com/{i}", source="BBC")
        for i in range(n)
    ]


class TestPickTopStories(unittest.TestCase):
    def test_fewer_than_n_returns_all(self):
        articles = _make_articles(3)
        result = pick_top_stories("A", "X1", articles, n=5)
        self.assertEqual(result, [0, 1, 2])

    def test_exactly_n_returns_all(self):
        articles = _make_articles(5)
        result = pick_top_stories("A", "X1", articles, n=5)
        self.assertEqual(result, [0, 1, 2, 3, 4])

    @patch("news.chat_completion", return_value="1,3,5")
    def test_parses_llm_indices(self, _mock):
        articles = _make_articles(10)
        result = pick_top_stories("A", "X1", articles, n=3)
        self.assertEqual(result, [0, 2, 4])  # 1-based → 0-based

    @patch("news.chat_completion", side_effect=Exception("API error"))
    def test_fallback_on_error(self, _mock):
        articles = _make_articles(10)
        result = pick_top_stories("A", "X1", articles, n=5)
        self.assertEqual(result, [0, 1, 2, 3, 4])

    @patch("news.chat_completion", return_value="99,100")  # out-of-range
    def test_ignores_out_of_range_indices(self, _mock):
        articles = _make_articles(5)
        result = pick_top_stories("A", "X1", articles, n=3)
        # All indices out of range → fallback to first n
        self.assertEqual(result, [0, 1, 2])

    def test_no_client_param(self):
        """Signature must not require a client argument."""
        import inspect
        sig = inspect.signature(pick_top_stories)
        self.assertNotIn("client", sig.parameters)


class TestProcessCategory(unittest.TestCase):
    def test_empty_articles_returns_message(self):
        result = process_category("A", "X1", [])
        self.assertIn("No articles", result)

    @patch("news.chat_completion", return_value="Bot analysis: market volatility today.")
    def test_returns_llm_output(self, _mock):
        articles = _make_articles(5)
        result = process_category("A", "X1", articles)
        self.assertEqual(result, "Bot analysis: market volatility today.")

    @patch("news.chat_completion", return_value="⚠️ All models failed: timeout")
    def test_warning_response_returned(self, _mock):
        articles = _make_articles(5)
        result = process_category("A", "X1", articles)
        self.assertTrue(result.startswith("⚠️"))

    def test_no_client_param(self):
        """Signature must not require a client argument."""
        import inspect
        sig = inspect.signature(process_category)
        self.assertNotIn("client", sig.parameters)


class TestGenerateFullDigestSignature(unittest.TestCase):
    def test_api_key_is_optional(self):
        """generate_full_digest must accept zero args (backward compat for old callers too)."""
        import inspect
        sig = inspect.signature(generate_full_digest)
        param = sig.parameters.get("api_key")
        self.assertIsNotNone(param)
        self.assertIsNot(param.default, inspect.Parameter.empty)


if __name__ == "__main__":
    unittest.main()
