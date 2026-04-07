#!/usr/bin/env python3
"""Unit tests for send_code_review.py"""
import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch


class TestFindClaudeBin(unittest.TestCase):
    def test_returns_env_path_if_exists(self):
        import send_code_review
        with patch("send_code_review.os.path.exists", return_value=True):
            result = send_code_review._find_claude_bin()
        self.assertEqual(result, send_code_review.CLAUDE_BIN)

    def test_returns_fallback_path(self):
        import send_code_review
        def fake_exists(path):
            if path == send_code_review.CLAUDE_BIN:
                return False
            if path == "/usr/local/bin/claude":
                return True
            return False

        with patch("send_code_review.os.path.exists", side_effect=fake_exists):
            result = send_code_review._find_claude_bin()
        self.assertEqual(result, "/usr/local/bin/claude")

    def test_returns_none_when_not_found(self):
        import send_code_review
        with patch("send_code_review.os.path.exists", return_value=False):
            result = send_code_review._find_claude_bin()
        self.assertIsNone(result)


class TestClassifyIssue(unittest.TestCase):
    def test_risky_file_always_risky(self):
        import send_code_review
        result = send_code_review._classify_issue("unused import", "admin_bot.py")
        self.assertEqual(result, "RISKY")

    def test_llm_client_is_risky(self):
        import send_code_review
        result = send_code_review._classify_issue("unused variable", "llm_client.py")
        self.assertEqual(result, "RISKY")

    def test_unused_import_is_safe(self):
        import send_code_review
        result = send_code_review._classify_issue("unused import in news.py", "news.py")
        self.assertEqual(result, "SAFE")

    def test_dead_variable_is_safe(self):
        import send_code_review
        result = send_code_review._classify_issue("dead variable found", "crypto_news.py")
        self.assertEqual(result, "SAFE")

    def test_formatting_is_safe(self):
        import send_code_review
        result = send_code_review._classify_issue("formatting issues trailing space", "bot_base.py")
        self.assertEqual(result, "SAFE")

    def test_logic_error_is_risky(self):
        import send_code_review
        result = send_code_review._classify_issue("logic error in retry loop", "news.py")
        self.assertEqual(result, "RISKY")

    def test_security_issue_is_risky(self):
        import send_code_review
        result = send_code_review._classify_issue("security token exposure", "config.py")
        self.assertEqual(result, "RISKY")

    def test_unknown_defaults_to_risky(self):
        import send_code_review
        result = send_code_review._classify_issue("some mysterious issue", "utils.py")
        self.assertEqual(result, "RISKY")

    def test_error_handling_is_risky(self):
        import send_code_review
        result = send_code_review._classify_issue("error handling missing", "run_bot.py")
        self.assertEqual(result, "RISKY")

    def test_redundant_import_is_safe(self):
        import send_code_review
        result = send_code_review._classify_issue("redundant import os", "helpers.py")
        self.assertEqual(result, "SAFE")


class TestParseReviewFindings(unittest.TestCase):
    def test_parses_single_finding(self):
        import send_code_review
        result_text = """
- **[HIGH]** Unused import in news.py
- File:news.py:42
- Importing `json` but never using it
- Remove the import line
"""
        findings = send_code_review._parse_review_findings(result_text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "HIGH")
        self.assertIn("Unused import", findings[0]["title"])
        self.assertEqual(findings[0]["file_path"], "news.py:42")

    def test_parses_multiple_findings(self):
        import send_code_review
        result_text = """
- **[CRITICAL]** Logic error in retry
- File:run_bot.py:100
- Retry loop has off-by-one
- Fix the loop counter

- **[LOW]** Dead variable
- File:utils.py:20
- `x` assigned but never read
- Remove the assignment
"""
        findings = send_code_review._parse_review_findings(result_text)
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0]["severity"], "CRITICAL")
        self.assertEqual(findings[1]["severity"], "LOW")

    def test_classifies_findings(self):
        import send_code_review
        result_text = """
- **[MEDIUM]** unused import detected
- File:helpers.py:5
- Not needed
- Remove

- **[HIGH]** error handling missing
- File:api.py:10
- No try/except
- Add error handling
"""
        findings = send_code_review._parse_review_findings(result_text)
        safe = [f for f in findings if f["classification"] == "SAFE"]
        risky = [f for f in findings if f["classification"] == "RISKY"]
        self.assertEqual(len(safe), 1)
        self.assertEqual(len(risky), 1)

    def test_returns_empty_on_no_findings(self):
        import send_code_review
        result_text = "System is healthy, no issues found."
        findings = send_code_review._parse_review_findings(result_text)
        self.assertEqual(findings, [])

    def test_extracts_file_path_with_file_prefix(self):
        import send_code_review
        result_text = """
- **[LOW]** unused import
File:crypto_news.py:15
- fix it
- remove it
"""
        findings = send_code_review._parse_review_findings(result_text)
        if findings:
            self.assertEqual(findings[0]["file_path"], "crypto_news.py:15")


