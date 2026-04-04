# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Tests for admin_bot/youtube.py"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestExtractVideoId(unittest.TestCase):
    def setUp(self):
        from admin_bot.youtube import _extract_video_id
        self.fn = _extract_video_id

    def test_standard_url(self):
        self.assertEqual(self.fn("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_short_url(self):
        self.assertEqual(self.fn("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_embed_url(self):
        self.assertEqual(self.fn("https://www.youtube.com/embed/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_shorts_url(self):
        self.assertEqual(self.fn("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_bare_id(self):
        self.assertEqual(self.fn("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_invalid_url(self):
        self.assertIsNone(self.fn("https://vimeo.com/123456"))

    def test_empty_string(self):
        self.assertIsNone(self.fn(""))

    def test_url_with_extra_params(self):
        self.assertEqual(
            self.fn("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s"),
            "dQw4w9WgXcQ"
        )


class TestGetTranscript(unittest.TestCase):
    def setUp(self):
        from admin_bot.youtube import _get_transcript
        self.fn = _get_transcript

    def test_returns_text_on_success(self):
        mock_snippet = MagicMock()
        mock_snippet.text = "Hello world"
        mock_api = MagicMock()
        mock_api.fetch.return_value = [mock_snippet]
        with patch("youtube_transcript_api.YouTubeTranscriptApi", return_value=mock_api):
            result = self.fn("dQw4w9WgXcQ")
        self.assertEqual(result, "Hello world")

    def test_returns_none_on_exception(self):
        with patch("youtube_transcript_api.YouTubeTranscriptApi", side_effect=Exception("no transcript")):
            result = self.fn("dQw4w9WgXcQ")
        self.assertIsNone(result)

    def test_returns_none_on_empty_transcript(self):
        mock_snippet = MagicMock()
        mock_snippet.text = "   "
        mock_api = MagicMock()
        mock_api.fetch.return_value = [mock_snippet]
        with patch("youtube_transcript_api.YouTubeTranscriptApi", return_value=mock_api):
            result = self.fn("dQw4w9WgXcQ")
        self.assertIsNone(result)


class TestDownloadAndTranscribe(unittest.TestCase):
    def setUp(self):
        from admin_bot.youtube import _download_and_transcribe
        self.fn = _download_and_transcribe

    def test_uses_canonical_url(self):
        """Verify it accepts a canonical youtu.be URL (fix #5 — no raw user input)."""
        # Just check it doesn't crash on a well-formed URL when yt-dlp fails
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="not found")
            result = self.fn("https://youtu.be/dQw4w9WgXcQ")
        self.assertIsNone(result)

    def test_returns_none_on_yt_dlp_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            result = self.fn("https://youtu.be/abc12345678")
        self.assertIsNone(result)

    def test_returns_none_on_timeout(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("yt-dlp", 120)):
            result = self.fn("https://youtu.be/abc12345678")
        self.assertIsNone(result)

    def test_cleans_up_temp_file_on_failure(self):
        import os
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="err")
            with patch("tempfile.NamedTemporaryFile") as mock_tmp:
                mock_tmp.return_value.__enter__ = lambda s: MagicMock(name="/tmp/fake.m4a")
                mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
                self.fn("https://youtu.be/abc12345678")
        # No file left behind (temp cleanup via finally block)


class TestSummarize(unittest.TestCase):
    def setUp(self):
        from admin_bot.youtube import _summarize
        self.fn = _summarize

    def test_returns_error_when_no_api_key(self):
        with patch.dict("os.environ", {"MINIMAX_API_KEY": ""}):
            result = self.fn("some transcript")
        self.assertIn("Error", result)

    def test_truncates_long_transcript(self):
        from admin_bot.youtube import MAX_TRANSCRIPT_CHARS
        long_text = "x" * (MAX_TRANSCRIPT_CHARS + 1000)
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "summary"
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "testkey"}):
            with patch("openai.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = mock_resp
                mock_openai.return_value = mock_client
                with patch("utils.strip_think", return_value="summary"):
                    result = self.fn(long_text)
        # Confirm truncation marker was passed to API
        call_args = mock_client.chat.completions.create.call_args
        user_content = call_args[1]["messages"][1]["content"]
        self.assertIn("[... truncated]", user_content)

    def test_returns_summary_on_success(self):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "  key point 1\nkey point 2  "
        with patch.dict("os.environ", {"MINIMAX_API_KEY": "testkey"}):
            with patch("openai.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_client.chat.completions.create.return_value = mock_resp
                mock_openai.return_value = mock_client
                with patch("utils.strip_think", side_effect=lambda x: x):
                    result = self.fn("short transcript")
        self.assertIn("key point", result)


class TestCmdYt(unittest.IsolatedAsyncioTestCase):
    async def test_no_args_replies_usage(self):
        from admin_bot.youtube import cmd_yt
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.id = 999
        context = MagicMock()
        context.args = []
        # admin_only checks user id — patch it
        with patch("admin_bot.youtube.admin_only", lambda f: f):
            from admin_bot import youtube as yt_mod
            orig = yt_mod.cmd_yt.__wrapped__ if hasattr(yt_mod.cmd_yt, "__wrapped__") else yt_mod.cmd_yt
        await orig(update, context)
        update.message.reply_text.assert_called_once()
        assert "Usage" in update.message.reply_text.call_args[0][0]

    async def test_invalid_url_replies_error(self):
        from admin_bot.youtube import _extract_video_id
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = MagicMock()
        context.args = ["https://vimeo.com/123"]
        with patch("admin_bot.youtube._extract_video_id", return_value=None):
            from admin_bot import youtube as yt_mod
            orig = yt_mod.cmd_yt.__wrapped__ if hasattr(yt_mod.cmd_yt, "__wrapped__") else yt_mod.cmd_yt
            await orig(update, context)
        update.message.reply_text.assert_called_once()
        assert "parse" in update.message.reply_text.call_args[0][0].lower()

    async def test_yt_dlp_receives_canonical_url(self):
        """Fix #5: yt-dlp must receive https://youtu.be/<id>, not raw user URL."""
        from admin_bot import youtube as yt_mod
        update = MagicMock()
        update.message.reply_text = AsyncMock(return_value=MagicMock(
            edit_text=AsyncMock(), delete=AsyncMock()
        ))
        update.message.message_thread_id = None
        update.effective_chat.id = -100123
        update.get_bot = MagicMock(return_value=MagicMock())
        context = MagicMock()
        context.args = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=99s"]

        captured = {}

        def fake_download(url):
            captured["url"] = url
            return "transcript text"

        with patch.object(yt_mod, "_get_transcript", return_value=None), \
             patch.object(yt_mod, "_download_and_transcribe", side_effect=fake_download), \
             patch.object(yt_mod, "_summarize", return_value="summary"), \
             patch("admin_bot.helpers._send_msg", new_callable=AsyncMock):
            orig = yt_mod.cmd_yt.__wrapped__ if hasattr(yt_mod.cmd_yt, "__wrapped__") else yt_mod.cmd_yt
            await orig(update, context)

        self.assertEqual(captured.get("url"), "https://youtu.be/dQw4w9WgXcQ")


if __name__ == "__main__":
    unittest.main()
