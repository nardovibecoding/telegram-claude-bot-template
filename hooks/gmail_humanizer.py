#!/usr/bin/env python3
"""PostToolUse hook: remind to run content-humanizer after creating Gmail drafts."""
import json
import sys


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("{}")
        return

    tool_name = input_data.get("tool_name", "")

    if tool_name != "mcp__claude_ai_Gmail__gmail_create_draft":
        print("{}")
        return

    print(json.dumps({
        "systemMessage": (
            "📝 **Gmail draft created.** Run content-humanizer on the draft body before sending. "
            "Remove AI patterns: delve, crucial, leverage, navigate, robust. "
            "Add personality and real voice."
        )
    }))


if __name__ == "__main__":
    main()
