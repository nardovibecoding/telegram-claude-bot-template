# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Tests for camofox_client.py — stdlib unittest only."""
import json
import sys
import os
import unittest
from unittest.mock import patch, MagicMock, ANY

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camofox_client as cc


class TestHealth(unittest.TestCase):

    def test_returns_true_when_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        with patch("camofox_client.requests.get", return_value=mock_resp):
            self.assertTrue(cc.health())

    def test_returns_false_when_status_not_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False}
        with patch("camofox_client.requests.get", return_value=mock_resp):
            self.assertFalse(cc.health())

    def test_returns_false_on_connection_error(self):
        with patch("camofox_client.requests.get", side_effect=Exception("refused")):
            self.assertFalse(cc.health())

    def test_returns_false_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("camofox_client.requests.get", return_value=mock_resp):
            self.assertFalse(cc.health())


class TestCreateTab(unittest.TestCase):

    def test_returns_tab_id_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"tabId": "abc123"}
        with patch("camofox_client.requests.post", return_value=mock_resp):
            result = cc._create_tab("user1", "https://example.com")
        self.assertEqual(result, "abc123")

    def test_returns_none_on_error_status(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "server error"
        with patch("camofox_client.requests.post", return_value=mock_resp):
            result = cc._create_tab("user1", "https://example.com")
        self.assertIsNone(result)

    def test_returns_none_on_exception(self):
        with patch("camofox_client.requests.post", side_effect=Exception("timeout")):
            result = cc._create_tab("user1", "https://example.com")
        self.assertIsNone(result)


class TestDeleteTab(unittest.TestCase):

    def test_silent_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("camofox_client.requests.delete", return_value=mock_resp):
            cc._delete_tab("abc123", "user1")  # should not raise

    def test_silent_on_exception(self):
        with patch("camofox_client.requests.delete", side_effect=Exception("gone")):
            cc._delete_tab("abc123", "user1")  # should not raise


class TestScrapePage(unittest.TestCase):

    def _mock_tab_flow(self, snapshot_text):
        """Helper: mock create_tab → snapshot → delete_tab."""
        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {"tabId": "t1"}

        snap_resp = MagicMock()
        snap_resp.status_code = 200
        snap_resp.json.return_value = {"snapshot": snapshot_text}

        del_resp = MagicMock()
        del_resp.status_code = 200

        return [create_resp, del_resp], snap_resp

    def test_returns_text_from_snapshot(self):
        post_resps, snap_resp = self._mock_tab_flow("Hello world content here")
        with patch("camofox_client.requests.post", side_effect=post_resps), \
             patch("camofox_client.requests.get", return_value=snap_resp), \
             patch("camofox_client.time.sleep"):
            result = cc.scrape_page("https://example.com")
        self.assertIn("Hello world", result)

    def test_strips_element_refs(self):
        post_resps, snap_resp = self._mock_tab_flow("[button e1] Click me [link e2] Go here")
        with patch("camofox_client.requests.post", side_effect=post_resps), \
             patch("camofox_client.requests.get", return_value=snap_resp), \
             patch("camofox_client.time.sleep"):
            result = cc.scrape_page("https://example.com")
        self.assertNotIn("e1", result)
        self.assertNotIn("[button", result)

    def test_returns_empty_when_tab_creation_fails(self):
        with patch("camofox_client._create_tab", return_value=None):
            result = cc.scrape_page("https://example.com")
        self.assertEqual(result, "")

    def test_respects_max_chars(self):
        long_text = "x" * 5000
        post_resps, snap_resp = self._mock_tab_flow(long_text)
        with patch("camofox_client.requests.post", side_effect=post_resps), \
             patch("camofox_client.requests.get", return_value=snap_resp), \
             patch("camofox_client.time.sleep"):
            result = cc.scrape_page("https://example.com", max_chars=100)
        self.assertLessEqual(len(result), 100)

    def test_deletes_tab_even_on_snapshot_error(self):
        with patch("camofox_client._create_tab", return_value="t1"), \
             patch("camofox_client._get_snapshot", return_value=""), \
             patch("camofox_client._delete_tab") as mock_del, \
             patch("camofox_client.time.sleep"):
            cc.scrape_page("https://example.com")
        mock_del.assert_called_once_with("t1", ANY)


class TestParseChildren(unittest.TestCase):

    def _make_child(self, **kwargs):
        defaults = {
            "title": "Test Post", "score": 100, "upvote_ratio": 0.95,
            "num_comments": 10, "author": "testuser", "created_utc": 1700000000,
            "is_self": True, "subreddit": "python", "permalink": "/r/python/comments/abc",
            "selftext": "body text", "url": "https://reddit.com/r/python/comments/abc",
            "link_flair_text": None,
        }
        defaults.update(kwargs)
        return {"data": defaults}

    def test_parses_basic_post(self):
        children = [self._make_child()]
        posts = cc._parse_children(children, cutoff=0, sub="python")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "Test Post")
        self.assertEqual(posts[0]["score"], 100)

    def test_filters_by_cutoff(self):
        children = [
            self._make_child(created_utc=1000),   # old
            self._make_child(created_utc=9999999999),  # new
        ]
        posts = cc._parse_children(children, cutoff=5000, sub="python")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["created_utc"], 9999999999)

    def test_cutoff_zero_includes_all(self):
        children = [self._make_child(created_utc=1), self._make_child(created_utc=2)]
        posts = cc._parse_children(children, cutoff=0, sub="python")
        self.assertEqual(len(posts), 2)

    def test_permalink_formatted_correctly(self):
        children = [self._make_child(permalink="/r/python/comments/xyz")]
        posts = cc._parse_children(children, cutoff=0, sub="python")
        self.assertTrue(posts[0]["permalink"].startswith("https://reddit.com"))

    def test_link_url_empty_for_self_posts(self):
        children = [self._make_child(is_self=True, url="https://example.com")]
        posts = cc._parse_children(children, cutoff=0, sub="python")
        self.assertEqual(posts[0]["link_url"], "")

    def test_link_url_set_for_link_posts(self):
        children = [self._make_child(is_self=False, url="https://example.com")]
        posts = cc._parse_children(children, cutoff=0, sub="python")
        self.assertEqual(posts[0]["link_url"], "https://example.com")


