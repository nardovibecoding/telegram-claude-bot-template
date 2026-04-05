#!/usr/bin/env python3
"""PreToolUse hook: ask approval for >1 agent, hard block at >3."""
import json
import sys
import time
from pathlib import Path

COUNTER_FILE = Path("/tmp/claude_agent_spawn_counter.json")
TURN_TIMEOUT = 5  # reset counter after 5s gap (new turn)
MAX_AGENTS = 3    # hard cap


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("{}")
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Agent":
        print("{}")
        return

    now = time.time()

    # Load or reset counter
    counter = {"count": 0, "ts": now}
    if COUNTER_FILE.exists():
        try:
            counter = json.loads(COUNTER_FILE.read_text())
            if now - counter.get("ts", 0) > TURN_TIMEOUT:
                counter = {"count": 0, "ts": now}
        except (json.JSONDecodeError, OSError):
            counter = {"count": 0, "ts": now}

    counter["count"] += 1
    counter["ts"] = now
    COUNTER_FILE.write_text(json.dumps(counter))

    if counter["count"] > MAX_AGENTS:
        # Hard block above 3
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Hard cap: {MAX_AGENTS} agents max. Currently at {counter['count']}.",
            }
        }
        print(json.dumps(result))
        return

    if counter["count"] > 1:
        # Ask user for approval for 2nd and 3rd agent
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"Spawning agent #{counter['count']} of {MAX_AGENTS} max. Allow?",
            }
        }
        print(json.dumps(result))
        return

    # First agent — always allowed
    print("{}")


if __name__ == "__main__":
    main()
