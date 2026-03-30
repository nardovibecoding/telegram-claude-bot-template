# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
import struct
import unittest
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory import (
    _blob_to_vec,
    _cosine_similarity,
    _doc_id,
    _extract_proper_nouns,
)


class TestBlobToVec(unittest.TestCase):
    def test_roundtrip(self):
        original = [0.1, 0.2, 0.3, 0.4]
        blob = struct.pack(f"{len(original)}f", *original)
        result = _blob_to_vec(blob)
        self.assertIsInstance(result, np.ndarray)
        np.testing.assert_allclose(result, original, rtol=1e-5)

    def test_single_float(self):
        blob = struct.pack("1f", 1.0)
        result = _blob_to_vec(blob)
        self.assertEqual(len(result), 1)

    def test_empty_blob(self):
        result = _blob_to_vec(b"")
        self.assertEqual(len(result), 0)


class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors(self):
        v = np.array([1.0, 0.0, 0.0])
        self.assertAlmostEqual(_cosine_similarity(v, v), 1.0)

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        self.assertAlmostEqual(_cosine_similarity(a, b), 0.0)

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        self.assertAlmostEqual(_cosine_similarity(a, b), -1.0)

    def test_zero_vector_a(self):
        a = np.array([0.0, 0.0])
        b = np.array([1.0, 0.0])
        self.assertEqual(_cosine_similarity(a, b), 0.0)

    def test_zero_vector_b(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 0.0])
        self.assertEqual(_cosine_similarity(a, b), 0.0)

    def test_both_zero(self):
        a = np.array([0.0, 0.0])
        self.assertEqual(_cosine_similarity(a, a), 0.0)


class TestDocId(unittest.TestCase):
    def test_returns_32_char_hex(self):
        result = _doc_id("123", "user", "hello", 1000)
        self.assertEqual(len(result), 32)
        int(result, 16)  # must be valid hex

    def test_deterministic(self):
        a = _doc_id("123", "user", "hello", 1000)
        b = _doc_id("123", "user", "hello", 1000)
        self.assertEqual(a, b)

    def test_different_inputs_differ(self):
        a = _doc_id("123", "user", "hello", 1000)
        b = _doc_id("123", "user", "hello", 1001)
        self.assertNotEqual(a, b)

    def test_different_chats_differ(self):
        a = _doc_id("111", "user", "hello", 1000)
        b = _doc_id("222", "user", "hello", 1000)
        self.assertNotEqual(a, b)


class TestExtractProperNouns(unittest.TestCase):
    def test_english_proper_noun(self):
        result = _extract_proper_nouns("Alice lives in London")
        self.assertIn("alice", result)
        self.assertIn("london", result)

    def test_filters_stopwords(self):
        result = _extract_proper_nouns("The quick brown fox")
        self.assertNotIn("the", result)

    def test_filters_tech_acronyms(self):
        result = _extract_proper_nouns("Using API and SSH on VPS")
        self.assertNotIn("api", result)
        self.assertNotIn("ssh", result)
        self.assertNotIn("vps", result)

    def test_chinese_characters(self):
        # All chars are CJK so they match as one sequence
        result = _extract_proper_nouns("我住在香港")
        self.assertIn("我住在香港", result)

    def test_chinese_isolated(self):
        # 3+ char CJK sequence required (len >= 3 filter)
        result = _extract_proper_nouns("I live in 上海市")
        self.assertIn("上海市", result)

    def test_at_handles(self):
        result = _extract_proper_nouns("Follow @nardovibecoding on X")
        self.assertIn("@nardovibecoding", result)

    def test_empty_string(self):
        result = _extract_proper_nouns("")
        self.assertEqual(result, set())

    def test_min_length_filter(self):
        # 2-char words should be excluded
        result = _extract_proper_nouns("Go is great")
        self.assertNotIn("go", result)


