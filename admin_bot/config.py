# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""All constants and configuration for admin bot."""
import os
import subprocess
from dotenv import load_dotenv

load_dotenv()

from utils import PROJECT_DIR  # noqa: E402

VERSION = "4.0.0"  # Bump on significant changes

def _git_short_hash():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_DIR, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"

GIT_HASH = _git_short_hash()
VERSION_STR = f"v{VERSION} ({GIT_HASH})"

TOKEN = os.environ["TELEGRAM_BOT_TOKEN_ADMIN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
BOTS = {
    "bot1": "bot1", "bot2": "bot2",
}
LOG_FILE = "/tmp/start_all.log"
LOG_FILES = {b: LOG_FILE for b in BOTS}
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
BOT_THREADS = {
    "bot1": 2, "bot2": 11,
}

SESSIONS_FILE = os.path.join(PROJECT_DIR, "claude_sessions.json")
DOMAINS_FILE = os.path.join(PROJECT_DIR, "domain_groups.json")

def _load_memory_index():
    """Load MEMORY.md index for context awareness."""
    mem_path = os.path.join(PROJECT_DIR, "memory", "MEMORY.md")
    try:
        with open(mem_path) as f:
            return f.read()
    except Exception:
        return ""

_MEMORY_INDEX = _load_memory_index()

_CONTENT_DRAFTS_INSTRUCTION = (
    "AUTO-SAVE TWEETS: When Owner shares insights, discoveries, surprising numbers, or ideas worth tweeting: "
    "1) Append to ~/telegram-claude-bot-template/content_drafts/running_log.md using: "
    "python3 -c \"from utils import save_to_content_drafts; save_to_content_drafts('TEXT', 'CATEGORY')\" "
    "(or use the Write/Edit tool directly). "
    "2) Reply with 💾 to confirm. "
    "3) Categories: insight, result, code, number, journey, mistake. "
    "Do NOT ask — just save silently when you detect something tweet-worthy."
)

_BASE_PROMPT = (
    "Owner's admin assistant. Read CLAUDE.md + ADMIN_HANDBOOK.md. "
    "Match Owner's language. Be concise — do it, show result, no narration. "
    "On failure: diagnose root cause from logs, never retry blindly. Learn from mistakes. "
    "Show cost/time estimate before expensive ops. "
    "NEVER restart bots — you ARE admin_bot. "
    "Code changes: git pull first, then add+commit+push. "
    + _CONTENT_DRAFTS_INSTRUCTION + " "
    "MEMORY INDEX (read relevant files for details):\n" + _MEMORY_INDEX
)
_CRAWL_WORKFLOW = (
    "SEARCH WORKFLOW (applies to all social media crawling): "
    "1) Analyze user's request — improve keywords, suggest filters, identify platform "
    "2) Show search plan: 'I'll search [platform] for [keywords] with [filters]. OK?' "
    "3) Wait for user confirmation before executing "
    "4) Execute search using the fastest tool (MCP > WebFetch > MediaCrawler) "
    "5) Format results clearly with metrics in a table "
    "Minimize API calls — use search results directly for sorting, only fetch details when user asks."
)

# Add your own group IDs and thread→domain mappings here.
# Example: TOOLS_GROUP = int(os.environ.get("TOOLS_GROUP_ID", "0"))
# Example: TOOLS_THREADS = {2: "email", 3: "search", 6: "translate"}
TEAM_E_GROUP = int(os.environ.get("TEAM_E_GROUP_ID", "0"))
TEAM_E_THREADS = {}  # Map thread IDs to domain names, e.g. {2: "email", 3: "search"}
PERSONAL_GROUP = int(os.environ.get("PERSONAL_GROUP_ID", "0"))
PERSONAL_THREADS = {}  # Map thread IDs to domain names, e.g. {2: "research", 3: "social"}

SYSTEM_PROMPTS = {
    "news": (
        f"{_BASE_PROMPT} "
        "Current domain: News Forum (telegram-claude-bot-template). "
        "You manage 8 persona bots + digests + X curation + Reddit."
    ),
    "team_a": (
        f"{_BASE_PROMPT} "
        "Current domain: Team A. "
        "Mission: find a vertical and build an app to dominate it. "
        "Team agents: Scout (market research), Builder (coding), Growth (distribution), Critic (idea challenge)."
    ),
    "team_a:scout": (
        f"{_BASE_PROMPT} "
        "You are the SCOUT agent for Team A. "
        "Your role: market research, competitor analysis, trend scanning, finding opportunities. "
        "Be data-driven. Cite sources. Rank opportunities by TAM and feasibility."
    ),
    "team_a:growth": (
        f"{_BASE_PROMPT} "
        "You are the GROWTH agent for Team A. "
        "Your role: distribution strategy, user acquisition, CAC estimates, viral loops, launch plans. "
        "Think like a growth hacker. Prioritize cheap/free channels first."
    ),
    "team_a:critic": (
        f"{_BASE_PROMPT} "
        "You are the CRITIC agent for Team A. "
        "Your role: challenge ideas, find flaws, play devil's advocate, stress-test assumptions. "
        "Be brutally honest. If an idea is bad, say so. Ask hard questions. "
        "Never be a yes-man. Your value is in killing bad ideas early."
    ),
    "team_a:builder": (
        f"{_BASE_PROMPT} "
        "You are the BUILDER agent for Team A. "
        "Your role: write production-quality code, build MVPs, implement features. "
        "NEVER commit half-done code. Test everything before declaring done. "
        "After coding, ALWAYS do: git add -A && git diff --cached to show what changed. "
        "Do NOT commit — wait for code review approval."
    ),
    # ── Example domains ──
    # Add your own domains here. Each key maps a domain name to a system prompt.
    # Register a domain with /domain <name> in a Telegram group thread.
    #
    # "email": (
    #     "You are Owner's email assistant. "
    #     "You have Gmail access via MCP tools. "
    #     "Tasks: read, search, draft emails."
    # ),
    # "research": (
    #     "You are a research assistant. "
    #     "Use WebSearch and WebFetch to find information. "
    #     "Summarize findings clearly with sources."
    # ),
}

PIDFILE = os.path.join(PROJECT_DIR, ".admin_bot.pid")
COOKIE_LOCK_FILE = os.path.join(PROJECT_DIR, ".cookie_refreshing")

_INFLIGHT_FILE = os.path.join(PROJECT_DIR, ".inflight_requests.json")
_HEARTBEAT_FILE = os.path.join(PROJECT_DIR, ".admin_heartbeat")
_BUSY_FILE = os.path.join(PROJECT_DIR, ".admin_busy")
_HEARTBEAT_THREAD = 152  # Healer/Heartbeat thread

_MAX_QUEUE_DEPTH = 3
_SPINNER = ["🟧", "🔶", "🟠", "🔸"]

_GMAIL_CHECK_PROMPT = """Check the Gmail inbox for emails from the past 24 hours that may need a reply.

Use Gmail search with `newer_than:1d is:inbox` to find recent unread emails.

For each email found:
- Read the full message to determine if it's personal and needs a reply
- Filter out automated notifications, promos, newsletters, mass mailings
- Summarize any emails that DO need a reply: who sent it, subject, brief summary

Present a clear summary:
- If emails need replies: list them with sender, subject, one-line summary
- If no emails need replies: "All clear — no emails needing a reply in the last 24 hours"
"""

# Rotating focus areas by day of week
_REVIEW_FOCUS = {
    0: "Code quality & dead code",
    1: "Performance & caching",
    2: "Reliability & retry logic",
    3: "Security & authorization",
    4: "Architecture & duplication",
    5: "Digest/cron operational health",
    6: "Feature ideas (top 3 most impactful)",
}
# Full checklist: references/review-checklist.md (loaded by send_code_review.py at runtime)
