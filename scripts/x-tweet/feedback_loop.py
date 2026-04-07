#!/usr/bin/env python3
# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
"""Tweet quality feedback loop — learns from wins and losses.

Two-layer learning:
  Layer 1: Winners bank — posted tweets become few-shot examples
  Layer 2: Rejection autopsy — one-liner lessons from rejected drafts

Anti-overfit:
  - Lessons cap at 50, oldest drops
  - Lessons decay after 30 days unless pinned
  - Confidence tracking — lessons that don't help get dropped
  - 1-in-4 drafts ignore lessons (exploration wildcard)
  - Winners bank rotates (last 20 only)

Usage:
    python feedback_loop.py save-winner --text "tweet" --score 82
    python feedback_loop.py record-rejection --drafts '[{"text":"...","score":40}]'
    python feedback_loop.py get-context
    python feedback_loop.py decay
    python feedback_loop.py lessons
    python feedback_loop.py winners
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

_data_a = Path(__file__).parent / "data"
_data_b = Path(__file__).parent.parent / "data"
DATA_DIR = _data_a if _data_a.exists() else _data_b
DATA_DIR.mkdir(exist_ok=True)

WINNERS_PATH = DATA_DIR / "winners_bank.json"
LESSONS_PATH = DATA_DIR / "lessons.json"

MAX_WINNERS = 20
MAX_LESSONS = 50
LESSON_TTL_DAYS = 30
EXPLORATION_RATE = 0.25  # 1 in 4 drafts skip lessons

# Groq for lesson generation (free, fast)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Fallback: load from telegram-claude-bot/.env
if not GROQ_API_KEY:
    env_path = Path.home() / "telegram-claude-bot" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                GROQ_API_KEY = line.split("=", 1)[1].strip().strip('"')
                break


# ── Storage helpers ───────────────────────────────────────────────────

def _load_json(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return []
    return []


def _save_json(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Winners bank ──────────────────────────────────────────────────────

def save_winner(text: str, score: float, tweet_id: str = "") -> dict:
    """Save a posted tweet to the winners bank as a few-shot example."""
    winners = _load_json(WINNERS_PATH)

    winner = {
        "text": text,
        "score": score,
        "tweet_id": tweet_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    # Check for duplicate
    for w in winners:
        if w["text"] == text:
            return w

    winners.append(winner)

    # Cap at MAX_WINNERS, keep highest scored
    if len(winners) > MAX_WINNERS:
        winners.sort(key=lambda w: w["score"], reverse=True)
        winners = winners[:MAX_WINNERS]

    _save_json(WINNERS_PATH, winners)
    return winner


def get_winners(n: int = 5) -> list[dict]:
    """Get top N winners for few-shot prompting."""
    winners = _load_json(WINNERS_PATH)
    winners.sort(key=lambda w: w["score"], reverse=True)
    return winners[:n]


# ── Lessons ───────────────────────────────────────────────────────────

def _generate_lesson(rejected_drafts: list[dict], winners: list[dict]) -> str | None:
    """Use Groq to generate a one-liner lesson from rejected drafts."""
    if not GROQ_API_KEY:
        return None

    winner_examples = "\n".join(
        f"  GOOD: {w['text'][:150]}" for w in winners[:3]
    ) if winners else "  (no winners yet)"

    rejected_texts = "\n".join(
        f"  BAD: {d['text'][:150]}" for d in rejected_drafts[:3]
    )

    prompt = f"""You are analyzing rejected tweet drafts for @<github-user> (a vibecoding account).

These tweets were ACCEPTED (good examples):
{winner_examples}

These tweets were REJECTED:
{rejected_texts}

Write exactly ONE lesson (under 15 words) that explains why the rejected drafts failed compared to the good ones. Focus on the pattern, not the specific content.

Examples of good lessons:
- "Hook in first 5 words, not a slow build"
- "Show what you built, don't describe the concept"
- "Personal experience beats general advice"
- "One specific detail beats three vague claims"

