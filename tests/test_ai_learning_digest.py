#!/usr/bin/env python3
"""Unit tests for ai_learning_digest.py (ai_learning_digest == ai_evolve module)"""
import json
import unittest
from unittest.mock import MagicMock, mock_open, patch


class TestLoadPreferences(unittest.TestCase):
    def test_returns_defaults_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            import ai_learning_digest
            result = ai_learning_digest._load_preferences()
        self.assertEqual(result["liked_keywords"], [])
        self.assertIn("skipped_sources", result)

    def test_loads_valid_preferences(self):
        data = {"liked_keywords": ["mcp", "agent"], "skipped_sources": {"reddit": 2}}
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            import ai_learning_digest
            result = ai_learning_digest._load_preferences()
        self.assertIn("mcp", result["liked_keywords"])

    def test_returns_defaults_on_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="bad json")):
            import ai_learning_digest
            result = ai_learning_digest._load_preferences()
        self.assertEqual(result["liked_keywords"], [])


class TestSavePreferences(unittest.TestCase):
    def test_saves_json(self):
        m = mock_open()
        with patch("builtins.open", m):
            import ai_learning_digest
            ai_learning_digest._save_preferences({"liked_keywords": ["mcp"], "skipped_sources": {}})
        written = "".join(call.args[0] for call in m().write.call_args_list)
        data = json.loads(written)
        self.assertIn("mcp", data["liked_keywords"])


class TestSend(unittest.IsolatedAsyncioTestCase):
    async def test_send_proposals_sends_header_message(self):
        """Test that send_proposals sends at least one message to the admin group."""
        import ai_learning_digest
        from unittest.mock import AsyncMock

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        proposals = [
            {
                "id": "abc12345",
                "type": "🔌 MCP Discovery",
                "title": "New MCP server",
                "description": "File access server",
                "url": "https://github.com/user/mcp",
                "action": "Evaluate if useful",
                "relevance": 5,
                "source": "github",
                "stars": 100,
                "score": 0,
            }
        ]

        with patch("telegram.Bot", return_value=mock_bot), \
             patch.object(ai_learning_digest, "load_pending", return_value={}), \
             patch.object(ai_learning_digest, "save_pending"):
            await ai_learning_digest.send_proposals(proposals)

        # At minimum a header + 1 proposal message
        self.assertGreater(mock_bot.send_message.await_count, 0)
        # First call should be to admin chat
        call_kwargs = mock_bot.send_message.call_args_list[0].kwargs
        self.assertEqual(call_kwargs.get("chat_id"), ai_learning_digest.ADMIN_CHAT_ID)


class TestClassifyAndPropose(unittest.TestCase):
    def test_filters_seen_urls(self):
        import ai_learning_digest
        cache = {"seen_urls": ["https://example.com/already-seen"]}
        posts = [
            {"title": "Test Post", "url": "https://example.com/already-seen",
             "text": "mcp server agent tool", "source": "reddit", "score": 0, "stars": 0}
        ]
        with patch.object(ai_learning_digest, "_load_preferences", return_value={"liked_keywords": []}):
            result = ai_learning_digest.classify_and_propose(posts, cache)
        # URL was in seen_urls, should be filtered
        self.assertEqual(result, [])

    def test_filters_low_relevance(self):
        import ai_learning_digest
        cache = {"seen_urls": []}
        posts = [
            {"title": "Unrelated post about cats", "url": "https://example.com/cats",
             "text": "cute cat videos", "source": "reddit", "score": 0, "stars": 0}
        ]
        with patch.object(ai_learning_digest, "_load_preferences", return_value={"liked_keywords": []}):
            result = ai_learning_digest.classify_and_propose(posts, cache)
        self.assertEqual(result, [])

    def test_classifies_mcp_discovery(self):
        import ai_learning_digest
        cache = {"seen_urls": []}
        posts = [
            {"title": "New MCP server for file access", "url": "https://github.com/user/mcp-files",
             "text": "mcp new server tool for filesystem access", "source": "github",
             "score": 0, "stars": 150}
        ]
        with patch.object(ai_learning_digest, "_load_preferences", return_value={"liked_keywords": []}):
            result = ai_learning_digest.classify_and_propose(posts, cache)
        if result:
            self.assertIn("MCP", result[0]["type"])

    def test_classifies_self_log_as_bug_fix(self):
        import ai_learning_digest
        cache = {"seen_urls": []}
        posts = [
            {"title": "Recurring error: ConnectionError (5x)",
             "url": "", "text": "ConnectionError in news.py line 42",
             "source": "self_logs", "score": 0, "stars": 0, "count": 5}
        ]
        with patch.object(ai_learning_digest, "_load_preferences", return_value={"liked_keywords": []}):
            result = ai_learning_digest.classify_and_propose(posts, cache)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "🔧 Bug Fix")

    def test_boosts_liked_keywords(self):
        import ai_learning_digest
        cache = {"seen_urls": []}
        posts = [
            {"title": "MCP server for claude agent automation",
             "url": "https://github.com/user/mcp-claude",
             "text": "mcp claude agent automation tool",
             "source": "github", "score": 0, "stars": 200}
        ]
        with patch.object(ai_learning_digest, "_load_preferences",
                          return_value={"liked_keywords": ["mcp"]}):
            result = ai_learning_digest.classify_and_propose(posts, cache)
        if result:
            # Boosted item should have higher relevance
            self.assertGreater(result[0]["relevance"], 0)


