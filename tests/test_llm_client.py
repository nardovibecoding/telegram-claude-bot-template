# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_client import _FALLBACK_CHAIN, _is_fatal, PROVIDERS


class TestFallbackChain(unittest.TestCase):
    def test_kimi_is_primary(self):
        self.assertEqual(_FALLBACK_CHAIN[0], "kimi")

    def test_minimax_is_second(self):
        self.assertEqual(_FALLBACK_CHAIN[1], "minimax")

    def test_all_providers_exist(self):
        for key in _FALLBACK_CHAIN:
            self.assertIn(key, PROVIDERS, f"Provider '{key}' in chain but not in PROVIDERS")


class TestIsFatal(unittest.TestCase):
    def test_kimi_401_is_fatal(self):
        self.assertTrue(_is_fatal("401 - Invalid Authentication"))

    def test_invalid_api_key_is_fatal(self):
        self.assertTrue(_is_fatal("Error: invalid_api_key provided"))

    def test_rate_limit_is_not_fatal(self):
        self.assertFalse(_is_fatal("429 Too Many Requests"))

    def test_timeout_is_not_fatal(self):
        self.assertFalse(_is_fatal("Request timed out after 45s"))


if __name__ == "__main__":
    unittest.main()