class TestGetRedditPosts(unittest.TestCase):

    def _reddit_json(self, titles):
        children = [
            {"data": {
                "title": t, "score": 50, "upvote_ratio": 0.9,
                "num_comments": 5, "author": "user", "created_utc": 9999999999,
                "is_self": True, "subreddit": "python", "selftext": "",
                "permalink": "/r/python/comments/x", "url": "", "link_flair_text": None,
            }}
            for t in titles
        ]
        return json.dumps({"kind": "Listing", "data": {"children": children}})

    def test_returns_posts_on_success(self):
        snap = self._reddit_json(["Post A", "Post B"])
        with patch("camofox_client._create_tab", return_value="t1"), \
             patch("camofox_client._get_snapshot", return_value=snap), \
             patch("camofox_client._delete_tab"), \
             patch("camofox_client.time.sleep"):
            posts = cc.get_reddit_posts("python", limit=10)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["title"], "Post A")

    def test_returns_empty_when_tab_fails(self):
        with patch("camofox_client._create_tab", return_value=None):
            posts = cc.get_reddit_posts("python")
        self.assertEqual(posts, [])

    def test_returns_empty_when_no_json_in_snapshot(self):
        with patch("camofox_client._create_tab", return_value="t1"), \
             patch("camofox_client._get_snapshot", return_value="page not found"), \
             patch("camofox_client._delete_tab"), \
             patch("camofox_client.time.sleep"):
            posts = cc.get_reddit_posts("python")
        self.assertEqual(posts, [])

    def test_deletes_tab_even_on_parse_failure(self):
        with patch("camofox_client._create_tab", return_value="t1"), \
             patch("camofox_client._get_snapshot", return_value="garbage"), \
             patch("camofox_client._delete_tab") as mock_del, \
             patch("camofox_client.time.sleep"):
            cc.get_reddit_posts("python")
        mock_del.assert_called_once()


if __name__ == "__main__":
    unittest.main()
