#!/usr/bin/env python3
"""Unit tests for auto_healer.py"""
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch


class TestLoadHistory(unittest.TestCase):
    def test_returns_empty_list_on_missing_file(self):
        import auto_healer
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = auto_healer.load_history()
        self.assertEqual(result, [])

    def test_returns_empty_list_on_invalid_json(self):
        import auto_healer
        with patch("builtins.open", mock_open(read_data="not-json")):
            result = auto_healer.load_history()
        self.assertEqual(result, [])

    def test_loads_valid_history(self):
        import auto_healer
        data = [{"type": "bot_down", "component": "daliu"}]
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            result = auto_healer.load_history()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "bot_down")


class TestSaveHistory(unittest.TestCase):
    def test_saves_json(self):
        import auto_healer
        h = [{"type": "bot_down", "component": "daliu"}]
        m = mock_open()
        with patch("builtins.open", m):
            auto_healer.save_history(h)
        m.assert_called_once()

    def test_truncates_to_last_100(self):
        import auto_healer
        h = [{"type": "x", "component": str(i)} for i in range(200)]
        written = []
        m = mock_open()
        with patch("builtins.open", m), \
             patch("json.dump", side_effect=lambda obj, f, **kw: written.append(obj)):
            auto_healer.save_history(h)
        self.assertEqual(len(written[0]), 100)
        self.assertEqual(written[0][0]["component"], "100")  # last 100 entries


class TestSaveAlerted(unittest.TestCase):
    def test_writes_json(self):
        import auto_healer
        data = {"bot_down:daliu": 1234567890.0}
        m = mock_open()
        with patch("builtins.open", m):
            auto_healer.save_alerted(data)
        m.assert_called_once()


class TestMarkAlerted(unittest.TestCase):
    def test_marks_issues(self):
        import auto_healer
        issues = [{"type": "bot_down", "component": "daliu"}]
        saved = {}

        def fake_save(d):
            saved.update(d)

        with patch.object(auto_healer, "load_alerted", return_value={}), \
             patch.object(auto_healer, "save_alerted", side_effect=fake_save), \
             patch("auto_healer.time") as mock_time:
            mock_time.time.return_value = 1000000.0
            auto_healer.mark_alerted(issues)

        self.assertIn("bot_down:daliu", saved)
        self.assertEqual(saved["bot_down:daliu"], 1000000.0)

    def test_cleans_entries_older_than_48h(self):
        import auto_healer
        old_ts = time.time() - 200000  # >48h ago
        existing = {"stale_cookies:twitter_cookies": old_ts}
        saved = {}

        with patch.object(auto_healer, "load_alerted", return_value=existing), \
             patch.object(auto_healer, "save_alerted", side_effect=lambda d: saved.update(d)), \
             patch("auto_healer.time") as mock_time:
            mock_time.time.return_value = time.time()
            auto_healer.mark_alerted([])

        self.assertNotIn("stale_cookies:twitter_cookies", saved)


class TestReadLog(unittest.TestCase):
    def test_returns_empty_string_on_missing_file(self):
        import auto_healer
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = auto_healer.read_log("/nonexistent/path.log")
        self.assertEqual(result, "")

    def test_returns_last_n_lines(self):
        import auto_healer
        lines = [f"line{i}\n" for i in range(20)]
        with patch("builtins.open", mock_open(read_data="".join(lines))):
            # mock_open doesn't support readlines() slicing well; patch directly
            with patch.object(auto_healer, "read_log", wraps=auto_healer.read_log):
                pass
        # Direct test: read_log returns joined last N lines
        with patch("builtins.open") as mock_f:
            mock_f.return_value.__enter__.return_value.readlines.return_value = lines
            result = auto_healer.read_log("/tmp/test.log", lines=5)
        self.assertEqual(result, "".join(lines[-5:]))

    def test_returns_full_content_when_lines_exceed_file(self):
        import auto_healer
        lines = ["line1\n", "line2\n"]
        with patch("builtins.open") as mock_f:
            mock_f.return_value.__enter__.return_value.readlines.return_value = lines
            result = auto_healer.read_log("/tmp/test.log", lines=500)
        self.assertEqual(result, "line1\nline2\n")


