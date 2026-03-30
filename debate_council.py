# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""R&D Council — 6-model debate with multi-round argumentation."""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from llm_client import chat_completion_multi, chat_completion_async

logger = logging.getLogger("debate_council")
HKT = timezone(timedelta(hours=8))
HISTORY_FILE = Path(__file__).parent / "debate_history.json"

MODELS = ["minimax", "cerebras", "deepseek", "gemini", "kimi", "qwen"]


async def run_debate(topic: str, context: str = "") -> dict:
    """Run a full 3-round debate on a topic.

    Returns dict with: topic, rounds, synthesis, consensus_score, action_items
    """

    # Round 1: Independent Analysis
    round1_prompt = [{"role": "user", "content":
        f"You are part of an R&D council. Give your independent analysis on this topic. "
        f"Be specific, opinionated, and actionable. Max 200 words.\n\n"
        f"Topic: {topic}\n"
        + (f"\nContext: {context}" if context else "")
    }]

    round1 = await chat_completion_multi(round1_prompt, max_tokens=500)
    round1_responses = {k: v for k, v in round1.items() if k != "errors" and v}

    # Round 2: Cross-Examination
    round1_summary = "\n\n".join(
        f"[{model}]: {response}"
        for model, response in round1_responses.items()
    )

    round2_tasks = {}
    for model in MODELS:
        if model not in round1_responses:
            continue
        prompt = [{
            "role": "user",
            "content": f"You are {model} in an R&D council debate about: {topic}\n\n"
                       f"Here are all models' Round 1 responses:\n\n{round1_summary}\n\n"
                       f"Your task:\n"
                       f"1. Which argument do you AGREE with most and why? (1 sentence)\n"
                       f"2. Which argument do you DISAGREE with and why? (1 sentence)\n"
                       f"3. Refine your own position based on what you learned. (2 sentences)\n"
                       f"Max 100 words."
        }]
        round2_tasks[model] = chat_completion_async(prompt, max_tokens=300)

    round2_raw = await asyncio.gather(
        *round2_tasks.values(), return_exceptions=True
    )
    round2_results = {}
    for model, result in zip(round2_tasks.keys(), round2_raw):
        if isinstance(result, Exception):
            logger.warning("Round 2 failed for %s: %s", model, result)
        else:
            round2_results[model] = result

    await asyncio.sleep(1)  # Rate limit breathing room

    # Round 3: Final Position
    round2_summary = "\n\n".join(
        f"[{model}]: {response}"
        for model, response in round2_results.items()
    )

    round3_tasks = {}
    for model in MODELS:
        if model not in round1_responses:
            continue
        prompt = [{
            "role": "user",
            "content": f"Final round. Topic: {topic}\n\n"
                       f"Cross-examination results:\n{round2_summary}\n\n"
                       f"Give your FINAL position in 2-3 sentences. "
                       f"Start with 'I changed my mind because...' or 'I maintain my position because...'"
        }]
        round3_tasks[model] = chat_completion_async(prompt, max_tokens=200)

    round3_raw = await asyncio.gather(
        *round3_tasks.values(), return_exceptions=True
    )
    round3_results = {}
    for model, result in zip(round3_tasks.keys(), round3_raw):
        if isinstance(result, Exception):
            logger.warning("Round 3 failed for %s: %s", model, result)
        else:
            round3_results[model] = result

    # Judge Synthesis
    all_rounds = (
        f"TOPIC: {topic}\n\n"
        f"=== ROUND 1 (Independent) ===\n{round1_summary}\n\n"
        f"=== ROUND 2 (Cross-Examination) ===\n{round2_summary}\n\n"
        f"=== ROUND 3 (Final Positions) ===\n"
        + "\n\n".join(f"[{m}]: {r}" for m, r in round3_results.items())
    )

    judge_prompt = [{
        "role": "user",
        "content": f"You are the judge of an R&D council debate. 6 AI models debated this topic across 3 rounds.\n\n"
                   f"{all_rounds}\n\n"
                   f"Produce an executive memo:\n"
                   f"1. CONSENSUS: What do all/most models agree on? (2-3 bullets)\n"
                   f"2. KEY DISAGREEMENTS: Where did they clash? Who had the stronger argument? (2-3 bullets)\n"
                   f"3. ACTION ITEMS: Top 3 specific, actionable next steps\n"
                   f"4. CONTRARIAN INSIGHT: What did only 1-2 models see that others missed?\n"
                   f"5. CONFIDENCE: Rate consensus 1-10\n"
                   f"Keep it under 300 words."
    }]

    synthesis = await chat_completion_async(judge_prompt, max_tokens=800)

    # Build result
    result = {
        "topic": topic,
        "timestamp": datetime.now(HKT).isoformat(),
        "models_participated": len(round1_responses),
        "rounds": {
            "round1": round1_responses,
            "round2": round2_results,
            "round3": round3_results,
        },
        "synthesis": synthesis,
    }

    # Save to history
    _save_history(result)

    return result


def _save_history(result):
    """Save debate to history file."""
    try:
        history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    except Exception:
        history = []
    history.append(result)
    history = history[-90:]  # Keep 90 days
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))


def format_memo(result: dict) -> str:
    """Format debate result as Telegram HTML message."""
    synthesis = result.get("synthesis", "No synthesis")
    models = result.get("models_participated", 0)
    topic = result.get("topic", "Unknown")
    ts = result.get("timestamp", "")[:16]

    return (
        f"<b>\U0001f4cb R&D COUNCIL MEMO</b>\n"
        f"\U0001f4c5 {ts}\n"
        f"\U0001f4ac Topic: {topic}\n"
        f"\U0001f916 Models: {models}/6\n"
        f"{'─' * 30}\n\n"
        f"{synthesis}"
    )


if __name__ == "__main__":
    import sys
    topic = " ".join(sys.argv[1:]) or "Should we prioritize outreach volume or message personalization?"
    result = asyncio.run(run_debate(topic))
    print(format_memo(result))
