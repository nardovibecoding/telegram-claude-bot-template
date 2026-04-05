#!/usr/bin/env python3
"""PreToolUse hook: block >1 agent, force Claude to ask user how many."""
import json
import sys
import time
from pathlib import Path

COUNTER_FILE = Path("/tmp/claude_agent_spawn_counter.json")
TURN_TIMEOUT = 60  # reset counter after 60s gap (new turn)


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

    if counter["count"] > 1:
        # Block and tell Claude to ask how many
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"BLOCKED: Agent #{counter['count']}. Ask Bernard how many agents he wants first.",
                "additionalContext": "You tried to spawn more than 1 agent without asking. STOP. Ask Bernard: 'How many agents do you want for this?' Then spawn exactly that many.",
            }
        }
        print(json.dumps(result))
        return

    # First agent — always allowed
    print("{}")


if __name__ == "__main__":
    main()