class TestIssueKey(unittest.TestCase):
    def test_key_format(self):
        import auto_healer
        issue = {"type": "bot_down", "component": "daliu"}
        self.assertEqual(auto_healer._issue_key(issue), "bot_down:daliu")

    def test_different_issues_have_different_keys(self):
        import auto_healer
        i1 = {"type": "bot_down", "component": "daliu"}
        i2 = {"type": "bot_down", "component": "sbf"}
        self.assertNotEqual(auto_healer._issue_key(i1), auto_healer._issue_key(i2))


class TestLoadAlerted(unittest.TestCase):
    def test_returns_empty_dict_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            import auto_healer
            result = auto_healer.load_alerted()
        self.assertEqual(result, {})

    def test_returns_empty_dict_on_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="bad")):
            import auto_healer
            result = auto_healer.load_alerted()
        self.assertEqual(result, {})

    def test_loads_valid_data(self):
        data = {"bot_down:daliu": 1234567890.0}
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            import auto_healer
            result = auto_healer.load_alerted()
        self.assertIn("bot_down:daliu", result)


class TestDedupIssues(unittest.TestCase):
    def test_filters_recently_alerted_issues(self):
        import auto_healer
        alerted = {"bot_down:daliu": time.time() - 3600}  # 1 hour ago
        with patch.object(auto_healer, "load_alerted", return_value=alerted):
            issues = [{"type": "bot_down", "component": "daliu"}]
            result = auto_healer.dedup_issues(issues)
        self.assertEqual(result, [])

    def test_includes_issues_past_24h(self):
        import auto_healer
        alerted = {"bot_down:daliu": time.time() - 90000}  # 25 hours ago
        with patch.object(auto_healer, "load_alerted", return_value=alerted):
            issues = [{"type": "bot_down", "component": "daliu"}]
            result = auto_healer.dedup_issues(issues)
        self.assertEqual(len(result), 1)

    def test_includes_never_alerted_issues(self):
        import auto_healer
        with patch.object(auto_healer, "load_alerted", return_value={}):
            issues = [{"type": "bot_down", "component": "sbf"}]
            result = auto_healer.dedup_issues(issues)
        self.assertEqual(len(result), 1)


