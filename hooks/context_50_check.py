#!/usr/bin/env python3
"""UserPromptSubmit hook: force /s when context hits 50%.

Fires at the start of every user turn. Checks statusline JSON for context %.
At >=50%, injects urgent systemMessage telling Claude to run /s first.

Fires on EVERY turn above 50%. Resets naturally after /s triggers /clear.
Uses exit 0 + systemMessage (not exit 2, which blocks user prompt entirely).
"""
import json
import sys
from pathlib import Path

STATUSLINE_JSON = Path("/tmp/claude_statusline.json")
TRIGGER_PCT = 50


def main():
    try:
        hook_input = json.load(sys.stdin)
        prompt = hook_input.get("prompt", "")
    except (json.JSONDecodeError, EOFError):
        prompt = ""

    # Don't block if user is already running /s
    if prompt.strip().startswith("/s"):
        print("{}")
        return

    if not STATUSLINE_JSON.exists():
        print("{}")
        return

    try:
        data = json.loads(STATUSLINE_JSON.read_text())
        ctx_pct = float(data.get("context_window", {}).get("used_percentage", 0))
    except (ValueError, OSError, KeyError):
        print("{}")
        return

    if ctx_pct < TRIGGER_PCT:
        print("{}")
        return

    # systemMessage on every turn above 50%. Claude sees it and must act.
    # Fires repeatedly -- no "fire once" guard. After /s triggers /clear,
    # context resets and this stops firing.
    print(json.dumps({
        "systemMessage": (
            f"CONTEXT AT {ctx_pct:.0f}%. This is URGENT. "
            f"Run /s NOW (Skill tool, skill='s') BEFORE doing anything else. "
            f"Do not answer the user's question first. Do not ask permission. "
            f"Run /s immediately as your first action."
        )
    }))


if __name__ == "__main__":
    main()
