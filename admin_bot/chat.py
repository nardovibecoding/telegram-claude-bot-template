# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Message routing + MiniMax chat handler.

Routes messages to the right model using weighted scoring:
  MiniMax  → chat/greetings (1-2s)
  Haiku    → quick lookups (2-3s)
  Sonnet   → analysis/debugging (3-5s)
  Opus     → coding/implementation (8-15s)

Scoring system: each keyword adds weight to its category.
Highest total score wins. Ties → cheaper model. No more first-match bugs.

To swap in LLM classifier later: set ANTHROPIC_API_KEY and uncomment _classify_llm().
"""
import asyncio
import logging
import os
import re

from .config import SYSTEM_PROMPTS

log = logging.getLogger("admin")

# ── Weighted keyword scoring ──
# Format: (keyword, weight) — higher weight = stronger signal
# Weights: 3 = definitive, 2 = strong, 1 = weak/ambiguous

_OPUS_KW = [
    # English — action/implementation
    ("write code", 3), ("write a ", 2), ("implement", 3), ("refactor", 3), ("rewrite", 3),
    ("build a", 3), ("create file", 3), ("new module", 3), ("new script", 3),
    ("add feature", 2), ("add a ", 1),
    ("fix bug", 3), ("fix the", 3), ("fix this", 3), ("fix it", 2),
    ("fix code", 3), ("fix any", 2), ("fix ", 1),
    ("deploy", 2), ("migrate", 2), ("redesign", 3), ("restructure", 3),
    ("commit", 2), ("push to", 2), ("set up", 2), ("install", 2),
    ("configure", 2), ("cron", 2), ("hook", 2), ("schedule", 1),
    ("mactovps", 3), ("vpstomac", 3), ("split", 1), ("architect", 2),
    # Chinese
    ("写代码", 3), ("实现", 3), ("重构", 3), ("修复", 3), ("部署", 2),
    ("迁移", 2), ("重写", 3), ("新功能", 3), ("改代码", 3), ("加功能", 3),
    ("建一个", 3), ("做一个", 3), ("搞一个", 3),
    ("帮我改", 3), ("帮我写", 3), ("帮我做", 3), ("帮我建", 3), ("帮我加", 3),
    ("整个", 2), ("搞个", 2), ("弄个", 2), ("起一个", 2),
]

_SONNET_KW = [
    # English — analysis/investigation
    ("analyze", 3), ("review", 2), ("debug", 3), ("investigate", 3),
    ("diagnose", 3), ("compare", 2), ("evaluate", 2), ("explain code", 3),
    ("why does", 3), ("why is", 3), ("how does", 2), ("what went wrong", 3),
    ("research", 2), ("study", 2), ("plan", 1), ("design", 1),
    ("look at", 1), ("log", 1), ("error", 2),
    ("claude.md", 2), ("admin_bot", 2), ("bot_base", 2),
    (".py", 1), (".json", 1), ("codebase", 2),
    # Chinese
    ("分析", 3), ("检查", 2), ("调试", 3), ("排查", 3), ("研究", 2),
    ("对比", 2), ("评估", 2), ("为什么", 3), ("怎么回事", 3),
    ("什么问题", 3), ("出了什么", 3),
    ("帮我看", 2), ("你看看", 2), ("看下", 1), ("查一下", 2), ("查下", 1),
    ("看一下", 1), ("看看", 1),
]

_HAIKU_KW = [
    # English — quick lookups
    ("status", 2), ("translate", 3), ("what is", 2), ("what are", 2),
    ("how many", 2), ("show me", 1), ("look up", 2),
    ("weather", 3), ("time", 1), ("count", 1), ("quick", 1),
    # Chinese
    ("翻译", 3), ("几个", 2), ("几点", 2), ("天气", 3), ("多少", 2),
]

_CHAT_ONLY = (
    "hi", "hello", "hey", "thanks", "thank you", "ok", "yes", "no",
    "sure", "good", "nice", "cool", "great", "alright", "yo", "sup",
    "morning", "afternoon", "evening", "night",
    "早", "谢", "好的", "行", "嗯", "ok", "得",
)

# Sticky session: remember last model per thread (capped at 100 entries)
_last_model: dict[str, str] = {}
_MAX_STICKY = 100


def _has_chinese(text: str) -> bool:
    return any('\u4e00' <= c <= '\u9fff' for c in text)


def _score(text: str, keywords: list[tuple[str, int]]) -> int:
    """Sum weights of all matching keywords in text.

    Short keywords (≤4 chars) use word-boundary matching to avoid substring false
    positives (e.g. "log" inside "logic", "plan" inside "explain").
    Long keywords (≥5 chars) use plain substring — specific enough already.
    """
    score = 0
    for kw, w in keywords:
        if len(kw) <= 4:
            # word boundary: space/start/end around the keyword
            if re.search(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', text):
                score += w
        else:
            if kw in text:
                score += w
    return score


def pick_model(text: str, context_msgs: list[str] | None = None, thread_key: str | None = None) -> str:
    """Pick model via weighted scoring with conversation context.

    Args:
        text: current message
        context_msgs: up to 3 previous messages (oldest first) for context.
            Context keywords score at 50% weight — enough to influence ties
            and zero-score cases, but current message always dominates.

    Returns: 'minimax'|'haiku'|'sonnet'|'opus'
    """
    low = text.lower().strip()

    # 1. Explicit prefix override
    if low.startswith("opus "):
        return "opus"
    if low.startswith("sonnet "):
        return "sonnet"
    if low.startswith("haiku "):
        return "haiku"

    # 2. Pure greetings → MiniMax (but NOT if context was a work conversation)
    is_greeting = (
        len(low) < 20
        and any(low == s or (low.startswith(s) and len(low) < 20) for s in _CHAT_ONLY)
    )
    if is_greeting and not context_msgs:
        return "minimax"

    # 3. Score current message (full weight)
    s_opus = _score(low, _OPUS_KW)
    s_sonnet = _score(low, _SONNET_KW)
    s_haiku = _score(low, _HAIKU_KW)

    # 4. Add context from previous messages (50% weight via halved scores)
    if context_msgs:
        ctx_text = " ".join(m.lower() for m in context_msgs if m)[:1000]
        if ctx_text:
            ctx_opus = _score(ctx_text, _OPUS_KW)
            ctx_sonnet = _score(ctx_text, _SONNET_KW)
            ctx_haiku = _score(ctx_text, _HAIKU_KW)
            s_opus += ctx_opus // 2
            s_sonnet += ctx_sonnet // 2
            s_haiku += ctx_haiku // 2

    best = max(s_opus, s_sonnet, s_haiku)

    if best == 0:
        # No keywords in current msg OR context — use sticky session
        if is_greeting and not context_msgs:
            return "minimax"
        if thread_key and thread_key in _last_model:
            sticky = _last_model[thread_key]
            log.info("router: sticky %s (no keywords, using last model) for: %.60s", sticky, text)
            return sticky
        if _has_chinese(low):
            return "opus"
        if len(low) > 200:
            return "opus"
        return "opus"

    # Winner takes all. On tie: prefer cheaper model
    if best == s_haiku and s_haiku >= s_sonnet and s_haiku >= s_opus:
        code_refs = (".py", ".json", ".sh", "codebase", "admin_bot", "claude.md")
        if any(kw in low for kw in code_refs) or re.search(r'\bbot\b', low):
            result = "sonnet"
        else:
            result = "haiku"
    elif best == s_sonnet and s_sonnet >= s_opus:
        result = "sonnet"
    else:
        result = "opus"

    # Save for sticky session (evict oldest if over cap)
    if thread_key:
        _last_model[thread_key] = result
        if len(_last_model) > _MAX_STICKY:
            oldest = next(iter(_last_model))
            del _last_model[oldest]

    ctx_flag = f" +ctx({len(context_msgs)})" if context_msgs else ""
    log.info("router: %s (opus=%d sonnet=%d haiku=%d%s) for: %.60s",
             result, s_opus, s_sonnet, s_haiku, ctx_flag, text)
    return result


def needs_claude(text: str) -> bool:
    """Quick check — does this message need Claude Code or can MiniMax handle it?"""
    return pick_model(text) != "minimax"


MODEL_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

MODEL_EMOJI = {
    "minimax": "💬",
    "haiku": "⚡",
    "sonnet": "🟧",
    "opus": "🧠",
}


async def _minimax_reply(prompt: str, domain: str) -> str:
    """Quick chat reply for chat-style messages via LLM fallback chain."""
    import sys
    import os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from llm_client import chat_completion_async

    sys_prompt = SYSTEM_PROMPTS.get(domain, "You are a helpful assistant.")
    return await chat_completion_async(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        system=sys_prompt,
    )
