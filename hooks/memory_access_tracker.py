#!/usr/bin/env python3
# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 -- see LICENSE
"""PostToolUse hook: bump importance + access_count when a memory file is read."""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hook_base import run_hook

MEMORY_DIR = Path.home() / ".claude" / "projects" / f"-Users-{Path.home().name}" / "memory"
SKIP_FILES = {"MEMORY.md"}


def check(tool_name, tool_input, input_data):
    if tool_name != "Read":
        return False
    file_path = tool_input.get("file_path", "")
    return (
        "memory/" in file_path
        and file_path.endswith(".md")
        and Path(file_path).name not in SKIP_FILES
        and "/archive/" not in file_path
    )


def action(tool_name, tool_input, input_data):
    file_path = Path(tool_input.get("file_path", ""))
    if not file_path.exists():
        return None

    text = file_path.read_text()
    m = re.match(r"^(---\n)(.*?)(\n---\n)(.*)", text, re.DOTALL)
    if not m:
        return None

    fm_text = m.group(2)
    today = date.today().isoformat()

    # Parse current values
    access_count = 0
    importance = 50
    for line in fm_text.splitlines():
        if line.startswith("access_count:"):
            try:
                access_count = int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("importance:"):
            try:
                importance = int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass

    new_access = access_count + 1
    new_importance = min(100, importance + 3)

    # Update frontmatter fields
    def replace_field(fm, field, value):
        pattern = re.compile(rf"^{field}:.*$", re.MULTILINE)
        if pattern.search(fm):
            return pattern.sub(f"{field}: {value}", fm)
        return fm + f"\n{field}: {value}"

    new_fm = replace_field(fm_text, "access_count", new_access)
    new_fm = replace_field(new_fm, "last_accessed", today)
    new_fm = replace_field(new_fm, "importance", new_importance)

    new_text = m.group(1) + new_fm + m.group(3) + m.group(4)

    # Atomic write
    tmp = file_path.with_suffix(".tmp")
    tmp.write_text(new_text)
    tmp.rename(file_path)

    return None  # Silent -- no need to notify Claude


if __name__ == "__main__":
    run_hook(check, action, "memory_access_tracker")