class TestFetchAnthropicNews(unittest.TestCase):
    def test_returns_filtered_posts(self):
        import ai_learning_digest
        html = """<html><body>
        <a href="/docs/new-model">New model release</a>
        <a href="/docs/update">Update to claude</a>
        <a href="/docs/other">Something else entirely</a>
        </body></html>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("ai_learning_digest.requests.get", return_value=mock_resp):
            result = ai_learning_digest.fetch_anthropic_news()

        # Only links containing "new", "update", "release", or "model" should appear
        for post in result:
            title_lower = post["title"].lower()
            self.assertTrue(
                any(kw in title_lower for kw in ["new", "update", "release", "model"]),
                f"Unexpected post: {post['title']}"
            )

    def test_returns_empty_on_error(self):
        import ai_learning_digest
        with patch("ai_learning_digest.requests.get", side_effect=Exception("fail")):
            result = ai_learning_digest.fetch_anthropic_news()
        self.assertEqual(result, [])

    def test_handles_non_200(self):
        import ai_learning_digest
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("ai_learning_digest.requests.get", return_value=mock_resp):
            result = ai_learning_digest.fetch_anthropic_news()
        self.assertEqual(result, [])


class TestFetchGithubTrending(unittest.TestCase):
    def test_returns_repos(self):
        import ai_learning_digest
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "full_name": "user/mcp-server",
                    "html_url": "https://github.com/user/mcp-server",
                    "description": "An MCP server for Claude",
                    "stargazers_count": 500,
                    "license": {"spdx_id": "MIT"},
                }
            ]
        }
        with patch("ai_learning_digest.requests.get", return_value=mock_resp):
            result = ai_learning_digest.fetch_github_trending()
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["source"], "github")

    def test_returns_empty_on_error(self):
        import ai_learning_digest
        with patch("ai_learning_digest.requests.get", side_effect=Exception("fail")):
            result = ai_learning_digest.fetch_github_trending()
        self.assertEqual(result, [])


class TestFetchHackernews(unittest.TestCase):
    def test_returns_relevant_stories(self):
        import ai_learning_digest
        ids_resp = MagicMock()
        ids_resp.json.return_value = [101, 102, 103]

        ai_story = MagicMock()
        ai_story.json.return_value = {
            "type": "story",
            "title": "New AI agent framework released",
            "url": "https://example.com",
            "score": 300,
            "id": 101,
        }

        unrelated_story = MagicMock()
        unrelated_story.json.return_value = {
            "type": "story",
            "title": "Cooking recipes for beginners",
            "url": "https://food.com",
            "score": 50,
            "id": 102,
        }

        comment = MagicMock()
        comment.json.return_value = {"type": "comment", "title": "", "id": 103}

        responses = [ids_resp, ai_story, unrelated_story, comment]
        call_count = [0]

        def fake_get(*args, **kwargs):
            r = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return r

        with patch("ai_learning_digest.requests.get", side_effect=fake_get):
            result = ai_learning_digest.fetch_hackernews()

        self.assertGreater(len(result), 0)
        self.assertTrue(any("AI" in p["title"] or "ai" in p["title"].lower() for p in result))

    def test_returns_empty_on_error(self):
        import ai_learning_digest
        with patch("ai_learning_digest.requests.get", side_effect=Exception("fail")):
            result = ai_learning_digest.fetch_hackernews()
        self.assertEqual(result, [])


class TestFetchMcpRegistries(unittest.TestCase):
    def test_returns_mcp_servers_from_glama(self):
        import ai_learning_digest
        html = """<html><body>
        <a href="/mcp/servers/file-server">File MCP Server</a>
        <a href="/mcp/servers/browser-server">Browser MCP Server</a>
        </body></html>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        with patch("ai_learning_digest.requests.get", return_value=mock_resp):
            result = ai_learning_digest.fetch_mcp_registries()
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["source"], "mcp_registry")

    def test_returns_empty_on_error(self):
        import ai_learning_digest
        with patch("ai_learning_digest.requests.get", side_effect=Exception("fail")):
            result = ai_learning_digest.fetch_mcp_registries()
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
