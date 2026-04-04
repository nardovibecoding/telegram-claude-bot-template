#!/usr/bin/env python3
"""Unit tests for youtube_digest.py"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch


class TestLoadCache(unittest.TestCase):
    def test_returns_default_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            import youtube_digest
            result = youtube_digest._load_cache()
        self.assertEqual(result, {"seen": []})

    def test_loads_valid_cache(self):
        data = {"seen": ["vid1", "vid2"]}
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            import youtube_digest
            result = youtube_digest._load_cache()
        self.assertEqual(result["seen"], ["vid1", "vid2"])


class TestSaveCache(unittest.TestCase):
    def test_saves_json(self):
        m = mock_open()
        with patch("builtins.open", m):
            import youtube_digest
            youtube_digest._save_cache({"seen": ["vid1"]})
        written = "".join(call.args[0] for call in m().write.call_args_list)
        data = json.loads(written)
        self.assertEqual(data["seen"], ["vid1"])


class TestFetch(unittest.IsolatedAsyncioTestCase):
    async def test_returns_text_on_200(self):
        import youtube_digest
        import aiohttp
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="feed content")

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=mock_session):
            result = await youtube_digest._fetch("http://example.com")
        self.assertEqual(result, "feed content")

    async def test_returns_empty_on_error(self):
        import youtube_digest
        import aiohttp
        with patch.object(aiohttp, "ClientSession", side_effect=Exception("fail")):
            result = await youtube_digest._fetch("http://example.com")
        self.assertEqual(result, "")


class TestFetchChannelVideos(unittest.IsolatedAsyncioTestCase):
    def _make_feed(self):
        return """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <yt:videoId>abc123xyz</yt:videoId>
    <title>Test Video</title>
    <published>2026-04-01T12:00:00+00:00</published>
  </entry>
