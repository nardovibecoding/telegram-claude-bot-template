# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
X Curation Forecast Layer — signals & forward-looking analysis.

After X curation picks the day's top tweets, this module generates
a "Signals & Forecast" section using multi-model cross-check.

Usage:
    from x_forecast import generate_forecast

    text = await generate_forecast(curated_tweets, lang="en")
    # text is Telegram-ready markdown
"""

import asyncio
import logging
import re

from llm_client import cross_check

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

_FORECAST_PROMPT = """\
Based on today's curated crypto/AI Twitter content, analyze:

{tweet_summaries}

1. EMERGING SIGNALS: What trends are accelerating? What's getting mentioned for the first time?
2. NARRATIVE SHIFTS: What narratives are gaining/losing momentum vs last week?
3. CONTRARIAN TAKES: What did only 1-2 tweets mention that could become big?
4. 7-DAY FORECAST: What will matter next week that most people aren't watching?

Be specific. Name projects, protocols, narratives. No vague generalities.
Keep each section to 2-3 bullet points max. Total response under 400 words."""

_SYSTEM_PROMPT = (
    "You are a crypto/AI market analyst. You produce concise, actionable signal reports "
    "from curated Twitter feeds. Be contrarian where warranted. Avoid hype. "
    "Cite specific projects, protocols, and data points."
)


# ── Build tweet summary ──────────────────────────────────────────────────────

def _build_summary(curated_tweets: list[dict], max_tokens: int = 500) -> str:
    """Compress curated tweets into a summary block for the LLM prompt.

    Each tweet dict is expected to have at least 'text' or 'summary',
    and optionally 'author', 'metrics'.
    """
    lines = []
    for i, tw in enumerate(curated_tweets[:25], 1):
        author = tw.get("author", tw.get("user", ""))
        text = tw.get("summary", tw.get("text", tw.get("content", "")))
        if not text:
            continue
        # Truncate long tweets
        if len(text) > 200:
            text = text[:200] + "..."
        prefix = f"@{author}" if author else f"Tweet {i}"
        lines.append(f"- {prefix}: {text}")

    summary = "\n".join(lines)
    # Rough token cap (~4 chars per token)
    if len(summary) > max_tokens * 4:
        summary = summary[: max_tokens * 4] + "\n[truncated]"
    return summary


# ── Format output ─────────────────────────────────────────────────────────────

def _format_forecast(synthesis: str, disagreements: list[str]) -> str:
    """Format cross-check synthesis into Telegram-ready text."""
    # Extract bullet points from the synthesis
    bullets = []
    for line in synthesis.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Grab lines that look like bullets or numbered items
        if re.match(r"^[-*•]\s+", line):
            bullets.append(line.lstrip("-*• ").strip())
        elif re.match(r"^\d+[.)]\s+", line):
            bullets.append(re.sub(r"^\d+[.)]\s+", "", line).strip())

    # If extraction failed, take first 6 non-empty lines as bullets
    if len(bullets) < 3:
        bullets = [
            ln.strip() for ln in synthesis.split("\n")
            if ln.strip() and not ln.strip().startswith("CONSENSUS")
        ][:6]

    # Cap at 6 bullets
    bullets = bullets[:6]

    parts = ["\U0001f4e1 Signals & Forecast"]
    for b in bullets:
        parts.append(f"• {b}")

    if disagreements:
        # Pick the most interesting disagreement
        disagree_text = disagreements[0]
        if len(disagree_text) > 200:
            disagree_text = disagree_text[:200] + "..."
        parts.append(f"\n\u26a1 Model disagreement: {disagree_text}")

    return "\n".join(parts)


# ── Main function ─────────────────────────────────────────────────────────────

async def generate_forecast(
    curated_tweets: list[dict],
    lang: str = "en",
) -> str:
    """Generate signals & forecast from today's curated tweets.

    Uses multi-model cross-check to identify emerging trends and disagreements.

    Args:
        curated_tweets: list of curated tweet dicts (from x_curator).
        lang: 'en', 'zh', 'ai', 'lists'.

    Returns:
        Formatted forecast text ready for Telegram.
    """
    if not curated_tweets:
        logger.warning("No curated tweets provided — skipping forecast")
        return ""

    # 1. Build summary
    tweet_summary = _build_summary(curated_tweets)
    if not tweet_summary.strip():
        logger.warning("Empty tweet summary — skipping forecast")
        return ""

    logger.info("Generating forecast for %d tweets (lang=%s)", len(curated_tweets), lang)

    # 2. Cross-check across models
    prompt = _FORECAST_PROMPT.format(tweet_summaries=tweet_summary)
    messages = [{"role": "user", "content": prompt}]

    try:
        result = await cross_check(
            messages=messages,
            max_tokens=800,
            timeout=45,
            system=_SYSTEM_PROMPT,
        )
    except Exception as e:
        logger.error("Cross-check failed for forecast: %s", e)
        return ""

    synthesis = result.get("synthesis", "")
    disagreements = result.get("disagreements", [])

    if not synthesis or synthesis.startswith("All models failed"):
        logger.error("Forecast synthesis empty or all models failed")
        return ""

    # 3. Format
    forecast_text = _format_forecast(synthesis, disagreements)

    logger.info(
        "Forecast generated: %d chars, consensus=%s",
        len(forecast_text),
        result.get("consensus_score", "?"),
    )

    return forecast_text


# ── Standalone test ───────────────────────────────────────────────────────────

async def _test():
    """Quick test with sample tweet data."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sample_tweets = [
        {"author": "cobie", "summary": "Hyperliquid TVL just passed $2B. The perps meta is far from over."},
        {"author": "inversebrah", "summary": "Ethena yields compressing fast. USDe peg holding but for how long at 8%?"},
        {"author": "DefiIgnas", "summary": "Pendle is quietly becoming the yield layer for everything. TVL up 40% this month."},
        {"author": "0xMert_", "summary": "Solana TPS hitting 4000 consistently now. Firedancer testnet looking good."},
        {"author": "aixbt_agent", "summary": "AI agent tokens dumping across the board. VIRTUAL down 30% from ATH."},
        {"author": "Flood", "summary": "Basis trade getting crowded. CME premiums compressing. Smart money rotating to alts."},
        {"author": "Ansem", "summary": "Base ecosystem exploding. Aerodrome doing $50M daily volume. This is the Uniswap of L2s."},
        {"author": "punk6529", "summary": "RWA narrative accelerating. Ondo, Centrifuge, Maple all hitting new highs."},
    ]

    forecast = await generate_forecast(sample_tweets, lang="en")
    print("\n" + "=" * 60)
    print(forecast)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(_test())