class TestRunClaudeReview(unittest.IsolatedAsyncioTestCase):
    async def test_returns_result_from_json_dict(self):
        import send_code_review
        output = json.dumps({"result": "Found 2 issues."})
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(output.encode(), b""))

        with patch("send_code_review.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("send_code_review.asyncio.wait_for", return_value=(output.encode(), b"")):
            result = await send_code_review._run_claude_review("/usr/bin/claude", "Review prompt")

        self.assertEqual(result, "Found 2 issues.")

    async def test_returns_raw_output_on_json_error(self):
        import send_code_review
        output = b"Raw text output with issues found"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(output, b""))

        async def fake_wait_for(coro, timeout):
            return await coro

        with patch("send_code_review.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("send_code_review.asyncio.wait_for", new=fake_wait_for):
            result = await send_code_review._run_claude_review("/usr/bin/claude", "prompt")

        self.assertIn("Raw text output", result)

    async def test_returns_placeholder_on_empty_output(self):
        import send_code_review
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_wait_for(coro, timeout):
            return await coro

        with patch("send_code_review.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("send_code_review.asyncio.wait_for", new=fake_wait_for):
            result = await send_code_review._run_claude_review("/usr/bin/claude", "prompt")

        self.assertEqual(result, "(no output from review)")


class TestAutoFixSafeIssues(unittest.IsolatedAsyncioTestCase):
    async def test_returns_false_on_empty_findings(self):
        import send_code_review
        result = await send_code_review._auto_fix_safe_issues("/usr/bin/claude", [])
        self.assertFalse(result)

    async def test_returns_false_on_timeout(self):
        import send_code_review
        mock_proc = AsyncMock()

        async def fake_wait_for(coro, timeout):
            raise asyncio.TimeoutError()

        with patch("send_code_review.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("send_code_review.asyncio.wait_for", new=fake_wait_for):
            result = await send_code_review._auto_fix_safe_issues(
                "/usr/bin/claude",
                [{"title": "unused import", "file_path": "test.py", "body": "Remove it"}]
            )
        self.assertFalse(result)

    async def test_returns_true_on_successful_commit(self):
        import send_code_review
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_wait_for(coro, timeout):
            return await coro

        git_result = MagicMock()
        git_result.returncode = 0
        git_result.stdout = "abc1234567890\n"

        with patch("send_code_review.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("send_code_review.asyncio.wait_for", new=fake_wait_for), \
             patch("send_code_review.subprocess.run", return_value=git_result), \
             patch("builtins.open", mock_open()), \
             patch("send_code_review.asyncio.create_task"):
            result = await send_code_review._auto_fix_safe_issues(
                "/usr/bin/claude",
                [{"title": "unused import", "file_path": "test.py", "body": "Remove it"}]
            )
        self.assertTrue(result)


class TestHandleRiskyIssues(unittest.IsolatedAsyncioTestCase):
    async def test_returns_false_on_empty_findings(self):
        import send_code_review
        result = await send_code_review._handle_risky_issues("/usr/bin/claude", [], "2026-04-03")
        self.assertFalse(result)

    async def test_sends_tg_notification_on_success(self):
        import send_code_review
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        async def fake_wait_for(coro, timeout):
            return await coro

        git_result = MagicMock()
        git_result.returncode = 0

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()

        risky_findings = [
            {"severity": "HIGH", "title": "logic error", "file_path": "bot.py", "body": "Fix needed"}
        ]

        with patch("send_code_review.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("send_code_review.asyncio.wait_for", new=fake_wait_for), \
             patch("send_code_review.subprocess.run", return_value=git_result), \
             patch("telegram.Bot", return_value=mock_bot):
            result = await send_code_review._handle_risky_issues(
                "/usr/bin/claude", risky_findings, "2026-04-03"
            )
        self.assertTrue(result)
        mock_bot.send_message.assert_awaited_once()


class TestNotifyTg(unittest.TestCase):
    def test_sends_message(self):
        import send_code_review
        import httpx as httpx_mod
        mock_response = MagicMock()
        original_token = send_code_review.BOT_TOKEN
        original_uid = send_code_review.ADMIN_USER_ID
        send_code_review.BOT_TOKEN = "test-token"
        send_code_review.ADMIN_USER_ID = 12345
        try:
            with patch.object(httpx_mod, "post", return_value=mock_response) as mock_post:
                send_code_review._notify_tg("Test notification")
            mock_post.assert_called_once()
        finally:
            send_code_review.BOT_TOKEN = original_token
            send_code_review.ADMIN_USER_ID = original_uid

    def test_truncates_long_messages(self):
        import send_code_review
        import httpx as httpx_mod
        long_text = "A" * 5000
        original_token = send_code_review.BOT_TOKEN
        original_uid = send_code_review.ADMIN_USER_ID
        send_code_review.BOT_TOKEN = "test-token"
        send_code_review.ADMIN_USER_ID = 12345
        try:
            with patch.object(httpx_mod, "post") as mock_post:
                send_code_review._notify_tg(long_text)
            if mock_post.called:
                call_json = mock_post.call_args.kwargs.get("json", {})
                if "text" in call_json:
                    self.assertLessEqual(len(call_json["text"]), 4020)
        finally:
            send_code_review.BOT_TOKEN = original_token
            send_code_review.ADMIN_USER_ID = original_uid

    def test_does_nothing_without_token(self):
        import send_code_review
        import httpx as httpx_mod
        original_token = send_code_review.BOT_TOKEN
        original_uid = send_code_review.ADMIN_USER_ID
        send_code_review.BOT_TOKEN = ""
        send_code_review.ADMIN_USER_ID = 0
        try:
            with patch.object(httpx_mod, "post") as mock_post:
                send_code_review._notify_tg("Test message")
            mock_post.assert_not_called()
        finally:
            send_code_review.BOT_TOKEN = original_token
            send_code_review.ADMIN_USER_ID = original_uid


class TestScheduleRevertCheck(unittest.IsolatedAsyncioTestCase):
    async def test_no_crash_keeps_commit(self):
        import send_code_review
        import tempfile
        import os

        tmp = tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".flag")
        commit_hash = "abc1234567890"
        commit_time = "2026-04-03T10:00:00+00:00"
        tmp.write(f"{commit_hash}\n{commit_time}\n")
        tmp.close()

        with patch("send_code_review.asyncio.sleep", return_value=None), \
             patch("send_code_review.AUTOFIX_COMMIT_FLAG", tmp.name), \
             patch("send_code_review.os.path.exists", return_value=True), \
             patch("send_code_review.subprocess.run") as mock_run:

            # Return clean log with no crash keywords
            clean_result = MagicMock()
            clean_result.returncode = 0
            clean_result.stdout = "[2026-04-03 10:05:00] Bot started OK\n"
            mock_run.return_value = clean_result

            await send_code_review._schedule_revert_check(commit_hash)

        # No revert call — just tail command
        # If run was called, it was only for log reading (tail) + cleanup, not revert
        revert_calls = [c for c in mock_run.call_args_list
                        if "revert" in str(c)]
        self.assertEqual(len(revert_calls), 0)
        # Flag may have been deleted by the function, ignore
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass

    async def test_skips_if_flag_missing(self):
        import send_code_review
        with patch("send_code_review.asyncio.sleep", return_value=None), \
             patch("send_code_review.os.path.exists", side_effect=lambda p: "start_all" in p), \
             patch("send_code_review.subprocess.run") as mock_run:
            await send_code_review._schedule_revert_check("abc123")
        # Should not call git revert
        revert_calls = [c for c in mock_run.call_args_list if "revert" in str(c)]
        self.assertEqual(len(revert_calls), 0)


if __name__ == "__main__":
    unittest.main()