</feed>"""

    async def test_parses_channel_feed(self):
        import youtube_digest
        with patch.object(youtube_digest, "_fetch", return_value=self._make_feed()):
            videos = await youtube_digest._fetch_channel_videos("UCTEST123", "TestChannel")
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["video_id"], "abc123xyz")
        self.assertEqual(videos[0]["title"], "Test Video")
        self.assertEqual(videos[0]["channel"], "TestChannel")
        self.assertIn("watch?v=abc123xyz", videos[0]["url"])

    async def test_returns_empty_on_no_feed(self):
        import youtube_digest
        with patch.object(youtube_digest, "_fetch", return_value=""):
            videos = await youtube_digest._fetch_channel_videos("UCTEST", "Channel")
        self.assertEqual(videos, [])

    async def test_returns_empty_on_parse_error(self):
        import youtube_digest
        with patch.object(youtube_digest, "_fetch", return_value="not xml"):
            videos = await youtube_digest._fetch_channel_videos("UCTEST", "Channel")
        self.assertEqual(videos, [])


class TestSearchYoutube(unittest.IsolatedAsyncioTestCase):
    async def test_returns_videos_from_invidious(self):
        import youtube_digest
        data = [
            {"type": "video", "videoId": "vid1", "title": "AI Interview", "author": "TestChan"},
            {"type": "video", "videoId": "vid2", "title": "Sam Altman Talk", "author": "AIChan"},
        ]
        with patch.object(youtube_digest, "_fetch", return_value=json.dumps(data)):
            results = await youtube_digest._search_youtube("AI agent interview")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["video_id"], "vid1")

    async def test_skips_non_video_type(self):
        import youtube_digest
        data = [
            {"type": "channel", "videoId": "c1", "title": "A Channel", "author": "Foo"},
            {"type": "video", "videoId": "vid1", "title": "Video", "author": "Bar"},
        ]
        with patch.object(youtube_digest, "_fetch", return_value=json.dumps(data)):
            results = await youtube_digest._search_youtube("test")
        self.assertEqual(len(results), 1)

    async def test_tries_all_instances_on_failure(self):
        import youtube_digest
        # First two fail, third returns valid
        valid_data = [{"type": "video", "videoId": "x1", "title": "Title", "author": "Chan"}]
        call_count = [0]

        async def fake_fetch(url, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                return ""
            return json.dumps(valid_data)

        with patch.object(youtube_digest, "_fetch", side_effect=fake_fetch):
            results = await youtube_digest._search_youtube("AI")
        self.assertEqual(len(results), 1)

    async def test_returns_empty_on_all_fail(self):
        import youtube_digest
        with patch.object(youtube_digest, "_fetch", return_value=""):
            results = await youtube_digest._search_youtube("AI")
        self.assertEqual(results, [])


class TestGetTranscript(unittest.IsolatedAsyncioTestCase):
    async def test_returns_transcript_via_executor(self):
        """Test that _get_transcript calls executor and returns its result."""
        import youtube_digest
        import asyncio

        async def fake_wait_for(coro, timeout):
            return "Hello world transcript text here and more text"

        with patch("youtube_digest.asyncio.wait_for", side_effect=fake_wait_for):
            result = await youtube_digest._get_transcript("abc123")

        self.assertIn("Hello world", result)

    async def test_returns_empty_on_timeout(self):
        import youtube_digest
        import asyncio

        with patch("youtube_digest.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await youtube_digest._get_transcript("abc123")
        self.assertEqual(result, "")


class TestGetYoutubeThread(unittest.IsolatedAsyncioTestCase):
    async def test_reads_cached_thread_id(self):
        import youtube_digest
        with patch("youtube_digest.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data="777")):
            mock_bot = MagicMock()
            result = await youtube_digest._get_youtube_thread(mock_bot)
        self.assertEqual(result, 777)

    async def test_creates_forum_topic_if_no_cache(self):
        import youtube_digest
        with patch("youtube_digest.os.path.exists", return_value=False), \
             patch("builtins.open", mock_open()):
            mock_bot = MagicMock()
            mock_topic = MagicMock()
            mock_topic.message_thread_id = 888
            mock_bot.create_forum_topic = AsyncMock(return_value=mock_topic)
            result = await youtube_digest._get_youtube_thread(mock_bot)
        self.assertEqual(result, 888)


class TestScoreVideos(unittest.IsolatedAsyncioTestCase):
    async def test_scores_celebrity_video_highly(self):
        import youtube_digest
        videos = [
            {
                "video_id": "v1",
                "title": "Elon Musk on AI Future",
                "url": "https://youtube.com/watch?v=v1",
                "channel": "Lex Fridman",
                "pub_date": "2026-04-01",
            }
        ]

        stats = {
            "viewCount": 1000000,
            "likeCount": 50000,
            "lengthSeconds": 3600,
            "description": "Great interview",
            "subCountText": "3M subscribers",
        }

        with patch.object(youtube_digest, "_fetch", return_value=json.dumps(stats)):
            scored = await youtube_digest._score_videos(videos)

        self.assertEqual(len(scored), 1)
        self.assertGreater(scored[0]["_score"], 0)
        # Celebrity name should boost score
        self.assertIn("_er", scored[0])

    async def test_filters_short_videos(self):
        import youtube_digest
        videos = [
            {
                "video_id": "short1",
                "title": "Quick tip",
                "url": "https://youtube.com/watch?v=short1",
                "channel": "Channel",
                "pub_date": "2026-04-01",
            }
        ]

        stats = {
            "viewCount": 5000,
            "likeCount": 100,
            "lengthSeconds": 60,  # 1 min -- filtered out (<600s)
            "description": "",
            "subCountText": "10K subscribers",
        }

        with patch.object(youtube_digest, "_fetch", return_value=json.dumps(stats)):
            scored = await youtube_digest._score_videos(videos)

        self.assertEqual(len(scored), 0)

    async def test_skip_duration_filter_keeps_short_video(self):
        import youtube_digest
        videos = [
            {
                "video_id": "short2",
                "title": "AI tip",
                "url": "https://youtube.com/watch?v=short2",
                "channel": "Chan",
                "pub_date": "2026-04-01",
            }
        ]

        stats = {
            "viewCount": 5000,
            "likeCount": 100,
            "lengthSeconds": 60,
            "description": "ai agent automation",
            "subCountText": "10K subscribers",
        }

        with patch.object(youtube_digest, "_fetch", return_value=json.dumps(stats)):
            scored = await youtube_digest._score_videos(videos, skip_duration_filter=True)

        self.assertEqual(len(scored), 1)


if __name__ == "__main__":
    unittest.main()
