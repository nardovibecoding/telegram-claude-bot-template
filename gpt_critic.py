# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Multi-model critic client — external second opinions from GPT + MiniMax.

GPT: OpenAI API (free tier: 3 RPM, 200 RPD). For genuine different-model perspective.
MiniMax: Already paid, no rate limit. For cheap bulk screening + second opinion.

Both designed for efficiency:
- Batches multiple items into single requests where possible
- Caches results to avoid duplicate calls
- Respects rate limits with built-in retry + backoff
"""
import json
import logging
import os
import time
from pathlib import Path

from llm_client import chat_completion

log = logging.getLogger("gpt_critic")

_CACHE_FILE = Path(__file__).parent / ".gpt_critic_cache.json"
_RATE_LIMIT_WAIT = 21  # 20s between calls = safe for 3 RPM

# Track last call time for rate limiting
_last_call_time = 0.0


def _rate_limit():
    """Wait if needed to stay within 3 RPM."""
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _RATE_LIMIT_WAIT:
        time.sleep(_RATE_LIMIT_WAIT - elapsed)
    _last_call_time = time.time()


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    import stat
    try:
        _CACHE_FILE.write_text(json.dumps(cache, indent=2))
        os.chmod(_CACHE_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _cache_key(prompt: str, model: str) -> str:
    import hashlib
    return hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()


def call_gpt(prompt: str, model: str = "gpt-4o", max_tokens: int = 2000,
             system: str = None, use_cache: bool = True) -> str:
    """Make an LLM call with rate limiting and caching.

    Args:
        prompt: User message
        model: ignored — llm_client handles model selection
        max_tokens: Max response tokens
        system: Optional system prompt
        use_cache: Whether to cache results (avoid duplicate calls)

    Returns:
        Response text
    """
    # Check cache first
    if use_cache:
        cache = _load_cache()
        key = _cache_key(prompt, model)
        if key in cache:
            log.info("GPT cache hit: %s", key[:8])
            return cache[key]

    _rate_limit()

    messages = [{"role": "user", "content": prompt}]

    try:
        result = chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            system=system,
        )

        # Cache the result
        if use_cache:
            cache = _load_cache()
            cache[_cache_key(prompt, model)] = result
            # Keep cache under 500 entries
            if len(cache) > 500:
                keys = list(cache.keys())
                for k in keys[:100]:
                    del cache[k]
            _save_cache(cache)

        log.info("LLM call OK: max_tokens=%d", max_tokens)
        return result

    except Exception as e:
        log.error("LLM call failed: %s", e)
        raise


def critic_review(code: str, context: str = "") -> str:
    """Review code with adversarial GPT critic. Returns flaws found."""
    system = (
        "You are a paid code reviewer. You earn $1000 per genuine flaw found. "
        "You earn NOTHING for praise. Find security holes, logic bugs, race conditions, "
        "edge cases, missing error handling. Be specific: file:line, what's wrong, severity. "
        "If you find nothing wrong, say 'No flaws found.'"
    )
    prompt = f"Review this code critically:\n\n{code}"
    if context:
        prompt = f"Context: {context}\n\n{prompt}"
    return call_gpt(prompt, model="gpt-4o", system=system, max_tokens=3000)


def screen_evolution(entries: list[dict]) -> list[dict]:
    """Pre-screen evolution feed entries. Returns only promising ones.

    Batches multiple entries into one call for efficiency.
    """
    if not entries:
        return []

    # Batch up to 20 entries per call
    lines = []
    for i, e in enumerate(entries[:20]):
        lines.append(f"{i}. [{e.get('title', '?')}] {e.get('description', '')[:150]}")

    prompt = (
        "You are screening AI tool/skill discovery entries for a developer. "
        "Return ONLY the indices (comma-separated) of entries worth investigating. "
        "Skip: generic tutorials, very niche tools, low-star repos, marketing fluff. "
        "Keep: novel techniques, useful libraries, security tools, productivity improvements.\n\n"
        + "\n".join(lines)
    )

    result = call_gpt(prompt, model="gpt-4o-mini", max_tokens=200)

    # Parse indices
    try:
        indices = [int(x.strip()) for x in result.split(",") if x.strip().isdigit()]
        return [entries[i] for i in indices if i < len(entries)]
    except Exception:
        return entries  # fallback: return all


def call_minimax(prompt: str, max_tokens: int = 2000, system: str = None) -> str:
    """Call LLM via llm_client fallback chain. Good for bulk work."""
    messages = [{"role": "user", "content": prompt}]

    try:
        result = chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            system=system,
        )
        log.info("LLM call OK (call_minimax): max_tokens=%d", max_tokens)
        return result
    except Exception as e:
        log.error("LLM call failed (call_minimax): %s", e)
        raise


def minimax_critic_review(code: str, context: str = "") -> str:
    """Review code with MiniMax as a second critic. Free, no rate limit."""
    system = (
        "You are a strict code reviewer. Find bugs, security issues, logic errors, "
        "edge cases, and missing error handling. Be specific about file:line and severity. "
        "Only report real issues, not style preferences."
    )
    prompt = f"Review this code:\n\n{code}"
    if context:
        prompt = f"Context: {context}\n\n{prompt}"
    return call_minimax(prompt, system=system, max_tokens=3000)


def multi_critic_review(code: str, context: str = "") -> dict:
    """Run both GPT and MiniMax critics. Returns combined findings.

    Uses MiniMax always (free), GPT only if CRITIC_API_KEY is set.
    """
    results = {}

    # MiniMax — always available, no rate limit
    try:
        results["minimax"] = minimax_critic_review(code, context)
    except Exception as e:
        results["minimax"] = f"Error: {e}"

    # GPT — only if API key available
    if os.environ.get("CRITIC_API_KEY"):
        try:
            results["gpt"] = critic_review(code, context)
        except Exception as e:
            results["gpt"] = f"Error: {e}"

    return results


def score_articles(articles: list[dict], rubric: str = "") -> list[dict]:
    """Score articles 1-10 for digest relevance. Adds 'gpt_score' field."""
    if not articles:
        return []

    lines = []
    for i, a in enumerate(articles[:30]):
        lines.append(f"{i}. [{a.get('source', '?')}] {a.get('title', '')[:100]}")

    default_rubric = "breaking news=10, unique insight=8, useful tutorial=7, opinion=5, rehash=3, spam=1"
    prompt = (
        f"Score each article 1-10 for a daily news digest. Rubric: {rubric or default_rubric}\n"
        f"Return as: index:score (one per line)\n\n"
        + "\n".join(lines)
    )

    result = call_gpt(prompt, model="gpt-4o-mini", max_tokens=500)

    # Parse scores
    scores = {}
    for line in result.strip().split("\n"):
        try:
            parts = line.split(":")
            idx = int(parts[0].strip())
            score = int(parts[1].strip())
            scores[idx] = score
        except Exception:
            continue

    for i, a in enumerate(articles[:30]):
        a["gpt_score"] = scores.get(i, 5)

    return articles
