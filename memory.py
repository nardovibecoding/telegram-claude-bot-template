# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Persistent vector memory for 大劉 bot.

Storage:   SQLite (built-in) — staging table + main messages table
Screening: one LLM call per session, triggered by /clear
Embedding: MiniMax embo-01, batched over worthy messages only
Search:    numpy cosine similarity (brute-force; fast for personal scale)

Enhancements:
  - Contradiction detection: is_latest flag supersedes stale memories
  - Static/dynamic split: recency decay for dynamic memories
  - Query expansion: multi-phrasing vector search via MiniMax
  - Task-aware retrieval: context-based score boosting
"""
import hashlib
import logging
import math
import re
import sqlite3
import struct
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)

RECENT_WINDOW = 10    # rolling-window size kept in bot.py's in-memory list
_TOP_K        = 5     # semantic results injected into system prompt
_EMBED_MODEL  = "embo-01"       # MiniMax embedding — no free alternative yet
_EMBED_DIM    = 1536
_CHAT_MODEL   = None            # resolved at runtime from llm_client
_DB_PATH      = Path(__file__).parent / "memory.db"

_SCREEN_PROMPT = """\
你係一個記憶篩選員。以下係一批對話訊息，格式係：
[索引] 角色：內容

請判斷哪些訊息值得長期記住。值得記住嘅例子：
- 用戶嘅個人資料（名字、年齡、職業、家庭）
- 明確嘅偏好或習慣（鍾意/唔鍾意某樣嘢）
- 重要決定或計劃（投資、買樓、搬屋）
- 財務或事業相關資訊
- 反覆出現嘅話題或煩惱

唔值得記住嘅例子：
- 打招呼、「你好」「再見」
- 單字或極短回覆（「好」「係」「明白」「哈哈」）
- 純粹嘅閒聊，冇實質內容

對每個值得記住嘅訊息，請同時判斷佢係「static」（永久事實，例如名字、身份、角色）定「dynamic」（暫時狀態，例如目前項目、近期計劃）。

返回格式：索引:類型，以逗號分隔，例如：0:static,3:dynamic,5:static
如果全部都唔值得記住，返回空字串。唔好解釋，只返回結果。"""

# Regex for extracting proper nouns (capitalized words, CJK sequences, @handles)
_PROPER_NOUN_RE = re.compile(
    r"[A-Z][a-z]{2,}"            # English proper nouns (3+ chars)
    r"|[\u4e00-\u9fff]{2,}"      # Chinese character sequences
    r"|@\w+"                     # @handles
)

# Common English words that start with uppercase (false positives for proper nouns)
_STOPWORDS = {
    "the", "this", "that", "what", "how", "why", "when", "where", "who",
    "but", "and", "for", "not", "you", "she", "they", "his", "her",
    "its", "our", "your", "can", "did", "was", "has", "had", "are",
    "does", "been", "than", "same", "other", "into", "over", "after",
    "about", "from", "with", "also", "just", "then", "here", "there",
    "some", "more", "such", "each", "both", "few", "only", "even",
    "well", "now", "much", "many", "most", "all", "any", "very",
    "too",
}

# Common tech acronyms that aren't meaningful proper nouns for contradiction detection
_TECH_ACRONYMS = {
    "api", "url", "sql", "html", "css", "json", "http", "https", "ssh",
    "vps", "cpu", "gpu", "ram", "dns", "utc", "hkt", "id", "db",
    "ai", "ml", "ui", "ux", "cli", "sdk", "mcp", "tls", "ssl", "eof",
    "aws", "gcp", "nft", "dao", "eth", "btc", "ok",
}

# Task-aware keyword sets
_TASK_KEYWORDS: Dict[str, Tuple[Set[str], float]] = {
    "debug": ({"error", "bug", "fix", "fixed", "failed", "wrong", "crash", "broken", "exception", "traceback"}, 1.5),
    "build": ({"pattern", "architecture", "design", "how to", "implement", "structure", "schema", "api"}, 1.5),
    "explore": (set(), 1.3),  # explore uses recency, not keywords
}

_EXPAND_PROMPT = """\
Rephrase this search query in 2 different ways (one-line each). \
Return ONLY the 2 lines, nothing else.
Query: {query}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _doc_id(chat_id: str, role: str, text: str, ts_ms: int) -> str:
    raw = f"{chat_id}|{role}|{text}|{ts_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _vec_to_blob(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _extract_proper_nouns(text: str) -> Set[str]:
    """Extract proper nouns from text for contradiction detection.

    Filters out common English words, tech acronyms, and requires 3+ chars.
    """
    raw = {m.lower() for m in _PROPER_NOUN_RE.findall(text)}
    return {
        w for w in raw
        if len(w) >= 3
        and w not in _STOPWORDS
        and w not in _TECH_ACRONYMS
    }