class TestEmbedBatchAndOne(unittest.TestCase):
    """Tests for _embed_batch and _embed_one via MemoryManager with a mock client."""

    def _make_manager(self, mock_client):
        import tempfile
        from pathlib import Path
        from memory import MemoryManager
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        return MemoryManager(client=mock_client, db_path=db_path)

    def test_embed_batch_success(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        item = MagicMock()
        item.index = 0
        item.embedding = [0.1] * 1536
        client.embeddings.create.return_value.data = [item]

        mgr = self._make_manager(client)
        result = mgr._embed_batch(["hello"])
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0])
        self.assertEqual(len(result[0]), 1536)  # type: ignore[arg-type]

    def test_embed_batch_failure_returns_nones(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.embeddings.create.side_effect = Exception("network error")

        mgr = self._make_manager(client)
        result = mgr._embed_batch(["a", "b"])
        self.assertEqual(result, [None, None])

    def test_embed_one_success(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        item = MagicMock()
        item.index = 0
        item.embedding = [0.5] * 1536
        client.embeddings.create.return_value.data = [item]

        mgr = self._make_manager(client)
        result = mgr._embed_one("test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1536)  # type: ignore[arg-type]

    def test_embed_one_failure_returns_none(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.embeddings.create.side_effect = Exception("fail")

        mgr = self._make_manager(client)
        result = mgr._embed_one("test")
        self.assertIsNone(result)


class TestExpandQuery(unittest.TestCase):
    def _make_manager(self, mock_client):
        import tempfile
        from pathlib import Path
        from memory import MemoryManager
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        return MemoryManager(client=mock_client, db_path=db_path)

    def test_returns_up_to_2_lines(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.chat.completions.create.return_value.choices[0].message.content = (
            "What is Alice's job?\nWhat does Alice do for work?"
        )
        mgr = self._make_manager(client)
        result = mgr._expand_query("Alice job")
        self.assertLessEqual(len(result), 2)
        self.assertEqual(len(result), 2)

    def test_failure_returns_empty(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("fail")
        mgr = self._make_manager(client)
        result = mgr._expand_query("anything")
        self.assertEqual(result, [])


class TestIsDuplicate(unittest.TestCase):
    def _make_manager(self, mock_client):
        import tempfile
        from pathlib import Path
        from memory import MemoryManager
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        return MemoryManager(client=mock_client, db_path=db_path)

    def test_no_existing_memories_not_duplicate(self):
        from unittest.mock import MagicMock
        mgr = self._make_manager(MagicMock())
        vec = np.array([1.0] + [0.0] * 1535, dtype=np.float32)
        self.assertFalse(mgr._is_duplicate(vec, "chat1"))

    def test_identical_vec_is_duplicate(self):
        from unittest.mock import MagicMock
        import struct
        mgr = self._make_manager(MagicMock())
        vec = np.array([1.0] + [0.0] * 1535, dtype=np.float32)
        blob = struct.pack(f"{len(vec)}f", *vec.tolist())
        mgr._conn.execute(
            "INSERT INTO messages (id, chat_id, role, ts, text, embedding, is_latest, memory_type) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 'dynamic')",
            ("id1", "chat1", "user", 1000, "text", blob),
        )
        mgr._conn.commit()
        self.assertTrue(mgr._is_duplicate(vec, "chat1"))

    def test_different_chat_not_duplicate(self):
        from unittest.mock import MagicMock
        import struct
        mgr = self._make_manager(MagicMock())
        vec = np.array([1.0] + [0.0] * 1535, dtype=np.float32)
        blob = struct.pack(f"{len(vec)}f", *vec.tolist())
        mgr._conn.execute(
            "INSERT INTO messages (id, chat_id, role, ts, text, embedding, is_latest, memory_type) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 'dynamic')",
            ("id1", "chat_other", "user", 1000, "text", blob),
        )
        mgr._conn.commit()
        self.assertFalse(mgr._is_duplicate(vec, "chat1"))


if __name__ == "__main__":
    unittest.main()
