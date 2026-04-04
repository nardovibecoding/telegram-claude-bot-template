#!/usr/bin/env python3
"""Unit tests for podcast_digest.py"""
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch
from xml.etree import ElementTree as ET


class TestLoadCache(unittest.TestCase):
    def test_returns_default_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            import podcast_digest
            result = podcast_digest._load_cache()
        self.assertEqual(result, {"seen": []})

    def test_loads_valid_cache(self):
        data = {"seen": ["url1", "url2"]}
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            import podcast_digest
            result = podcast_digest._load_cache()
        self.assertEqual(result["seen"], ["url1", "url2"])

    def test_returns_default_on_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="not json")):
            import podcast_digest
            result = podcast_digest._load_cache()
        self.assertEqual(result, {"seen": []})


class TestSaveCache(unittest.TestCase):
    def test_saves_json(self):
        m = mock_open()
        with patch("builtins.open", m):
            import podcast_digest
            podcast_digest._save_cache({"seen": ["url1"]})
        written = "".join(call.args[0] for call in m().write.call_args_list)
        data = json.loads(written)
        self.assertEqual(data["seen"], ["url1"])


class TestFetch(unittest.IsolatedAsyncioTestCase):
    async def test_returns_text_on_200(self):
        import podcast_digest
        import aiohttp
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="<rss>content</rss>")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=mock_session):
            result = await podcast_digest._fetch("http://example.com/rss")

        self.assertEqual(result, "<rss>content</rss>")

    async def test_returns_empty_on_non_200(self):
        import podcast_digest
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
            result = await podcast_digest._fetch("http://example.com/rss")

        self.assertEqual(result, "")

    async def test_returns_empty_on_exception(self):
        import podcast_digest
        import aiohttp
        with patch.object(aiohttp, "ClientSession", side_effect=Exception("network error")):
            result = await podcast_digest._fetch("http://example.com")
        self.assertEqual(result, "")


class TestFetchXiaoyuzhouEpisodes(unittest.IsolatedAsyncioTestCase):
    def _make_rss(self):
        return """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Episode 1</title>
    <link>https://podcast.com/ep1</link>
    <enclosure url="https://audio.com/ep1.mp3" type="audio/mpeg" length="12345"/>
    <pubDate>Mon, 01 Apr 2026 00:00:00 +0000</pubDate>
  </item>
</channel></rss>"""

    async def test_parses_episodes(self):
        import podcast_digest
        with patch.object(podcast_digest, "_fetch", return_value=self._make_rss()):
            episodes = await podcast_digest._fetch_xiaoyuzhou_episodes("abc123", "TestPodcast")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "Episode 1")
        self.assertEqual(episodes[0]["audio_url"], "https://audio.com/ep1.mp3")
        self.assertEqual(episodes[0]["podcast"], "TestPodcast")
        self.assertEqual(episodes[0]["source"], "小宇宙")

    async def test_returns_empty_on_no_rss(self):
        import podcast_digest
        with patch.object(podcast_digest, "_fetch", return_value=""):
            episodes = await podcast_digest._fetch_xiaoyuzhou_episodes("abc123", "TestPodcast")
        self.assertEqual(episodes, [])

    async def test_skips_items_without_audio(self):
        import podcast_digest
        rss = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>No Audio Episode</title>
    <link>https://podcast.com/ep1</link>
  </item>
</channel></rss>"""
        with patch.object(podcast_digest, "_fetch", return_value=rss):
            episodes = await podcast_digest._fetch_xiaoyuzhou_episodes("abc123", "Test")
        self.assertEqual(episodes, [])


class TestFetchEnPodcastEpisodes(unittest.IsolatedAsyncioTestCase):
    async def test_parses_en_episodes(self):
        import podcast_digest
        rss = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>AI Interview</title>
    <link>https://podcast.com/ep1</link>
    <enclosure url="https://audio.com/ep1.mp3" type="audio/mpeg" length="9999"/>
    <pubDate>Mon, 01 Apr 2026 00:00:00 +0000</pubDate>
  </item>
</channel></rss>"""
        with patch.object(podcast_digest, "_fetch", return_value=rss):
            episodes = await podcast_digest._fetch_en_podcast_episodes(
                "https://feeds.example.com/show", "Lex Fridman"
            )
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["source"], "Podcast")
        self.assertEqual(episodes[0]["podcast"], "Lex Fridman")


class TestDownloadAudio(unittest.IsolatedAsyncioTestCase):
    async def test_returns_none_on_non_200(self):
        import podcast_digest
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
            result = await podcast_digest._download_audio("http://example.com/audio.mp3")

        self.assertIsNone(result)

    async def test_returns_none_on_exception(self):
        import podcast_digest
        import aiohttp
        with patch.object(aiohttp, "ClientSession", side_effect=Exception("network fail")):
            result = await podcast_digest._download_audio("http://example.com/audio.mp3")
        self.assertIsNone(result)


class TestSummarize(unittest.IsolatedAsyncioTestCase):
    async def test_returns_summary_on_success(self):
        import podcast_digest
        with patch("podcast_digest.chat_completion_async", return_value="Great insights."):
            result = await podcast_digest._summarize("Test Title", "Some transcript text")
        self.assertEqual(result, "Great insights.")

    async def test_returns_empty_on_llm_failure(self):
        import podcast_digest
        with patch("podcast_digest.chat_completion_async", return_value="⚠️ All providers failed"):
            result = await podcast_digest._summarize("Title", "transcript")
        self.assertEqual(result, "")

    async def test_en_mode_uses_english_prompt(self):
        import podcast_digest
        with patch("podcast_digest.chat_completion_async", return_value="Summary here.") as mock_llm:
            await podcast_digest._summarize("Title", "transcript text", en_mode=True)
        call_args = mock_llm.call_args
        prompt_content = call_args.kwargs["messages"][0]["content"]
        self.assertIn("English", prompt_content)


class TestGetPodcastThread(unittest.IsolatedAsyncioTestCase):
    async def test_reads_cached_thread_id(self):
        import podcast_digest
        with patch("podcast_digest.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="999")):
            mock_bot = MagicMock()
            result = await podcast_digest._get_podcast_thread(mock_bot, en_mode=False)
        self.assertEqual(result, 999)

    async def test_creates_forum_topic_if_no_cache(self):
        import podcast_digest
        with patch("podcast_digest.os.path.exists", return_value=False), \
             patch("builtins.open", mock_open()) as m:
            mock_bot = MagicMock()
            mock_topic = MagicMock()
            mock_topic.message_thread_id = 555
            mock_bot.create_forum_topic = AsyncMock(return_value=mock_topic)
            result = await podcast_digest._get_podcast_thread(mock_bot, en_mode=True)
        self.assertEqual(result, 555)
        mock_bot.create_forum_topic.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