class TestCheckProcess(unittest.IsolatedAsyncioTestCase):
    async def test_returns_true_when_process_found(self):
        import auto_healer
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"12345\n", b"")
        with patch("auto_healer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await auto_healer.check_process("run_bot.py daliu")
        self.assertTrue(result)

    async def test_returns_false_when_no_process(self):
        import auto_healer
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        with patch("auto_healer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await auto_healer.check_process("nonexistent_process")
        self.assertFalse(result)


class TestCheckPort(unittest.IsolatedAsyncioTestCase):
    async def test_returns_true_on_zero_exit(self):
        import auto_healer
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        with patch("auto_healer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await auto_healer.check_port(18789)
        self.assertTrue(result)

    async def test_returns_false_on_nonzero_exit(self):
        import auto_healer
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 1
        with patch("auto_healer.asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await auto_healer.check_port(18789)
        self.assertFalse(result)


class TestCheckFlags(unittest.IsolatedAsyncioTestCase):
    """Test the _check_flags nested function via detect_all_issues."""

    async def _run_detect_with_mocks(self, flag_exists=True, flag_content="2026-04-03",
                                      today="2026-04-03", hour=13):
        """Helper to run detect_all_issues with all external calls mocked."""
        import auto_healer
        from unittest.mock import patch, MagicMock, AsyncMock
        from pathlib import Path

        # All bots running, no issues except flags
        running_proc = AsyncMock()
        running_proc.communicate.return_value = (b"123\n", b"")

        df_proc = AsyncMock()
        df_proc.communicate.return_value = (b"Use%\n50%\n", b"")

        free_proc = AsyncMock()
        free_proc.communicate.return_value = (b"total info\nMem: 1000 500 500\n", b"")

        def fake_subprocess(*args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "pgrep":
                return running_proc
            elif cmd == "curl":
                port_proc = AsyncMock()
                port_proc.communicate.return_value = (b"ok", b"")
                port_proc.returncode = 0
                return port_proc
            elif cmd == "df":
                return df_proc
            elif cmd == "free":
                return free_proc
            return running_proc

        mock_flag = MagicMock(spec=Path)
        mock_flag.exists.return_value = flag_exists
        if flag_exists:
            mock_flag.read_text.return_value = flag_content

        mock_now = MagicMock()
        mock_now.strftime.return_value = today
        mock_now.hour = hour
        mock_now.minute = 35

        with patch("auto_healer.asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
             patch("auto_healer.read_log", return_value=""), \
             patch("auto_healer.datetime") as mock_dt, \
             patch("auto_healer.time") as mock_time, \
             patch.object(Path, "exists", return_value=False):  # cookie file
            mock_dt.now.return_value = mock_now
            mock_time.time.return_value = 1000000.0
            issues = await auto_healer.detect_all_issues()

        return issues

    async def test_missing_flag_file_triggers_issue(self):
        """When a flag file doesn't exist and hour > 12:30, an issue is raised."""
        import auto_healer
        from unittest.mock import patch, MagicMock, AsyncMock
        from pathlib import Path

        running_proc = AsyncMock()
        running_proc.communicate.return_value = (b"123\n", b"")

        df_proc = AsyncMock()
        df_proc.communicate.return_value = (b"Use%\n50%\n", b"")

        free_proc = AsyncMock()
        free_proc.communicate.return_value = (b"total info\nMem: 1000 500 500\n", b"")

        def fake_subprocess(*args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "pgrep":
                return running_proc
            elif cmd == "curl":
                port_proc = AsyncMock()
                port_proc.communicate.return_value = (b"ok", b"")
                port_proc.returncode = 0
                return port_proc
            elif cmd == "df":
                return df_proc
            elif cmd == "free":
                return free_proc
            return running_proc

        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-04-03"
        mock_now.hour = 13
        mock_now.minute = 35

        # Patch Path.exists to return False for all flag files
        original_exists = Path.exists

        def path_exists(self):
            # Return False for flag files, let cookie path return False too
            return False

        with patch("auto_healer.asyncio.create_subprocess_exec", side_effect=fake_subprocess), \
             patch("auto_healer.read_log", return_value=""), \
             patch("auto_healer.datetime") as mock_dt, \
             patch("auto_healer.time") as mock_time, \
             patch.object(Path, "exists", path_exists):
            mock_dt.now.return_value = mock_now
            mock_time.time.return_value = 1000000.0
            issues = await auto_healer.detect_all_issues()

        digest_issues = [i for i in issues if i["type"] == "digest_not_sent"]
        self.assertGreater(len(digest_issues), 0)


class TestAutoFix(unittest.IsolatedAsyncioTestCase):
    async def test_no_issues_does_nothing(self):
        import auto_healer
        # Should not raise even with empty issues
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        with patch("auto_healer.dedup_issues", return_value=[]), \
             patch("auto_healer.load_history", return_value=[]), \
             patch("auto_healer.save_history"), \
             patch("telegram.Bot", return_value=mock_bot):
            # If no fixable and no alert_only, nothing is sent
            await auto_healer.auto_fix([])

    async def test_alert_only_issues_send_message(self):
        import auto_healer
        issues = [{"type": "stale_cookies", "severity": "MEDIUM",
                   "component": "twitter_cookies", "details": "Cookies 40h old"}]

        # stale_cookies not in fixable set, so it goes to alert_only.
        # dedup_issues is called twice: once for alert_only (should return issues),
        # once for fixable (should return [] to avoid subprocess launch).
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        dedup_calls = [issues, []]  # alert_only first, fixable second
        with patch("auto_healer.dedup_issues", side_effect=lambda lst: dedup_calls.pop(0) if dedup_calls else []), \
             patch("auto_healer.load_history", return_value=[]), \
             patch("auto_healer.save_history"), \
             patch("auto_healer.mark_alerted"), \
             patch("telegram.Bot", return_value=mock_bot):
            await auto_healer.auto_fix(issues)

        mock_bot.send_message.assert_awaited()


if __name__ == "__main__":
    unittest.main()
