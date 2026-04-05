# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Rolling conversation compressor for persona bots.

Maintains a running summary of older messages that fall outside the
recent window, so context is preserved instead of silently dropped.

Flow:
  1. absorb_truncated() — called when MAX_HISTORY truncation drops messages
  2. maybe_compress()   — called before each API call; batches older messages
  3. get_summary_block() — returns formatted summary for system prompt injection
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_COMPRESS_MODEL = None  # resolved at runtime from llm_client
_COMPRESS_THRESHOLD = 5  # summarise after this many unsummarised messages accumulate
_MAX_SUMMARY_TOKENS = 500  # keep summary concise

_SUMMARY_PROMPT = """\
You are a conversation summariser. Your job is to maintain a rolling summary \
of a conversation between a user and an AI assistant.

Rules:
- Preserve ALL key facts: names, decisions, preferences, requests, numbers, dates
- Preserve the user's emotional tone and intent
- Remove filler, greetings, and repetition
- Write in the SAME LANGUAGE as the conversation (if Chinese, write Chinese)
- Be concise but complete — nothing important should be lost
- Output ONLY the updated summary, no preamble"""

_UPDATE_PROMPT = """\
Here is the existing conversation summary:
---
{existing}
---

Here are new messages to incorporate:
---
{new_messages}
---

Write an updated summary that merges the new information. \
Keep it concise. Same language as the conversation."""

_INITIAL_PROMPT = """\
Summarise this conversation so far. Preserve all key facts, decisions, \
and context. Same language as the conversation.

---
{messages}
---"""


class ConversationCompressor:
    """Per-conversation rolling summary manager."""

    def __init__(self, client=None, model_name: str = None) -> None:
        self._client = client
        self._model = model_name
        # conv_key -> running summary text
        self._summaries: dict[tuple, str] = {}
        # conv_key -> how many messages in current conv[] we've already summarised
        # (offset from start of the current conv list, NOT absolute)
        self._summarised_count: dict[tuple, int] = {}

    def absorb_truncated(self, conv_key: tuple, truncated: list[dict]) -> None:
        """Absorb messages about to be dropped by MAX_HISTORY truncation.

        Called BEFORE the truncation happens, so these messages aren't lost.
        This triggers an immediate (blocking) summarisation since the messages
        are about to disappear.
        """
        if not truncated:
            return
        existing = self._summaries.get(conv_key, "")
        new_text = self._format_messages(truncated)

        summary = self._call_summarise(existing, new_text)
        if summary:
            self._summaries[conv_key] = summary
            logger.info(
                "Compressor absorbed %d truncated messages for %s (%d chars)",
                len(truncated), conv_key, len(summary),
            )

        # After truncation the conv list is re-indexed, so reset counter.
        # The messages we already summarised are gone; the remaining conv
        # starts fresh from index 0.
        self._summarised_count[conv_key] = 0

    def maybe_compress(
        self, conv_key: tuple, conv: list[dict], recent_window: int = 10
    ) -> None:
        """Check whether older messages need summarising and do it if so.

        'Older' = messages in conv that are outside the recent_window.
        We only trigger when _COMPRESS_THRESHOLD unsummarised messages
        have accumulated, to avoid an LLM call on every message.
        """
        older_count = max(0, len(conv) - recent_window)
        if older_count == 0:
            return

        already = self._summarised_count.get(conv_key, 0)
        unsummarised = older_count - already
        if unsummarised < _COMPRESS_THRESHOLD:
            return

        # Grab the unsummarised older messages
        new_msgs = conv[already:older_count]
        existing = self._summaries.get(conv_key, "")
        new_text = self._format_messages(new_msgs)

        summary = self._call_summarise(existing, new_text)
        if summary:
            self._summaries[conv_key] = summary
            self._summarised_count[conv_key] = older_count
            logger.info(
                "Compressor updated summary for %s: %d msgs summarised, %d chars",
                conv_key, older_count, len(summary),
            )

    async def maybe_compress_async(
        self, conv_key: tuple, conv: list[dict], recent_window: int = 10
    ) -> None:
        """Async wrapper — runs summarisation in a thread to avoid blocking."""
        older_count = max(0, len(conv) - recent_window)
        already = self._summarised_count.get(conv_key, 0)
        unsummarised = older_count - already
        if unsummarised < _COMPRESS_THRESHOLD:
            return
        await asyncio.to_thread(self.maybe_compress, conv_key, conv, recent_window)

    def get_summary_block(self, conv_key: tuple) -> str:
        """Return formatted summary for injection into system prompt."""
        summary = self._summaries.get(conv_key, "")
        if not summary:
            return ""
        return (
            "[Earlier conversation summary:]\n"
            f"{summary}\n"
            "[End of summary — recent messages follow]"
        )

    def clear(self, conv_key: tuple) -> None:
        """Reset summary for a conversation (called on /clear)."""
        self._summaries.pop(conv_key, None)
        self._summarised_count.pop(conv_key, None)

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for m in messages:
            role = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{role}: {m['content']}")
        return "\n".join(lines)

    def _call_summarise(self, existing: str, new_messages: str) -> Optional[str]:
        """One LLM call to produce or update the summary."""
        if existing:
            user_content = _UPDATE_PROMPT.format(
                existing=existing, new_messages=new_messages
            )
        else:
            user_content = _INITIAL_PROMPT.format(messages=new_messages)

        try:
            resp = self._client.chat.completions.create(
                model=self._model or "kimi-for-coding",
                max_tokens=_MAX_SUMMARY_TOKENS,
                messages=[
                    {"role": "system", "content": _SUMMARY_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("Compressor summarisation failed: %s", exc)
            return None
