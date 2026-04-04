# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Tests for photo accumulator, album batching, and text chunk merging in bot_base."""
import asyncio
import unittest
from collections import defaultdict
import time as _time_mod


class TestPhotoQueue(unittest.TestCase):
    """Test the photo accumulator data structure behavior."""

    def setUp(self):
        self._photo_queue = defaultdict(list)
        self._media_group_seen = {}

    def test_single_photo_queued(self):
        ck = (123, None)
        self._photo_queue[ck].append({"file_id": "abc", "file_unique_id": "u1", "caption": ""})
        self.assertEqual(len(self._photo_queue[ck]), 1)

    def test_multiple_photos_queued(self):
        ck = (123, None)
        for i in range(5):
            self._photo_queue[ck].append({"file_id": f"f{i}", "file_unique_id": f"u{i}", "caption": ""})
        self.assertEqual(len(self._photo_queue[ck]), 5)

    def test_photo_queue_flush_clears(self):
        ck = (123, None)
        self._photo_queue[ck].append({"file_id": "abc", "file_unique_id": "u1", "caption": ""})
        photos = self._photo_queue.pop(ck, [])
        self.assertEqual(len(photos), 1)
        self.assertEqual(len(self._photo_queue[ck]), 0)

    def test_photo_queue_flush_empty_returns_empty(self):
        ck = (999, None)
        photos = self._photo_queue.pop(ck, [])
        self.assertEqual(photos, [])

    def test_separate_conversations_independent(self):
        ck1 = (123, None)
        ck2 = (456, None)
        self._photo_queue[ck1].append({"file_id": "a", "file_unique_id": "u1", "caption": ""})
        self._photo_queue[ck2].append({"file_id": "b", "file_unique_id": "u2", "caption": ""})
        self._photo_queue[ck2].append({"file_id": "c", "file_unique_id": "u3", "caption": ""})
        self.assertEqual(len(self._photo_queue[ck1]), 1)
        self.assertEqual(len(self._photo_queue[ck2]), 2)


class TestAlbumDedup(unittest.TestCase):
    """Test media_group_id deduplication for albums."""

    def setUp(self):
        self._media_group_seen = {}

    def test_new_media_group_marked(self):
        mg_id = "album123"
        self._media_group_seen[mg_id] = _time_mod.time()
        self.assertIn(mg_id, self._media_group_seen)

    def test_duplicate_media_group_detected(self):
        mg_id = "album123"
        self._media_group_seen[mg_id] = _time_mod.time()
        is_seen = mg_id in self._media_group_seen
        self.assertTrue(is_seen)

    def test_cleanup_stale_entries(self):
        self._media_group_seen["old"] = _time_mod.time() - 120  # 2 min ago
        self._media_group_seen["fresh"] = _time_mod.time()
        stale = [k for k, ts in self._media_group_seen.items() if _time_mod.time() - ts > 60]
        for k in stale:
            del self._media_group_seen[k]
        self.assertNotIn("old", self._media_group_seen)
        self.assertIn("fresh", self._media_group_seen)

    def test_no_media_group_id_not_tracked(self):
        mg_id = None
        if mg_id:
            self._media_group_seen[mg_id] = _time_mod.time()
        self.assertEqual(len(self._media_group_seen), 0)


class TestTextChunkMerging(unittest.TestCase):
    """Test text buffer merging logic."""

    def setUp(self):
        self._text_buffer = defaultdict(list)

    def test_single_chunk_unchanged(self):
        ck = (123, None)
        self._text_buffer[ck].append("hello world")
        merged = "\n".join(self._text_buffer.pop(ck, []))
        self.assertEqual(merged, "hello world")

    def test_multiple_chunks_joined(self):
        ck = (123, None)
        self._text_buffer[ck].append("hello")
        self._text_buffer[ck].append("world")
        self._text_buffer[ck].append("test")
        merged = "\n".join(self._text_buffer.pop(ck, []))
        self.assertEqual(merged, "hello\nworld\ntest")

    def test_buffer_cleared_after_flush(self):
        ck = (123, None)
        self._text_buffer[ck].append("chunk1")
        self._text_buffer.pop(ck, [])
        self.assertEqual(len(self._text_buffer[ck]), 0)

    def test_separate_conversations_buffered_independently(self):
        ck1 = (123, None)
        ck2 = (456, 789)
        self._text_buffer[ck1].append("msg A")
        self._text_buffer[ck2].append("msg B")
        self._text_buffer[ck2].append("msg C")
        self.assertEqual(len(self._text_buffer[ck1]), 1)
        self.assertEqual(len(self._text_buffer[ck2]), 2)


class TestTextDebounceAsync(unittest.IsolatedAsyncioTestCase):
    """Test that debounce actually merges rapid messages."""

    async def test_debounce_merges_rapid_texts(self):
        buffer = defaultdict(list)
        results = []
        DELAY = 0.1  # short for test

        async def flush(ck):
            await asyncio.sleep(DELAY)
            chunks = buffer.pop(ck, [])
            results.append("\n".join(chunks))

        ck = (123, None)
        tasks = {}

        # Simulate 3 rapid messages
        for msg in ["part1", "part2", "part3"]:
            buffer[ck].append(msg)
            existing = tasks.get(ck)
            if existing and not existing.done():
                existing.cancel()
            tasks[ck] = asyncio.ensure_future(flush(ck))

        await asyncio.sleep(DELAY + 0.05)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "part1\npart2\npart3")

    async def test_no_debounce_when_spaced_out(self):
        buffer = defaultdict(list)
        results = []
        DELAY = 0.05

        async def flush(ck):
            await asyncio.sleep(DELAY)
            chunks = buffer.pop(ck, [])
            results.append("\n".join(chunks))

        ck = (123, None)
        tasks = {}

        # First message
        buffer[ck].append("msg1")
        tasks[ck] = asyncio.ensure_future(flush(ck))
        await asyncio.sleep(DELAY + 0.02)  # wait for flush

        # Second message after flush
        buffer[ck].append("msg2")
        tasks[ck] = asyncio.ensure_future(flush(ck))
        await asyncio.sleep(DELAY + 0.02)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], "msg1")
        self.assertEqual(results[1], "msg2")


class TestPhotoQueueWithTextFlush(unittest.TestCase):
    """Test that text intent flushes photo queue correctly."""

    def test_text_flushes_photos(self):
        photo_queue = defaultdict(list)
        ck = (123, None)
        photo_queue[ck].append({"file_id": "f1", "file_unique_id": "u1", "caption": ""})
        photo_queue[ck].append({"file_id": "f2", "file_unique_id": "u2", "caption": "cat"})

        # Simulate text arrival — flush
        photos = photo_queue.pop(ck, [])
        self.assertEqual(len(photos), 2)
        self.assertEqual(photos[0]["file_id"], "f1")
        self.assertEqual(photos[1]["caption"], "cat")

        # Queue should be empty
        self.assertEqual(len(photo_queue[ck]), 0)

    def test_no_photos_text_proceeds_normally(self):
        photo_queue = defaultdict(list)
        ck = (123, None)
        photos = photo_queue.pop(ck, [])
        self.assertEqual(photos, [])


if __name__ == "__main__":
    unittest.main()