Write ONLY the lesson, nothing else. No quotes, no explanation."""

    try:
        resp = httpx.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 50,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        lesson = resp.json()["choices"][0]["message"]["content"].strip()
        # Clean up: remove quotes, periods at end
        lesson = lesson.strip('"\'').rstrip(".")
        return lesson if len(lesson) < 100 else lesson[:97] + ".."
    except Exception as e:
        print(f"Lesson generation failed: {e}", file=sys.stderr)
        return None


def record_rejection(rejected_drafts: list[dict]) -> dict | None:
    """Generate and save a lesson from rejected drafts."""
    winners = get_winners(3)
    lesson_text = _generate_lesson(rejected_drafts, winners)

    if not lesson_text:
        return None

    lessons = _load_json(LESSONS_PATH)

    # Check for near-duplicate lessons
    for existing in lessons:
        # Simple overlap check
        existing_words = set(existing["lesson"].lower().split())
        new_words = set(lesson_text.lower().split())
        overlap = len(existing_words & new_words) / max(len(existing_words | new_words), 1)
        if overlap > 0.6:
            # Boost confidence of existing lesson instead
            existing["confidence"] = min(1.0, existing["confidence"] + 0.1)
            existing["reinforced_count"] = existing.get("reinforced_count", 0) + 1
            _save_json(LESSONS_PATH, lessons)
            return existing

    lesson = {
        "lesson": lesson_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "confidence": 0.5,  # starts neutral
        "reinforced_count": 0,
        "pinned": False,
        "rejected_drafts": [d["text"][:100] for d in rejected_drafts[:2]],
    }

    lessons.append(lesson)

    # Cap at MAX_LESSONS, drop oldest unpinned
    if len(lessons) > MAX_LESSONS:
        # Sort: pinned first, then by confidence, then by date
        unpinned = [l for l in lessons if not l.get("pinned")]
        pinned = [l for l in lessons if l.get("pinned")]
        unpinned.sort(key=lambda l: (l["confidence"], l["created_at"]))
        # Drop the weakest unpinned
        unpinned = unpinned[1:]
        lessons = pinned + unpinned

    _save_json(LESSONS_PATH, lessons)
    return lesson


def get_active_lessons() -> list[str]:
    """Get all non-expired lessons as one-liners for prompt injection."""
    lessons = _load_json(LESSONS_PATH)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LESSON_TTL_DAYS)).isoformat()

    active = []
    for l in lessons:
        # Skip expired (unless pinned)
        if not l.get("pinned") and l["created_at"] < cutoff:
            continue
        active.append(l["lesson"])

    return active


def should_explore() -> bool:
    """Return True if this draft should skip lessons (exploration mode)."""
    return random.random() < EXPLORATION_RATE


def decay_lessons() -> dict:
    """Remove expired lessons and reduce confidence of old ones."""
    lessons = _load_json(LESSONS_PATH)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LESSON_TTL_DAYS)).isoformat()

    kept = []
    expired = 0
    for l in lessons:
        if not l.get("pinned") and l["created_at"] < cutoff:
            expired += 1
            continue
        # Reduce confidence of old lessons slightly
        age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(
            l["created_at"].replace("Z", "+00:00")
        )).days
        if age_days > 14 and not l.get("pinned"):
            l["confidence"] = max(0.1, l["confidence"] - 0.02)
        kept.append(l)

    _save_json(LESSONS_PATH, kept)
    return {"kept": len(kept), "expired": expired}


# ── Context builder (for prompt injection) ────────────────────────────

def get_context() -> dict:
    """Build full context for tweet generation prompts."""
    exploring = should_explore()

    context = {
        "winners": [],
        "lessons": [],
        "exploring": exploring,
    }

    # Always include winners (positive examples are always useful)
    winners = get_winners(5)
    context["winners"] = [w["text"] for w in winners]

    # Include lessons unless exploring
    if not exploring:
        context["lessons"] = get_active_lessons()

    return context


def format_prompt_block(context: dict) -> str:
    """Format context into a prompt block for the tweet generator."""
    lines = []

    if context["winners"]:
        lines.append("## Your best tweets (match this quality):")
        for i, w in enumerate(context["winners"], 1):
            lines.append(f"{i}. {w}")
        lines.append("")

    if context["lessons"]:
        lines.append("## Lessons learned (avoid these mistakes):")
        for l in context["lessons"]:
            lines.append(f"- {l}")
        lines.append("")
    elif context["exploring"]:
        lines.append("## Mode: EXPLORATION — try something different from your usual style")
        lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tweet feedback loop")
    sub = parser.add_subparsers(dest="command")

    sw = sub.add_parser("save-winner")
    sw.add_argument("--text", required=True)
    sw.add_argument("--score", type=float, default=0)
    sw.add_argument("--id", default="")

    rr = sub.add_parser("record-rejection")
    rr.add_argument("--drafts", required=True, help="JSON array of {text, score}")

    sub.add_parser("get-context")
    sub.add_parser("decay")
    sub.add_parser("lessons")
    sub.add_parser("winners")

    args = parser.parse_args()

    if args.command == "save-winner":
        result = save_winner(args.text, args.score, args.id)
        print(json.dumps(result, indent=2))

    elif args.command == "record-rejection":
        drafts = json.loads(args.drafts)
        result = record_rejection(drafts)
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("No lesson generated")

    elif args.command == "get-context":
        ctx = get_context()
        print(format_prompt_block(ctx))

    elif args.command == "decay":
        result = decay_lessons()
        print(json.dumps(result))

    elif args.command == "lessons":
        lessons = _load_json(LESSONS_PATH)
        if not lessons:
            print("No lessons yet")
        else:
            for i, l in enumerate(lessons, 1):
                pin = " [pinned]" if l.get("pinned") else ""
                conf = f" (conf={l['confidence']:.1f})"
                print(f"{i}. {l['lesson']}{conf}{pin}")

    elif args.command == "winners":
        winners = get_winners(20)
        if not winners:
            print("No winners yet")
        else:
            for i, w in enumerate(winners, 1):
                print(f"{i}. [{w['score']:.0f}] {w['text'][:80]}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
