#!/usr/bin/env python3
"""Unit tests for evolution_feed.py"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch


class TestMakeId(unittest.TestCase):
    def test_returns_12_char_hex(self):
        import evolution_feed
        result = evolution_feed._make_id("some title")
        self.assertEqual(len(result), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))

    def test_same_title_same_id(self):
        import evolution_feed
        self.assertEqual(evolution_feed._make_id("hello"), evolution_feed._make_id("hello"))

    def test_different_titles_different_ids(self):
        import evolution_feed
        self.assertNotEqual(evolution_feed._make_id("a"), evolution_feed._make_id("b"))


class TestAlreadyExists(unittest.TestCase):
    def test_returns_true_for_existing_id(self):
        import evolution_feed
        title = "GitHub: user/mcp-server"
        eid = evolution_feed._make_id(title)
        db = [{"id": eid, "url": "https://github.com/user/mcp-server"}]
        self.assertTrue(evolution_feed._already_exists(db, title))

    def test_returns_true_for_matching_url(self):
        import evolution_feed
        db = [{"id": "differentid1", "url": "https://github.com/user/repo"}]
        self.assertTrue(evolution_feed._already_exists(db, "SomeTitle", url="https://github.com/user/repo"))

    def test_returns_false_for_new_entry(self):
        import evolution_feed
        db = [{"id": "abc123456789", "url": "https://github.com/user/old"}]
        self.assertFalse(evolution_feed._already_exists(db, "New Title", url="https://github.com/new"))

    def test_empty_db_returns_false(self):
        import evolution_feed
        self.assertFalse(evolution_feed._already_exists([], "Title", url="https://example.com"))


class TestLoadDb(unittest.TestCase):
    def test_returns_empty_list_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            import evolution_feed
            result = evolution_feed._load_db()
        self.assertEqual(result, [])

    def test_loads_valid_db(self):
        data = [{"id": "abc", "title": "Test"}]
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            import evolution_feed
            result = evolution_feed._load_db()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "abc")

    def test_returns_empty_on_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="bad json")):
            import evolution_feed
            result = evolution_feed._load_db()
        self.assertEqual(result, [])


class TestKeywordFallbackCategory(unittest.TestCase):
    def test_matches_memory_keywords(self):
        import evolution_feed
        result = evolution_feed._keyword_fallback_category("persistent memory storage recall sqlite")
        self.assertEqual(result, "memory")

    def test_matches_automation_keywords(self):
        import evolution_feed
        result = evolution_feed._keyword_fallback_category("workflow automation pipeline cron trigger")
        self.assertEqual(result, "automation")

    def test_matches_voice_keywords(self):
        import evolution_feed
        result = evolution_feed._keyword_fallback_category("TTS voice speech whisper audio")
        self.assertEqual(result, "voice")

    def test_returns_empty_string_on_no_match(self):
        import evolution_feed
        result = evolution_feed._keyword_fallback_category("random unrelated xyz here foo bar")
        self.assertEqual(result, "")

    def test_matches_security_keywords(self):
        import evolution_feed
        result = evolution_feed._keyword_fallback_category("security pentest audit vulnerability CVE")
        self.assertEqual(result, "security")

    def test_matches_agent_keywords(self):
        import evolution_feed
        result = evolution_feed._keyword_fallback_category("multi-agent orchestration crewai autogen")
        self.assertEqual(result, "agent")


class TestFetchReadmeSnippet(unittest.TestCase):
    def test_returns_empty_for_non_github_url(self):
        import evolution_feed
        result = evolution_feed._fetch_readme_snippet("https://example.com/project")
        self.assertEqual(result, "")

    def test_returns_empty_for_short_github_url(self):
        import evolution_feed
        result = evolution_feed._fetch_readme_snippet("https://github.com/user")
        self.assertEqual(result, "")

    def test_fetches_readme_content(self):
        import evolution_feed
        import urllib.request
        mock_response = MagicMock()
        mock_response.read.return_value = b"# MCP Server\nThis is a README"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch.object(urllib.request, "urlopen", return_value=mock_response):
            result = evolution_feed._fetch_readme_snippet("https://github.com/user/mcp-server")

        self.assertIn("MCP Server", result)

    def test_returns_empty_on_network_error(self):
        import evolution_feed
        import urllib.request
        with patch.object(urllib.request, "urlopen", side_effect=Exception("404")):
            result = evolution_feed._fetch_readme_snippet("https://github.com/user/mcp-server")
        self.assertEqual(result, "")


class TestClassifyCategory(unittest.TestCase):
    def test_uses_keyword_fallback_when_no_api_key(self):
        import evolution_feed
        with patch.dict("os.environ", {"MINIMAX_API_KEY": ""}):
            with patch.object(evolution_feed, "_fetch_readme_snippet", return_value=""), \
                 patch.object(evolution_feed, "_keyword_fallback_category", return_value="memory") as mock_fb:
                result = evolution_feed._classify_category(
                    "memory-tool", "persistent memory storage", "https://github.com/u/r"
                )
        mock_fb.assert_called_once()
        self.assertEqual(result, "memory")

    def test_returns_valid_category_from_minimax(self):
        import evolution_feed
        from openai import OpenAI as RealOpenAI
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "automation"
        mock_client.chat.completions.create.return_value.choices = [mock_choice]

        with patch.dict("os.environ", {"MINIMAX_API_KEY": "fake-key"}):
            with patch.object(evolution_feed, "_fetch_readme_snippet", return_value=""), \
                 patch("openai.OpenAI", return_value=mock_client):
                result = evolution_feed._classify_category(
                    "workflow-bot", "automate pipeline triggers", "https://github.com/u/r"
                )
        self.assertEqual(result, "automation")

    def test_falls_back_to_keywords_on_invalid_minimax_response(self):
        import evolution_feed
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "invalid_category_xyz"
        mock_client.chat.completions.create.return_value.choices = [mock_choice]

        with patch.dict("os.environ", {"MINIMAX_API_KEY": "fake-key"}):
            with patch.object(evolution_feed, "_fetch_readme_snippet", return_value=""), \
                 patch("openai.OpenAI", return_value=mock_client), \
                 patch.object(evolution_feed, "_keyword_fallback_category", return_value="agent") as mock_fb:
                result = evolution_feed._classify_category(
                    "agent-tool", "multi-agent orchestration", "https://github.com/u/r"
                )
        mock_fb.assert_called_once()
        self.assertEqual(result, "agent")


class TestFetch(unittest.IsolatedAsyncioTestCase):
    async def test_returns_sanitized_text_on_200(self):
        import evolution_feed
        import aiohttp
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="hello world")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=mock_session), \
             patch("evolution_feed.sanitize_external_content", side_effect=lambda x: x):
            result = await evolution_feed._fetch("http://example.com")

        self.assertEqual(result, "hello world")

    async def test_returns_empty_on_non_200(self):
        import evolution_feed
        import aiohttp
        mock_resp = AsyncMock()
        mock_resp.status = 404

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=mock_session):
            result = await evolution_feed._fetch("http://example.com")

        self.assertEqual(result, "")

    async def test_returns_empty_on_exception(self):
        import evolution_feed
        import aiohttp
        with patch.object(aiohttp, "ClientSession", side_effect=Exception("fail")):
            result = await evolution_feed._fetch("http://example.com")
        self.assertEqual(result, "")


class TestContextFilter(unittest.TestCase):
    def test_passes_all_entries_on_llm_failure(self):
        import evolution_feed
        entries = [
            {"title": "Tool 1", "description": "desc 1"},
            {"title": "Tool 2", "description": "desc 2"},
        ]
        with patch("evolution_feed.chat_completion", side_effect=Exception("LLM down")), \
             patch("evolution_feed.sanitize_external_content", side_effect=lambda x: x):
            result = evolution_feed._context_filter(entries)
        self.assertEqual(result, entries)

    def test_filters_based_on_llm_verdicts(self):
        import evolution_feed
        entries = [
            {"title": "New MCP server", "description": "file access"},
            {"title": "Another scraper", "description": "web scraping"},
        ]
        verdicts = json.dumps([
            {"index": 0, "verdict": "keep", "reason": "new capability"},
            {"index": 1, "verdict": "skip", "reason": "we already have scrapers"},
        ])
        with patch("evolution_feed.chat_completion", return_value=verdicts), \
             patch("evolution_feed.sanitize_external_content", side_effect=lambda x: x):
            result = evolution_feed._context_filter(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "New MCP server")

    def test_returns_empty_input_unchanged(self):
        import evolution_feed
        result = evolution_feed._context_filter([])
        self.assertEqual(result, [])

    def test_passes_all_on_llm_warning_response(self):
        import evolution_feed
        entries = [{"title": "Tool", "description": "desc"}]
        with patch("evolution_feed.chat_completion", return_value="⚠️ All providers failed"), \
             patch("evolution_feed.sanitize_external_content", side_effect=lambda x: x):
            result = evolution_feed._context_filter(entries)
        self.assertEqual(result, entries)


if __name__ == "__main__":
    unittest.main()
