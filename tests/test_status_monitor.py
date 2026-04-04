#!/usr/bin/env python3
"""Unit tests for status_monitor.py"""
import json
import unittest
from unittest.mock import MagicMock, mock_open, patch


class TestLoadState(unittest.TestCase):
    def test_returns_defaults_on_missing_file(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            import status_monitor
            result = status_monitor.load_state()
        self.assertEqual(result, {"last_claude_status": "operational", "known_models": []})

    def test_returns_defaults_on_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="not json")):
            import status_monitor
            result = status_monitor.load_state()
        self.assertEqual(result["last_claude_status"], "operational")

    def test_loads_valid_state(self):
        data = {"last_claude_status": "degraded", "known_models": ["m1"]}
        with patch("builtins.open", mock_open(read_data=json.dumps(data))):
            import status_monitor
            result = status_monitor.load_state()
        self.assertEqual(result["last_claude_status"], "degraded")
        self.assertIn("m1", result["known_models"])


class TestSaveState(unittest.TestCase):
    def test_writes_json(self):
        m = mock_open()
        with patch("builtins.open", m):
            import status_monitor
            status_monitor.save_state({"last_claude_status": "operational", "known_models": []})
        m.assert_called_once()
        written = "".join(call.args[0] for call in m().write.call_args_list)
        data = json.loads(written)
        self.assertEqual(data["last_claude_status"], "operational")


class TestCheckClaudeStatus(unittest.TestCase):
    def test_all_operational_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "components": [
                {"name": "API", "status": "operational"},
                {"name": "claude.ai", "status": "operational"},
            ]
        }
        with patch("status_monitor.requests.get", return_value=mock_resp):
            import status_monitor
            issues = status_monitor.check_claude_status()
        self.assertEqual(issues, [])

    def test_degraded_component_returns_issue(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "components": [
                {"name": "API", "status": "degraded_performance"},
            ]
        }
        with patch("status_monitor.requests.get", return_value=mock_resp):
            import status_monitor
            issues = status_monitor.check_claude_status()
        self.assertEqual(len(issues), 1)
        self.assertIn("API", issues[0])

    def test_network_error_returns_empty(self):
        with patch("status_monitor.requests.get", side_effect=Exception("timeout")):
            import status_monitor
            issues = status_monitor.check_claude_status()
        self.assertEqual(issues, [])


class TestCheckMinimaxModels(unittest.TestCase):
    def test_no_api_key_returns_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("status_monitor.os.environ.get", return_value=""):
                import status_monitor
                result = status_monitor.check_minimax_models()
        self.assertEqual(result, [])

    def test_returns_model_ids(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "MiniMax-M1"}, {"id": "MiniMax-M2"}]}
        with patch("status_monitor.os.environ.get", return_value="fake-key"):
            with patch("status_monitor.requests.get", return_value=mock_resp):
                import status_monitor
                result = status_monitor.check_minimax_models()
        self.assertIn("MiniMax-M1", result)
        self.assertIn("MiniMax-M2", result)

    def test_non_200_response_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("status_monitor.os.environ.get", return_value="fake-key"):
            with patch("status_monitor.requests.get", return_value=mock_resp):
                import status_monitor
                result = status_monitor.check_minimax_models()
        self.assertEqual(result, [])

    def test_network_error_returns_empty(self):
        with patch("status_monitor.os.environ.get", return_value="fake-key"):
            with patch("status_monitor.requests.get", side_effect=Exception("err")):
                import status_monitor
                result = status_monitor.check_minimax_models()
        self.assertEqual(result, [])


class TestCheckClaudeModels(unittest.TestCase):
    def test_returns_dict_with_expected_keys(self):
        import status_monitor
        result = status_monitor.check_claude_models()
        self.assertIn("opus", result)
        self.assertIn("sonnet", result)
        self.assertIn("haiku", result)


class TestReadButton(unittest.TestCase):
    def test_returns_inline_markup_with_read_button(self):
        import status_monitor
        markup = status_monitor.read_button()
        # InlineKeyboardMarkup has inline_keyboard attribute
        kb = markup.inline_keyboard
        self.assertEqual(len(kb), 1)
        self.assertEqual(kb[0][0].callback_data, "status_read")
        self.assertIn("已读", kb[0][0].text)


class TestCheckStatus(unittest.IsolatedAsyncioTestCase):
    async def test_sends_alert_on_new_degradation(self):
        import status_monitor
        state = {"last_claude_status": "operational", "known_models": []}
        with patch.object(status_monitor, "load_state", return_value=state), \
             patch.object(status_monitor, "save_state") as mock_save, \
             patch.object(status_monitor, "check_claude_status", return_value=["⚠️ <b>API</b>: degraded"]), \
             patch.object(status_monitor, "send_alert") as mock_alert:
            await status_monitor.check_status()
        mock_alert.assert_awaited_once()
        # State should be updated to degraded
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["last_claude_status"], "degraded")

    async def test_no_alert_when_already_degraded(self):
        import status_monitor
        state = {"last_claude_status": "degraded", "known_models": []}
        with patch.object(status_monitor, "load_state", return_value=state), \
             patch.object(status_monitor, "save_state"), \
             patch.object(status_monitor, "check_claude_status", return_value=["issue"]), \
             patch.object(status_monitor, "send_alert") as mock_alert:
            await status_monitor.check_status()
        mock_alert.assert_not_awaited()

    async def test_sends_recovery_when_previously_degraded(self):
        import status_monitor
        state = {"last_claude_status": "degraded", "known_models": []}
        with patch.object(status_monitor, "load_state", return_value=state), \
             patch.object(status_monitor, "save_state"), \
             patch.object(status_monitor, "check_claude_status", return_value=[]), \
             patch.object(status_monitor, "send_alert") as mock_alert:
            await status_monitor.check_status()
        mock_alert.assert_awaited_once()
        call_text = mock_alert.call_args[0][0]
        self.assertIn("Recovered", call_text)


class TestCheckModels(unittest.IsolatedAsyncioTestCase):
    async def test_sends_alert_for_new_models(self):
        import status_monitor
        state = {"last_claude_status": "operational", "known_models": ["old-model"]}
        with patch.object(status_monitor, "load_state", return_value=state), \
             patch.object(status_monitor, "save_state") as mock_save, \
             patch.object(status_monitor, "check_minimax_models", return_value=["old-model", "new-model"]), \
             patch.object(status_monitor, "send_alert") as mock_alert:
            await status_monitor.check_models()
        mock_alert.assert_awaited_once()
        saved = mock_save.call_args[0][0]
        self.assertIn("new-model", saved["known_models"])

    async def test_no_alert_when_no_new_models(self):
        import status_monitor
        state = {"last_claude_status": "operational", "known_models": ["model-1"]}
        with patch.object(status_monitor, "load_state", return_value=state), \
             patch.object(status_monitor, "save_state"), \
             patch.object(status_monitor, "check_minimax_models", return_value=["model-1"]), \
             patch.object(status_monitor, "send_alert") as mock_alert:
            await status_monitor.check_models()
        mock_alert.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
