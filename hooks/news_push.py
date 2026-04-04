#!/usr/bin/env python3
"""UserPromptSubmit hook: surface pending tweet ideas into Claude session."""
import os
import json
import sys

PENDING_JSON = os.path.expanduser("~/.tweet_ideas_pending.json")
PENDING_TXT = os.path.expanduser("~/.tweet_ideas_pending")


def main():
    # Must consume stdin for UserPromptSubmit hooks
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        pass

    # Try new JSON format first, fall back to old text format
    if os.path.exists(PENDING_JSON):
        try:
            with open(PENDING_JSON, "r") as f:
                data = json.load(f)
            os.remove(PENDING_JSON)
        except Exception:
            print("{}")
            return

        items = data.get("items", [])
        if not items:
            print("{}")
            return

        lines = [f"📰 {len(items)} hot tweet idea(s):"]
        for item in items[:5]:
            title = item.get("title", "")
            url = item.get("url", "")
            source = item.get("source", "")
            if url:
                lines.append(f"  • {title} ({source})\n    {url}")
            else:
                lines.append(f"  • {title} ({source})")

        print(json.dumps({"systemMessage": "\n".join(lines)}))
        return

    # Legacy text format
    if os.path.exists(PENDING_TXT):
        try:
            with open(PENDING_TXT, "r") as f:
                content = f.read().strip()
            os.remove(PENDING_TXT)
        except Exception:
            print("{}")
            return
        if content:
            print(json.dumps({"systemMessage": content}))
            return

    print("{}")


if __name__ == "__main__":
    main()
