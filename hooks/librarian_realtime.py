#!/usr/bin/env python3
"""Stop hook: nudge Claude to file anything worth keeping to NardoWorld.

Fires after Claude's full response so it can capture atoms from the
complete exchange (user input + Claude's response), not just user input.

Tracks last_filed timestamp so /s synthesis knows what's already been filed.
"""
import json
import sys
import time
from pathlib import Path

LAST_FILED = Path.home() / "NardoWorld/meta/last_filed"


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    # Don't fire if stop was due to error
    stop_reason = hook_input.get("stop_reason", "")
    if stop_reason in ("error", "api_error"):
        print("{}")
        return

    # Update last_filed timestamp
    try:
        LAST_FILED.parent.mkdir(parents=True, exist_ok=True)
        LAST_FILED.write_text(str(int(time.time())))
    except OSError:
        pass

    print(json.dumps({
        "systemMessage": (
            "NardoWorld librarian — scan this turn for knowledge atoms.\n"
            "WHAT TO FILE (highest priority first):\n"
            "- Decisions: 'chose X over Y because Z' (e.g. moved hook from PostToolUse→UserPromptSubmit because systemMessage timing)\n"
            "- Corrections/bugs: something broke, why, how it was fixed\n"
            "- Architecture: how systems connect, what talks to what\n"
            "- Links: GitHub repos, tools, articles shared or discovered\n"
            "- People: notable names with context\n"
            "- Ideas: things to try later\n"
            "SKIP: routine code edits, file reads, trivial exchanges, anything already filed today.\n"
            "HOW: Glob ~/NardoWorld/**/*.md for existing article first. "
            "Update existing > create new. Use the article format from /librarian skill. "
            "Append to ~/NardoWorld/meta/librarian-log.md.\n"
            "OUTPUT: one visible line per filing: \U0001f4c1 Filed: filename.md (created|updated)\n"
            "If nothing worth filing: say nothing about NardoWorld."
        )
    }))


if __name__ == "__main__":
    main()
