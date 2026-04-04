# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Tests for opencli_client.py — stdlib unittest only."""
import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import opencli_client as oc


class TestHealth(unittest.TestCase):

    def test_returns_true_when_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        with patch("opencli_client._session.get", return_value=mock_resp):
            self.assertTrue(oc.health())

    def test_returns_false_when_not_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False}
        with patch("opencli_client._session.get", return_value=mock_resp):
            self.assertFalse(oc.health())

    def test_returns_false_on_connection_error(self):
        with patch("opencli_client._session.get", side_effect=Exception("refused")):
            self.assertFalse(oc.health())

    def test_returns_false_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("opencli_client._session.get", return_value=mock_resp):
            self.assertFalse(oc.health())


class TestGetRedditPosts(unittest.TestCase):

    def _mock_response(self, data, status=200):
        m = MagicMock()
        m.status_code = status
        m.json.return_value = data
        return m

    def test_parses_list_of_posts(self):
        posts_data = [
            {
                "subreddit": "LocalLLaMA",
                "title": "New model released",
                "selftext": "Check out this new model",
                "url": "https://example.com",
                "permalink": "/r/LocalLLaMA/comments/abc/new_model/",
                "score": 150,
                "upvote_ratio": 0.95,
                "num_comments": 42,
                "author": "testuser",
                "created_utc": 1711900000,
                "is_self": False,
                "link_flair_text": "News",
            }
        ]
        with patch("opencli_client._session.get", return_value=self._mock_response(posts_data)):
            result = oc.get_reddit_posts("LocalLLaMA", limit=10)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "New model released")
        self.assertEqual(result[0]["score"], 150)
        self.assertEqual(result[0]["subreddit"], "LocalLLaMA")
        self.assertTrue(result[0]["permalink"].startswith("https://"))
        self.assertEqual(result[0]["flair"], "News")

    def test_returns_empty_on_error_response(self):
        with patch("opencli_client._session.get", return_value=self._mock_response({"error": "timeout"})):
            result = oc.get_reddit_posts("test")
        self.assertEqual(result, [])

    def test_returns_empty_on_http_error(self):
        with patch("opencli_client._session.get", return_value=self._mock_response({}, status=502)):
            result = oc.get_reddit_posts("test")
        self.assertEqual(result, [])

    def test_returns_empty_on_timeout(self):
        import requests as req
        with patch("opencli_client._session.get", side_effect=req.Timeout("timeout")):
            result = oc.get_reddit_posts("test")
        self.assertEqual(result, [])

    def test_cutoff_filters_old_posts(self):
        posts_data = [
            {"title": "Old", "created_utc": 1000, "score": 1},
            {"title": "New", "created_utc": 9999, "score": 1},
        ]
        with patch("opencli_client._session.get", return_value=self._mock_response(posts_data)):
            result = oc.get_reddit_posts("test", cutoff=5000)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "New")


class TestScrapePage(unittest.TestCase):

    def test_returns_content(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"content": "Hello world article text"}
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.scrape_page("https://example.com")
        self.assertEqual(result, "Hello world article text")

    def test_truncates_to_max_chars(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"content": "A" * 5000}
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.scrape_page("https://example.com", max_chars=100)
        self.assertEqual(len(result), 100)

    def test_returns_empty_on_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.scrape_page("https://example.com")
        self.assertEqual(result, "")

    def test_returns_empty_on_timeout(self):
        import requests as req
        with patch("opencli_client._session.get", side_effect=req.Timeout("timeout")):
            result = oc.scrape_page("https://example.com")
        self.assertEqual(result, "")


class TestTwitterSearch(unittest.TestCase):

    def test_returns_list(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"text": "tweet1"}, {"text": "tweet2"}]
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.twitter_search("AI agents")
        self.assertEqual(len(result), 2)

    def test_returns_empty_on_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": "not connected"}
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.twitter_search("test")
        self.assertEqual(result, [])


class TestYoutubeTranscript(unittest.TestCase):

    def test_returns_joined_text(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"text": "Hello"}, {"text": "world"}]
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.youtube_transcript("abc123")
        self.assertEqual(result, "Hello\nworld")

    def test_returns_empty_on_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("opencli_client._session.get", return_value=mock_resp):
            result = oc.youtube_transcript("abc123")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