# ── MemoryManager ─────────────────────────────────────────────────────────────

class MemoryManager:
    """
    Two-layer memory:
      staging   — raw text accumulated during a session, no embeddings yet
      messages  — screened + embedded, used for semantic retrieval across sessions

    Screening happens once per session when flush_staging() is called (on /clear).
    """

    def __init__(self, client=None, db_path: Path = _DB_PATH, model_name: str = None) -> None:
        self._client = client
        self._model = model_name
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")

        # Long-term store: screened + embedded messages
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL,
                role        TEXT NOT NULL,
                ts          INTEGER NOT NULL,
                text        TEXT NOT NULL,
                embedding   BLOB NOT NULL,
                is_latest   INTEGER NOT NULL DEFAULT 1,
                memory_type TEXT NOT NULL DEFAULT 'dynamic'
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_chat ON messages(chat_id)")

        # Staging: unscreened messages for the current session
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS staging (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id  TEXT NOT NULL,
                role     TEXT NOT NULL,
                ts       INTEGER NOT NULL,
                text     TEXT NOT NULL
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_staging_chat ON staging(chat_id)")

        # Graceful migration for existing DBs
        self._migrate()

        self._conn.commit()
        logger.info("Vector memory DB ready at %s", db_path)

    def _migrate(self) -> None:
        """Add new columns to existing tables if missing."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "is_latest" not in existing:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN is_latest INTEGER NOT NULL DEFAULT 1"
            )
            logger.info("Migrated: added is_latest column")
        if "memory_type" not in existing:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN memory_type TEXT NOT NULL DEFAULT 'dynamic'"
            )
            logger.info("Migrated: added memory_type column")

    # ── Public API ────────────────────────────────────────────────────────────

    def store(self, chat_id: str, role: str, text: str) -> None:
        """Stage a message for later screening (called after every bot turn)."""
        if not text or not text.strip():
            return
        ts_ms = int(time.time() * 1000)
        try:
            self._conn.execute(
                "INSERT INTO staging (chat_id, role, ts, text) VALUES (?, ?, ?, ?)",
                (str(chat_id), role, ts_ms, text),
            )
            self._conn.commit()
        except Exception as exc:
            logger.warning("MemoryManager.store (staging) failed: %s", exc)

    def flush_staging(self, chat_id: str) -> int:
        """
        Screen this session's staged messages, embed the worthy ones, persist them.
        Called by /clear. Returns the number of messages saved to long-term memory.
        """
        rows = self._conn.execute(
            "SELECT id, role, ts, text FROM staging WHERE chat_id = ? ORDER BY id",
            (str(chat_id),),
        ).fetchall()

        if not rows:
            logger.info("No staged messages for chat %s — nothing to screen", chat_id)
            return 0

        logger.info("Screening %d staged messages for chat %s", len(rows), chat_id)

        worthy_indices, type_map = self._screen(rows)
        logger.info("Screener kept %d/%d messages for chat %s",
                    len(worthy_indices), len(rows), chat_id)

        saved = 0
        if worthy_indices:
            worthy_rows = [rows[i] for i in worthy_indices]
            texts = [r[3] for r in worthy_rows]
            vecs = self._embed_batch(texts)

            for (_, role, ts, text), vec, idx in zip(worthy_rows, vecs, worthy_indices):
                if vec is None:
                    continue
                new_vec = np.array(vec, dtype=np.float32)
                if self._is_duplicate(new_vec, str(chat_id)):
                    logger.info("Dedup skip (cosine ≥ 0.92): %.60s…", text)
                    continue
                doc_id = _doc_id(chat_id, role, text, ts)
                mem_type = type_map.get(idx, "dynamic")

                # Contradiction detection: mark older overlapping user memories
                if role == "user":
                    self._mark_superseded(str(chat_id), text)

                try:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO messages "
                        "(id, chat_id, role, ts, text, embedding, is_latest, memory_type) "
                        "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                        (doc_id, str(chat_id), role, ts, text, _vec_to_blob(vec), mem_type),
                    )
                    saved += 1
                except Exception as exc:
                    logger.warning("Failed to persist screened message: %s", exc)

        # Clear staging for this chat regardless
        staging_ids = [r[0] for r in rows]
        self._conn.execute(
            f"DELETE FROM staging WHERE id IN ({','.join('?' * len(staging_ids))})",
            staging_ids,
        )
        self._conn.commit()
        return saved

    def retrieve(
        self,
        chat_id: str,
        query: str,
        k: int = _TOP_K,
        expand: bool = True,
        task_context: Optional[str] = None,
    ) -> List[dict]:
        """
        Return top-K semantically relevant screened memories, sorted oldest-first.

        Args:
            chat_id:      chat to search within
            query:        search query text
            k:            number of results
            expand:       if True, generate alternative query phrasings for broader recall
            task_context: 'debug', 'build', 'explore', or None — adjusts scoring
        """
        if not query or not query.strip():
            return []

        # Start with original query only
        base_vec = self._embed_one(query)
        if base_vec is None:
            return []
        query_vecs = [np.array(base_vec, dtype=np.float32)]

        try:
            rows = self._conn.execute(
                "SELECT id, role, ts, text, embedding, is_latest, memory_type "
                "FROM messages WHERE chat_id = ?",
                (str(chat_id),),
            ).fetchall()
        except Exception as exc:
            logger.warning("MemoryManager.retrieve failed: %s", exc)
            return []

        if not rows:
            return []

        # First pass: score with original query only
        now_ms = int(time.time() * 1000)
        seen_ids: Set[str] = set()
        scored: List[Tuple[float, int, str, str, str]] = []  # (score, ts, role, text, doc_id)
        _MIN_SIM_THRESHOLD = 0.3

        for doc_id, role, ts, text, blob, is_latest, memory_type in rows:
            vec = _blob_to_vec(blob)
            best_sim = max(_cosine_similarity(qv, vec) for qv in query_vecs)

            # is_latest boost: prefer current facts over superseded ones
            if not is_latest:
                best_sim *= 0.5

            # Dynamic memory recency decay: exp(-age_days / 30)
            if memory_type == "dynamic":
                age_days = (now_ms - ts) / (1000 * 86400)
                decay = math.exp(-age_days / 30.0)
                best_sim *= decay

            # Task-aware boosts
            if task_context and task_context in _TASK_KEYWORDS:
                keywords, boost = _TASK_KEYWORDS[task_context]
                if task_context == "explore":
                    # Boost recent memories (last 7 days)
                    age_days = (now_ms - ts) / (1000 * 86400)
                    if age_days <= 7:
                        best_sim *= boost
                else:
                    text_lower = text.lower()
                    if any(kw in text_lower for kw in keywords):
                        best_sim *= boost

            if doc_id not in seen_ids:
                scored.append((best_sim, ts, role, text, doc_id))
                seen_ids.add(doc_id)

        scored.sort(key=lambda x: x[0], reverse=True)

        # Lazy expansion: only expand if initial results are poor
        good_results = sum(1 for s, *_ in scored[:k] if s > _MIN_SIM_THRESHOLD)
        if expand and good_results < 3 and rows:
            alt = self._expand_query(query)
            for q_text in alt:
                vec = self._embed_one(q_text)
                if vec is None:
                    continue
                query_vecs.append(np.array(vec, dtype=np.float32))
            # Re-score with expanded queries
            scored.clear()
            seen_ids.clear()
            for doc_id, role, ts, text, blob, is_latest, memory_type in rows:
                vec = _blob_to_vec(blob)
                best_sim = max(_cosine_similarity(qv, vec) for qv in query_vecs)
                if is_latest == 0:
                    best_sim *= 0.5
                if memory_type == "dynamic":
                    age_days = (now_ms - ts) / 86_400_000
                    best_sim *= math.exp(-age_days / 30)
                if doc_id not in seen_ids:
                    scored.append((best_sim, ts, role, text, doc_id))
                    seen_ids.add(doc_id)
            scored.sort(key=lambda x: x[0], reverse=True)
            logger.info("Lazy expansion triggered: %d good results < 3", good_results)

        top = scored[:k]
        top.sort(key=lambda x: x[1])  # oldest-first
        return [{"role": r, "ts": ts, "text": t} for _, ts, r, t, _ in top]

    def delete_all(self, chat_id: str) -> None:
        """Wipe everything (staging + long-term) for a chat. Use sparingly."""
        try:
            self._conn.execute("DELETE FROM messages WHERE chat_id = ?", (str(chat_id),))
            self._conn.execute("DELETE FROM staging WHERE chat_id = ?", (str(chat_id),))
            self._conn.commit()
            logger.info("Wiped all memories for chat_id=%s", chat_id)
        except Exception as exc:
            logger.warning("MemoryManager.delete_all failed: %s", exc)

    # ── Internal: contradiction detection ──────────────────────────────────────

    def _mark_superseded(self, chat_id: str, new_text: str) -> None:
        """
        Mark older user-role memories as not-latest if they cover the same
        entity/topic as the new memory (based on shared proper nouns).
        """
        new_nouns = _extract_proper_nouns(new_text)
        if not new_nouns:
            return

        try:
            old_rows = self._conn.execute(
                "SELECT id, text FROM messages "
                "WHERE chat_id = ? AND role = 'user' AND is_latest = 1",
                (chat_id,),
            ).fetchall()
        except Exception as exc:
            logger.warning("Contradiction check failed: %s", exc)
            return

        ids_to_supersede = []
        for doc_id, old_text in old_rows:
            old_nouns = _extract_proper_nouns(old_text)
            # Require at least 2 shared proper nouns to mark as superseded
            if len(new_nouns & old_nouns) >= 2:
                ids_to_supersede.append(doc_id)

        if ids_to_supersede:
            placeholders = ",".join("?" * len(ids_to_supersede))
            self._conn.execute(
                f"UPDATE messages SET is_latest = 0 "
                f"WHERE id IN ({placeholders})",
                ids_to_supersede,
            )
            logger.info(
                "Marked %d older memories as superseded for chat %s",
                len(ids_to_supersede), chat_id,
            )

    # ── Internal: query expansion ──────────────────────────────────────────────

    def _expand_query(self, query: str) -> List[str]:
        """Generate 1-2 alternative phrasings of the query via MiniMax."""
        try:
            resp = self._client.chat.completions.create(
                model=self._model or "kimi-for-coding",
                max_tokens=100,
                messages=[
                    {"role": "user", "content": _EXPAND_PROMPT.format(query=query)},
                ],
            )
            raw = resp.choices[0].message.content.strip()
            lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
            # Take at most 2 expansions
            return lines[:2]
        except Exception as exc:
            logger.warning("Query expansion failed (%s) — using original only", exc)
            return []

    # ── Internal: screening ───────────────────────────────────────────────────

    def _screen(self, rows: list) -> Tuple[List[int], Dict[int, str]]:
        """
        One LLM call over the batch.
        Returns (indices_to_keep, {index: memory_type}) where memory_type is
        'static' or 'dynamic'.
        """
        numbered = "\n".join(
            f"[{i}] {role}：{text}" for i, (_, role, _, text) in enumerate(rows)
        )
        try:
            resp = self._client.chat.completions.create(
                model=self._model or "kimi-for-coding",
                max_tokens=200,
                messages=[
                    {"role": "system", "content": _SCREEN_PROMPT},
                    {"role": "user", "content": numbered},
                ],
            )
            raw = resp.choices[0].message.content.strip()
            if not raw:
                return [], {}

            indices = []
            type_map: Dict[int, str] = {}

            for tok in raw.split(","):
                tok = tok.strip()
                # New format: "0:static" or "3:dynamic"
                if ":" in tok:
                    parts = tok.split(":", 1)
                    idx_str = parts[0].strip()
                    mtype = parts[1].strip().lower()
                    if idx_str.isdigit():
                        idx = int(idx_str)
                        if 0 <= idx < len(rows):
                            indices.append(idx)
                            type_map[idx] = mtype if mtype in ("static", "dynamic") else "dynamic"
                # Backwards compat: plain index (no type)
                elif tok.isdigit():
                    idx = int(tok)
                    if 0 <= idx < len(rows):
                        indices.append(idx)
                        type_map[idx] = "dynamic"  # default

            return indices, type_map
        except Exception as exc:
            logger.warning("Screener LLM call failed (%s) — keeping all as fallback", exc)
            all_indices = list(range(len(rows)))
            return all_indices, {i: "dynamic" for i in all_indices}

    # ── Internal: dedup ───────────────────────────────────────────────────────

    def _is_duplicate(self, vec: np.ndarray, chat_id: str, threshold: float = 0.92) -> bool:
        """Return True if any existing memory has cosine similarity >= threshold."""
        try:
            rows = self._conn.execute(
                "SELECT embedding FROM messages WHERE chat_id = ?",
                (chat_id,),
            ).fetchall()
        except Exception:
            return False
        for (blob,) in rows:
            if _cosine_similarity(vec, _blob_to_vec(blob)) >= threshold:
                return True
        return False

    # ── Internal: embeddings ──────────────────────────────────────────────────

    def _embed_one(self, text: str) -> Optional[List[float]]:
        result = self._embed_batch([text])
        return result[0] if result else None

    def _embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Single API call for multiple texts; returns parallel list of vectors."""
        try:
            resp = self._client.embeddings.create(model=_EMBED_MODEL, input=texts)
            vecs: List[Optional[List[float]]] = [None] * len(texts)
            for item in resp.data:
                vecs[item.index] = item.embedding
            return vecs
        except Exception as exc:
            logger.warning("Batch embedding failed (%s)", exc)
            return [None] * len(texts)


# ── Formatting ────────────────────────────────────────────────────────────────

def format_memory_block(memories: List[dict]) -> str:
    """Format retrieved memories into a prompt block for injection into system prompt."""
    if not memories:
        return ""
    lines = ["[Relevant past conversations from memory:]"]
    for m in memories:
        label = "User" if m["role"] == "user" else "You"
        lines.append(f"• {label}: {m['text']}")
    lines.append("[End of memory context]")
    return "\n".join(lines)
