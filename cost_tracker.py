# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Cost tracking for MiniMax and Claude API usage.

Logs token usage to JSONL files and provides daily/weekly aggregation.
"""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).parent

MINIMAX_COST_LOG = PROJECT_DIR / "minimax_cost_log.jsonl"
CLAUDE_COST_LOG = PROJECT_DIR / "claude_cost_log.jsonl"

# Pricing per 1M tokens (USD) — approximate
_PRICING = {
    "MiniMax-M2.5": {"input": 0.50, "output": 1.50},
    "MiniMax-M2.5-highspeed": {"input": 0.50, "output": 1.50},
    "claude-haiku-3-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}


def log_minimax_cost(persona_id: str, input_tokens: int, output_tokens: int,
                     model: str = "MiniMax-M2.5") -> None:
    """Append MiniMax usage entry to JSONL log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "persona": persona_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    try:
        with open(MINIMAX_COST_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def log_claude_cost(persona_id: str, model: str,
                    input_tokens: int, output_tokens: int) -> None:
    """Append Claude usage entry to JSONL log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "persona": persona_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    try:
        with open(CLAUDE_COST_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD based on model pricing."""
    pricing = _PRICING.get(model)
    if not pricing:
        # Try partial match
        for key, p in _PRICING.items():
            if key.lower() in model.lower():
                pricing = p
                break
    if not pricing:
        return 0.0
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def _read_log(path: Path, since: datetime) -> list[dict]:
    """Read JSONL entries since a given datetime."""
    entries = []
    if not path.exists():
        return entries
    since_str = since.isoformat()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "") >= since_str:
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return entries


def _aggregate(entries: list[dict]) -> dict:
    """Aggregate entries into per-model and per-persona breakdowns."""
    by_model = {}
    by_persona = {}
    total_cost = 0.0

    for e in entries:
        model = e.get("model", "unknown")
        persona = e.get("persona", "unknown")
        inp = e.get("input_tokens", 0)
        out = e.get("output_tokens", 0)

        # Check for pre-computed cost_usd (claude_cost_log.jsonl format)
        cost = e.get("cost_usd", 0)
        if not cost:
            cost = _estimate_cost(model, inp, out)

        if model not in by_model:
            by_model[model] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
        by_model[model]["input_tokens"] += inp
        by_model[model]["output_tokens"] += out
        by_model[model]["cost_usd"] += cost
        by_model[model]["calls"] += 1

        if persona not in by_persona:
            by_persona[persona] = {"cost_usd": 0.0, "calls": 0}
        by_persona[persona]["cost_usd"] += cost
        by_persona[persona]["calls"] += 1

        total_cost += cost

    return {
        "total_cost_usd": total_cost,
        "by_model": by_model,
        "by_persona": by_persona,
        "total_calls": len(entries),
    }


def get_daily_costs() -> dict:
    """Get today's cost breakdown across all models."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    minimax_entries = _read_log(MINIMAX_COST_LOG, today_start)
    claude_entries = _read_log(CLAUDE_COST_LOG, today_start)

    return _aggregate(minimax_entries + claude_entries)


def get_weekly_costs() -> dict:
    """Get this week's cost breakdown across all models."""
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)

    minimax_entries = _read_log(MINIMAX_COST_LOG, week_start)
    claude_entries = _read_log(CLAUDE_COST_LOG, week_start)

    return _aggregate(minimax_entries + claude_entries)
