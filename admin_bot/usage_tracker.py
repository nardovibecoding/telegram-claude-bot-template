# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Track command usage and auto-sort the Telegram command menu."""
import json
from pathlib import Path

USAGE_FILE = Path(__file__).parent.parent / ".command_usage.json"

ALL_COMMANDS = [
    ("menu", "Command menu"),
    ("status", "Bots, digests, disk, errors, sessions"),
    ("health", "Deep system + source check"),
    ("panel", "Control panel buttons"),
    ("stop", "Stop current task"),
    ("model", "Switch AI model"),
    ("digest", "Run digest (daliu,sbf,twitter,xcn,xai,xniche,reddit)"),
    ("skills", "Claude skills"),
    ("homein", "Resume from phone"),
    ("homeout", "Transfer to phone"),
    ("usage", "API cost breakdown"),
    ("library", "Skill library stats + discoveries"),
]


def track_usage(command: str):
    """Increment usage count for a command."""
    data = {}
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text())
        except Exception:
            pass
    data[command] = data.get(command, 0) + 1
    USAGE_FILE.write_text(json.dumps(data, indent=2))


def get_sorted_commands() -> list[tuple[str, str]]:
    """Return commands sorted by usage count (descending), then alphabetically."""
    data = {}
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text())
        except Exception:
            pass

    # /menu always first, rest sorted by usage
    rest = [(c, d) for c, d in ALL_COMMANDS if c != "menu"]
    sorted_rest = sorted(rest, key=lambda x: (-data.get(x[0], 0), x[0]))
    return [("menu", "Command menu")] + sorted_rest
