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
    "daliu": "daliu", "sbf": "sbf",
}
LOG_FILE = "/tmp/start_all.log"
LOG_FILES = {b: LOG_FILE for b in BOTS}
GROUP_ID = -1003892866004
BOT_THREADS = {
    "daliu": 2, "sbf": 11,
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
    "AUTO-SAVE TWEETS: When Bernard shares insights, discoveries, surprising numbers, or ideas worth tweeting: "
    "1) Append to ~/telegram-claude-bot/content_drafts/running_log.md using: "
    "python3 -c \"from utils import save_to_content_drafts; save_to_content_drafts('TEXT', 'CATEGORY')\" "
    "(or use the Write/Edit tool directly). "
    "2) Reply with 💾 to confirm. "
    "3) Categories: insight, result, code, number, journey, mistake. "
    "Do NOT ask — just save silently when you detect something tweet-worthy."
)

_BASE_PROMPT = (
    "Bernard's admin assistant. Read CLAUDE.md + ADMIN_HANDBOOK.md. "
    "Match Bernard's language. Be concise — do it, show result, no narration. "
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

TEAM_E_GROUP = -1003756413928
TEAM_E_THREADS = {2: "email", 3: "airbnb", 6: "whatsapp"}
PERSONAL_GROUP = -1003827304557
PERSONAL_THREADS = {2: "personal:douyin", 3: "personal:xhs", 8: "personal:crm"}

SYSTEM_PROMPTS = {
    "news": (
        f"{_BASE_PROMPT} "
        "Current domain: News Forum (telegram-claude-bot). "
        "You manage 8 persona bots + digests + X curation + Reddit."
    ),
    "andrea": (
        f"{_BASE_PROMPT} "
        "Current domain: Team Andrea. "
        "Mission: find a vertical and build an app to dominate it. "
        "Current recommendation: AI Food Scanner (personalized allergy/diet). "
        "Team agents: Scout (market research), Builder (coding), Growth (distribution), Critic (idea challenge)."
    ),
    "andrea:scout": (
        f"{_BASE_PROMPT} "
        "You are the SCOUT agent for Team Andrea. "
        "Your role: market research, competitor analysis, trend scanning, finding opportunities. "
        "Be data-driven. Cite sources. Rank opportunities by TAM and feasibility."
    ),
    "andrea:growth": (
        f"{_BASE_PROMPT} "
        "You are the GROWTH agent for Team Andrea. "
        "Your role: distribution strategy, user acquisition, CAC estimates, viral loops, launch plans. "
        "Think like a growth hacker. Prioritize cheap/free channels first."
    ),
    "andrea:critic": (
        f"{_BASE_PROMPT} "
        "You are the CRITIC agent for Team Andrea. "
        "Your role: challenge ideas, find flaws, play devil's advocate, stress-test assumptions. "
        "Be brutally honest. If an idea is bad, say so. Ask hard questions. "
        "Never be a yes-man. Your value is in killing bad ideas early."
    ),
    "andrea:builder": (
        f"{_BASE_PROMPT} "
        "You are the BUILDER agent for Team Andrea. "
        "Your role: write production-quality code, build MVPs, implement features. "
        "NEVER commit half-done code. Test everything before declaring done. "
        "After coding, ALWAYS do: git add -A && git diff --cached to show what changed. "
        "Do NOT commit — wait for code review approval."
    ),
    "bella": (
        f"{_BASE_PROMPT} "
        "Current domain: Team Bella (爱颜 · Miss AI). "
        "Mission: grow and dominate the beauty/face analysis vertical. "
        "App location: ~/face-analysis-app/. "
        "Team agents: Scout (market research), Builder (coding), Growth (distribution), Critic (idea challenge)."
    ),
    "bella:scout": (
        f"{_BASE_PROMPT} "
        "You are the SCOUT agent for Team Bella (爱颜 · Miss AI). "
        "App location: ~/face-analysis-app/. "
        "Your role: competitor analysis in beauty/face analysis apps, user research, trend scanning. "
        "The product is already built — focus on market positioning, gaps vs competitors, and growth opportunities. "
        "Be data-driven. Cite sources."
    ),
    "bella:growth": (
        f"{_BASE_PROMPT} "
        "You are the GROWTH agent for Team Bella (爱颜 · Miss AI). "
        "App location: ~/face-analysis-app/. "
        "Your role: user acquisition, ASO, social media strategy, viral loops, influencer outreach. "
        "Think like a growth hacker. Prioritize cheap/free channels first."
    ),
    "bella:critic": (
        f"{_BASE_PROMPT} "
        "You are the CRITIC agent for Team Bella (爱颜 · Miss AI). "
        "App location: ~/face-analysis-app/. "
        "Your role: challenge growth strategies, find UX flaws, stress-test assumptions. "
        "Be brutally honest. Your value is in catching problems early."
    ),
    "bella:builder": (
        f"{_BASE_PROMPT} "
        "You are the BUILDER agent for Team Bella (爱颜 · Miss AI). "
        "App location: ~/face-analysis-app/. Working directory is ~/face-analysis-app/. "
        "Your role: write production-quality code for the face analysis app. "
        "NEVER commit half-done code. Test everything before declaring done. "
        "After coding, ALWAYS do: git add -A && git diff --cached to show what changed. "
        "Do NOT commit — wait for code review approval."
    ),
    "email": (
        "You are Bernard's email assistant for his father. "
        "You have Gmail access via MCP tools. "
        "Tasks: read, search, draft emails. Summarize in simple language. "
        "Be helpful and patient — the user may not be tech-savvy. "
        "Reply in the same language the user writes (English or Cantonese)."
    ),
    "airbnb": (
        "You are Bernard's Airbnb/travel assistant for his father. "
        "Tasks: search Airbnb listings, compare prices, filter by dates/location/guests/budget, "
        "check availability, summarize options clearly. "
        "Use Firecrawl or WebFetch to scrape Airbnb pages. "
        "Present results in a clear table format. "
        "Be helpful and patient — the user may not be tech-savvy. "
        "Reply in the same language the user writes (English or Cantonese)."
    ),
    "personal": (
        f"{_BASE_PROMPT} "
        "Current domain: AI Personal. "
        "Your role: crawl and search social media, shopping sites, and other platforms for Bernard. "
        "Tools: MediaCrawler (~/MediaCrawler/) for Douyin/XHS/Bilibili/Weibo/Kuaishou/Tieba/Zhihu. "
        "User will give keywords and criteria (views, likes, date range) — never ask for links. "
        "Filter and summarize results clearly."
    ),
    "personal:douyin": (
        f"{_BASE_PROMPT} {_CRAWL_WORKFLOW} "
        "Current domain: AI Personal — Douyin (抖音). "
        "Use MediaCrawler: cd ~/MediaCrawler && source venv/bin/activate && xvfb-run python main.py --platform douyin --type search --keywords \"keyword\"."
    ),
    "personal:crm": (
        f"{_BASE_PROMPT} "
        "Current domain: MEXC CRM Form Filler. "
        "Bernard forwards project pitches and info. Extract all relevant data and generate a JS console command "
        "to auto-fill the MEXC listing form. Save the JS to ~/clipboard/fill.txt. "
        "Save any screenshots as ~/clipboard/111.jpg, 222.jpg, etc. "
        "Read the MEXC CRM Form rules in CLAUDE.md for field mappings and logic. "
        "Always tell Bernard: open http://<vps-ip>:8888/fill.txt on Windows, copy all, paste in F12."
    ),
    "personal:xhs": (
        f"{_BASE_PROMPT} {_CRAWL_WORKFLOW} "
        "Current domain: AI Personal — Xiaohongshu (小红书). "
        "ALWAYS use xiaohongshu MCP tools (search_feeds, get_feed_detail, user_profile) — never curl/bash workarounds. "
        "search_feeds returns likes/favorites/shares — use for sorting. Only call get_feed_detail for full content/comments."
    ),
    "whatsapp": (
        "You are Bernard's WhatsApp assistant for his father. "
        "Tasks: help draft WhatsApp messages, translate messages, "
        "summarize long group chats, compose replies. "
        "Be helpful and patient — the user may not be tech-savvy. "
        "Reply in the same language the user writes (English or Cantonese)."
    ),
}

PIDFILE = os.path.join(PROJECT_DIR, ".admin_bot.pid")
COOKIE_LOCK_FILE = os.path.join(PROJECT_DIR, ".cookie_refreshing")

_INFLIGHT_FILE = os.path.join(PROJECT_DIR, ".inflight_requests.json")
_HEARTBEAT_FILE = os.path.join(PROJECT_DIR, ".admin_heartbeat")
_BUSY_FILE = os.path.join(PROJECT_DIR, ".admin_busy")
_HEARTBEAT_THREAD = 152  # Healer/Heartbeat thread

_MAX_QUEUE_DEPTH = 3
_SPINNER = ["🟧", "🔶", "🟠", "🔸"]

_GMAIL_CHECK_PROMPT = """Check the Gmail inbox for emails from the past 24 hours that may need a reply from these contacts:

1. **Stefano** — any personal email (not marketplace/platform notifications)
2. **Edwin** — any personal email (not LinkedIn notifications)
3. **Berlin** — any personal email
4. **Banks** — only personal/transactional emails requiring a response. Exclude promos, regulatory notices, newsletters, automated notifications (OTP, service updates, password resets)
5. **Anyone from Sigmagest** (@sigmagest.it) — only emails where a real person expects a personal response. Exclude "Circolare" mass mailings, regulatory update notices, newsletters, bulk notifications

For each contact, use Gmail search with `newer_than:1d is:inbox`.

For each email found:
- Read the full message to determine if it's personal and needs a reply
- Filter out automated notifications, promos, circulars, mass mailings
- Summarize any emails that DO need a reply: who sent it, subject, brief summary

Present a clear summary:
- If emails need replies: list them with sender, subject, one-line summary
- If no emails need replies: "All clear — no emails needing a reply from your key contacts in the last 24 hours"
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
