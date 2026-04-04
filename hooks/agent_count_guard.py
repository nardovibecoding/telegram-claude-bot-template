#!/usr/bin/env python3
"""PreToolUse hook: block Agent spawns > 1 unless user specified count."""
import json
import sys
import time
from pathlib import Path

COUNTER_FILE = Path("/tmp/claude_agent_spawn_counter.json")
# Reset counter if last spawn was more than 5 seconds ago (new turn)
TURN_TIMEOUT = 5


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
            # Reset if stale (new turn)
            if now - counter.get("ts", 0) > TURN_TIMEOUT:
                counter = {"count": 0, "ts": now}
        except (json.JSONDecodeError, OSError):
            counter = {"count": 0, "ts": now}

    counter["count"] += 1
    counter["ts"] = now
    COUNTER_FILE.write_text(json.dumps(counter))

    if counter["count"] > 1:
        # Ask user for confirmation instead of hard block
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"Already spawning {counter['count'] - 1} agent(s). Allow another? ({counter['count']} total)",
            }
        }
        print(json.dumps(result))
        return

    print("{}")


if __name__ == "__main__":
    main()
