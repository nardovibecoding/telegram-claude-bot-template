# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Menu category definitions and keyboard builders for /menu."""
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Category definitions: (callback_key, emoji_label, [commands...])
CATEGORIES = [
    ("quick", "\u26a1 Quick", ["status", "panel", "stop", "model"]),
    ("ops", "\U0001f527 Operations", ["health", "digest", "skills"]),
    ("sessions", "\U0001f504 Sessions", ["homein", "homeout"]),
    ("info", "\U0001f4ca Info", ["usage"]),
]

# Short descriptions for sub-commands
CMD_DESCRIPTIONS = {
    "status": "Bots, digests, disk, errors, sessions",
    "panel": "Control panel buttons",
    "stop": "Stop current task",
    "model": "Switch AI model",
    "health": "Deep system + source check",
    "digest": "Run digest (daliu,sbf,twitter,xcn,xai,xniche,reddit)",
    "skills": "Claude skills",
    "homein": "Resume from phone",
    "homeout": "Transfer to phone",
    "usage": "API cost breakdown",
}


def build_category_keyboard() -> InlineKeyboardMarkup:
    """Build top-level category keyboard (2 per row)."""
    buttons = []
    row = []
    for key, label, _cmds in CATEGORIES:
        row.append(InlineKeyboardButton(label, callback_data=f"menu:{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def build_subcmd_keyboard(category_key: str) -> InlineKeyboardMarkup:
    """Build sub-command keyboard for a given category."""
    cmds = []
    for key, _label, cmd_list in CATEGORIES:
        if key == category_key:
            cmds = cmd_list
            break

    buttons = []
    row = []
    for cmd in cmds:
        desc = CMD_DESCRIPTIONS.get(cmd, cmd)
        row.append(InlineKeyboardButton(
            f"/{cmd} — {desc}",
            callback_data=f"menu:cmd:{cmd}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Back button
    buttons.append([InlineKeyboardButton("← Back", callback_data="menu:back")])
    return InlineKeyboardMarkup(buttons)


def _build_skills_keyboard() -> InlineKeyboardMarkup:
    """Dynamically list skills from ~/.claude/skills/."""
    skills_dir = os.path.expanduser("~/.claude/skills")
    buttons = []
    if os.path.isdir(skills_dir):
        names = sorted(
            n for n in os.listdir(skills_dir)
            if os.path.isfile(os.path.join(skills_dir, n, "SKILL.md"))
        )
        row = []
        for name in names:
            row.append(InlineKeyboardButton(
                f"/{name}",
                callback_data=f"skill:{name}",
            ))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

    if not buttons:
        btn = InlineKeyboardButton("(no skills found)", callback_data="noop")
        buttons.append([btn])

    buttons.append([InlineKeyboardButton("← Back", callback_data="menu:back")])
    return InlineKeyboardMarkup(buttons)


def category_label(key: str) -> str:
    """Return the emoji+label for a category key."""
    for k, label, _cmds in CATEGORIES:
        if k == key:
            return label
    return key
