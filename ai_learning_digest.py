#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""AI Self-Evolution Digest — crawls AI ecosystem, proposes actionable
improvements to OUR system (CLAUDE.md, MCP tools, bot code).
Hermes-style: learn → propose mutation → human approve → apply.

NOT about installing external tools. About evolving our own system."""

import asyncio
import json
import os
import re
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from content_intelligence import ci

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

log = logging.getLogger("ai_evolve")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
EVOLUTION_DB = os.path.join(PROJECT_DIR, "evolution_database.json")
DIGEST_CACHE = os.path.join(PROJECT_DIR, ".ai_digest_cache.json")
PENDING_MUTATIONS = os.path.join(PROJECT_DIR, ".pending_mutations.json")
DRAFTS_DIR = os.path.join(PROJECT_DIR, "evolution_drafts")
SENT_FLAG = os.path.join(PROJECT_DIR, ".ai_digest_sent")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
ADMIN_CHAT_ID = -1003827304557  # Admin group
ADMIN_THREAD_ID = 151  # AI Evolution thread

HKT = timezone(timedelta(hours=8))

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AI-Evolution/1.0"}

# Reddit AI communities
REDDIT_SUBS = [
    "ClaudeAI", "mcp", "LocalLLaMA", "ChatGPTPro",
    "OpenAI", "artificial", "AIAgents", "MachineLearning",
]

# Our tech stack — filter for relevance
OUR_STACK = [
    "claude", "anthropic", "mcp", "patchright", "playwright",
    "telegram bot", "python bot", "fastapi", "uvicorn",
    "web scraping", "anti-bot", "stealth", "cookie",
    "prompt engineering", "system prompt", "claude.md",
    "agent skill", "self-improvement", "auto-improve",
    "error handling", "retry", "resilience",
    "xiaohongshu", "douyin", "reddit", "bilibili", "wechat",
    "residential proxy", "browser automation",
    "memory", "rag", "vector", "embedding",
    "workflow", "pipeline", "cron", "scheduler",
    "openclaw", "skill", "plugin",
]

# What we currently have — for context-aware proposals
OUR_SYSTEM = {
    "rules_file": "CLAUDE.md",
    "mcp_servers": [],
    "bots": "9 persona TG bots + admin bot (Claude Code bridge)",
    "digests": "news (daliu), crypto (sbf), X curation (4 bots), Reddit",
    "infra": "Hetzner VPS Helsinki, Mac sync every 10min",
    "ai_model": "Claude Sonnet/Opus via Claude Code, MiniMax M2.5 for personas",
    "browser": "Patchright for XHS, F2 for Douyin (migrating)",
}


def load_cache() -> dict:
    if os.path.exists(DIGEST_CACHE):
        with open(DIGEST_CACHE) as f:
            return json.load(f)
    return {"seen_urls": [], "last_run": ""}


def save_cache(cache: dict):
    with open(DIGEST_CACHE, "w") as f:
        json.dump(cache, f)


def load_evolution_db() -> list:
    if os.path.exists(EVOLUTION_DB):
        with open(EVOLUTION_DB) as f:
            return json.load(f)
    return []


def save_evolution_db(db: list):
    with open(EVOLUTION_DB, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def load_pending() -> dict:
    if os.path.exists(PENDING_MUTATIONS):
        with open(PENDING_MUTATIONS) as f:
            return json.load(f)
    return {}


def save_pending(items: dict):
    with open(PENDING_MUTATIONS, "w") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def item_id(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


# ── Source Fetchers ──────────────────────────────────────────────────

def fetch_reddit(sub: str, limit: int = 8) -> list:
    url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}&t=day"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200 or not resp.text.startswith("{"):
            return []
        posts = []
        for child in resp.json().get("data", {}).get("children", []):
            p = child["data"]
            if p.get("stickied"):
                continue
            posts.append({
                "title": p.get("title", ""),
                "url": f"https://reddit.com{p.get('permalink', '')}",
                "text": (p.get("selftext", "") or "")[:600],
                "score": p.get("score", 0),
                "comments": p.get("num_comments", 0),
                "sub": sub,
                "source": "reddit",
            })
        return posts
    except Exception:
        return []


def fetch_github_trending() -> list:
    """Find new repos relevant to our stack."""
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    queries = [
        f"mcp+server+created:>{since}",
        f"claude+agent+created:>{since}",
        f"telegram+bot+python+created:>{since}",
        f"self-improving+agent+created:>{since}",
    ]
    all_repos = []
    for q in queries:
        try:
            url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=5"
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            for item in resp.json().get("items", []):
                lic = item.get("license") or {}
                all_repos.append({
                    "title": item.get("full_name", ""),
                    "url": item.get("html_url", ""),
                    "text": (item.get("description", "") or "")[:300],
                    "stars": item.get("stargazers_count", 0),
                    "license": lic.get("spdx_id", "?"),
                    "source": "github",
                })
        except Exception:
            continue
    return all_repos


def fetch_skillsmp_trending() -> list:
    """Fetch trending skills from SkillsMP (Claude Code skills marketplace)."""
    try:
        # SkillsMP API or scrape trending page
        url = "https://skillsmp.com/api/skills?sort=trending&limit=10"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            # Fallback: scrape the homepage
            resp = requests.get("https://skillsmp.com/", headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                return []
            # Extract skill names from HTML
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            posts = []
            for card in soup.select("a[href*='/skills/']")[:10]:
                title = card.get_text(strip=True)[:80]
                href = card.get("href", "")
                if href and not href.startswith("http"):
                    href = f"https://skillsmp.com{href}"
                if title and len(title) > 3:
                    posts.append({
                        "title": f"[SkillsMP] {title}",
                        "url": href,
                        "text": "",
                        "stars": 0,
                        "source": "skillsmp",
                        "type": "skill",
                    })
            return posts

        # API response
        data = resp.json()
        posts = []
        for item in data.get("skills", data if isinstance(data, list) else [])[:10]:
            posts.append({
                "title": f"[SkillsMP] {item.get('name', item.get('title', ''))}",
                "url": item.get("url", f"https://skillsmp.com/skills/{item.get('slug', '')}"),
                "text": (item.get("description", "") or "")[:300],
                "stars": item.get("downloads", item.get("stars", 0)),
                "source": "skillsmp",
                "type": "skill",
            })
        return posts
    except Exception as e:
        log.warning(f"SkillsMP fetch failed: {e}")
        return []


def fetch_openclaw_trending() -> list:
    """Fetch trending from OpenClaw (Claude Code skills/plugins registry)."""
    try:
        resp = requests.get("https://openclaw.org/api/skills?sort=popular&limit=10",
                            headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("skills", [])
            return [{
                "title": f"[OpenClaw] {s.get('name', '')}",
                "url": s.get("url", f"https://openclaw.org/skills/{s.get('slug', '')}"),
                "text": (s.get("description", "") or "")[:300],
                "stars": s.get("downloads", 0),
                "source": "openclaw",
                "type": "skill",
            } for s in items[:10]]
        # Fallback: scrape
        resp2 = requests.get("https://openclaw.org/", headers=HEADERS, timeout=10)
        if resp2.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp2.text, "html.parser")
        posts = []
        for card in soup.select("a[href*='/skills/']")[:10]:
            title = card.get_text(strip=True)[:80]
            href = card.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://openclaw.org{href}"
            if title and len(title) > 3:
                posts.append({
                    "title": f"[OpenClaw] {title}",
                    "url": href, "text": "", "stars": 0,
                    "source": "openclaw", "type": "skill",
                })
        return posts
    except Exception as e:
        log.warning(f"OpenClaw fetch failed: {e}")
        return []


def fetch_anthropic_news() -> list:
    """Fetch latest from Anthropic blog/changelog."""
    try:
        resp = requests.get("https://docs.anthropic.com/en/docs/about-claude/models",
                            headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []
        for a in soup.select("a")[:20]:
            text = a.get_text(strip=True)
            href = a.get("href", "")
            if any(kw in text.lower() for kw in ["new", "update", "release", "model"]):
                if href and not href.startswith("http"):
                    href = f"https://docs.anthropic.com{href}"
                posts.append({
                    "title": f"[Anthropic] {text[:80]}",
                    "url": href, "text": "", "stars": 0,
                    "source": "anthropic", "type": "tool",
                })
        return posts[:5]
    except Exception as e:
        log.warning(f"Anthropic news fetch failed: {e}")
        return []


def fetch_hackernews() -> list:
    """Fetch top HN stories relevant to AI/dev tools."""
    try:
        # HN top stories API
        ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10).json()[:30]
        posts = []
        for sid in ids[:30]:
            item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5).json()
            if not item or item.get("type") != "story":
                continue
            title = item.get("title", "").lower()
            # Filter: only AI/dev/coding related
            if any(kw in title for kw in [
                "ai", "llm", "gpt", "claude", "agent", "mcp", "coding", "developer",
                "cursor", "copilot", "automation", "api", "open source", "model",
                "anthropic", "openai", "langchain", "rag", "vector", "embedding",
            ]):
                posts.append({
                    "title": f"[HN] {item.get('title', '')}",
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    "text": "",
                    "score": item.get("score", 0),
                    "source": "hackernews",
                    "type": "tool",
                })
            if len(posts) >= 8:
                break
        return posts
    except Exception as e:
        log.warning(f"HN fetch failed: {e}")
        return []


def fetch_producthunt() -> list:
    """Fetch today's top Product Hunt posts (AI/dev category)."""
    try:
        resp = requests.get(
            "https://www.producthunt.com/frontend/graphql",
            headers={**HEADERS, "Content-Type": "application/json"},
            json={"query": "{ posts(order: RANKING) { edges { node { name tagline url votesCount topics { edges { node { name } } } } } } }"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        edges = resp.json().get("data", {}).get("posts", {}).get("edges", [])
        posts = []
        for edge in edges[:20]:
            node = edge.get("node", {})
            topics = [t["node"]["name"].lower() for t in node.get("topics", {}).get("edges", [])]
            # Filter AI/dev related
            if any(kw in " ".join(topics) for kw in ["artificial intelligence", "developer tools", "saas", "api", "productivity"]):
                posts.append({
                    "title": f"[PH] {node.get('name', '')} — {node.get('tagline', '')}",
                    "url": node.get("url", ""),
                    "text": node.get("tagline", ""),
                    "stars": node.get("votesCount", 0),
                    "source": "producthunt",
                    "type": "tool",
                })
            if len(posts) >= 5:
                break
        return posts
    except Exception as e:
        log.warning(f"ProductHunt fetch failed: {e}")
        return []


def fetch_mcp_registries() -> list:
    """Fetch new/trending MCP servers from registries."""
    posts = []
    # Glama.ai
    try:
        resp = requests.get("https://glama.ai/mcp/servers?sort=new", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.select("a[href*='/mcp/servers/']")[:8]:
                title = card.get_text(strip=True)[:80]
                href = card.get("href", "")
                if title and len(title) > 3:
                    if not href.startswith("http"):
                        href = f"https://glama.ai{href}"
                    posts.append({
                        "title": f"[MCP] {title}",
                        "url": href,
                        "text": "",
                        "stars": 0,
                        "source": "mcp_registry",
                        "type": "skill",
                    })
    except Exception as e:
        log.warning(f"Glama fetch failed: {e}")
    return posts[:8]


def fetch_our_failure_logs() -> list:
    """Read recent errors from our own logs — basis for self-improvement."""
    items = []
    log_file = "/tmp/start_all.log"
    try:
        with open(log_file) as f:
            lines = f.readlines()[-2000:]
        errors = [l.strip() for l in lines if "ERROR" in l or "CRITICAL" in l]
        if errors:
            # Group by type
            error_types = {}
            for e in errors[-50:]:
                # Extract error class
                match = re.search(r'([\w.]+Error|[\w.]+Exception)', e)
                key = match.group(1) if match else "Unknown"
                error_types.setdefault(key, []).append(e)

            for err_type, examples in error_types.items():
                if len(examples) >= 2:  # Only recurring errors
                    items.append({
                        "title": f"Recurring error: {err_type} ({len(examples)}x)",
                        "text": examples[-1][:300],
                        "url": "",
                        "source": "self_logs",
                        "count": len(examples),
                    })
    except Exception:
        pass
    return items


# ── Intelligence Layer ───────────────────────────────────────────────

def classify_and_propose(posts: list, cache: dict) -> list:
    """Filter for relevance to OUR stack, generate evolution proposals."""
    seen = set(cache.get("seen_urls", []))
    proposals = []

    for p in posts:
        url = p.get("url", "")
        if url and url in seen:
            continue

        text = (p.get("title", "") + " " + p.get("text", "")).lower()

        # Score relevance to our stack
        relevance = sum(1 for kw in OUR_STACK if kw in text)
        if p.get("score", 0) > 200:
            relevance += 2
        if p.get("stars", 0) > 100:
            relevance += 2
        if p.get("source") == "self_logs":
            relevance += 5
        if p.get("source") == "skillsmp":
            relevance += 3  # Skills are directly applicable

        if relevance < 2:
            continue

        # Determine evolution type
        if p["source"] == "self_logs":
            evo_type = "🔧 Bug Fix"
            action = "Fix recurring error in our codebase"
        elif p["source"] == "skillsmp":
            evo_type = "🧩 Skill"
            action = "Evaluate this Claude Code skill for our workflow"
        elif "mcp" in text and ("new" in text or "server" in text or "tool" in text):
            evo_type = "🔌 MCP Discovery"
            action = "Evaluate if this MCP server adds value to our system"
        elif any(kw in text for kw in ["prompt", "system prompt", "claude.md", "instruction"]):
            evo_type = "📝 Prompt Evolution"
            action = "Propose CLAUDE.md rule mutation based on this pattern"
        elif any(kw in text for kw in ["error", "retry", "resilience", "fallback", "handling"]):
            evo_type = "🛡️ Resilience"
            action = "Improve error handling in our bot/MCP code"
        elif any(kw in text for kw in ["scraping", "anti-bot", "stealth", "proxy", "bypass"]):
            evo_type = "🕵️ Anti-Bot"
            action = "Apply this technique to our XHS/Douyin/Reddit scrapers"
        elif any(kw in text for kw in ["workflow", "pipeline", "automat", "cron", "schedul"]):
            evo_type = "⚡ Automation"
            action = "Adopt this workflow pattern in our system"
        elif any(kw in text for kw in ["memory", "rag", "context", "knowledge"]):
            evo_type = "🧠 Memory"
            action = "Improve our memory/context system with this approach"
        else:
            evo_type = "💡 Insight"
            action = "Learn from this and apply to our system"

        proposals.append({
            "id": item_id(url or p["title"]),
            "type": evo_type,
            "title": p["title"][:80],
            "description": p.get("text", "")[:200],
            "url": url,
            "action": action,
            "relevance": relevance,
            "source": p["source"],
            "stars": p.get("stars", 0),
            "score": p.get("score", 0),
        })

    # Balanced selection: ensure diversity across sources
    # Each source gets at least 1-2 slots, self_logs always first
    source_buckets = {}
    for p in proposals:
        source_buckets.setdefault(p["source"], []).append(p)

    # Sort within each bucket by relevance
    for src in source_buckets:
        source_buckets[src].sort(key=lambda x: x["relevance"], reverse=True)

    # Load preference history (studied topics boost similar ones)
    prefs = _load_preferences()
    liked_keywords = prefs.get("liked_keywords", [])

    # Boost relevance for items matching liked keywords
    if liked_keywords:
        for p in proposals:
            title_lower = p["title"].lower()
            if any(kw in title_lower for kw in liked_keywords):
                p["relevance"] = min(10, p["relevance"] + 2)

    # Pick: self_logs first, then round-robin from each source
    selected = []
    # Self-logs always included
    for p in source_buckets.pop("self_logs", [])[:2]:
        selected.append(p)

    # Round-robin: take top from each source
    remaining_sources = list(source_buckets.keys())
    round_num = 0
    while len(selected) < 50 and remaining_sources:
        for src in list(remaining_sources):
            if len(selected) >= 10:
                break
            bucket = source_buckets[src]
            if round_num < len(bucket):
                selected.append(bucket[round_num])
            else:
                remaining_sources.remove(src)
        round_num += 1

    return selected


PREFS_FILE = os.path.join(PROJECT_DIR, ".evolution_prefs.json")


def _load_preferences() -> dict:
    try:
        with open(PREFS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"liked_keywords": [], "skipped_sources": {}}


def _save_preferences(prefs: dict):
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


# ── Send to TG ───────────────────────────────────────────────────────

async def send_proposals(proposals: list):
    if not BOT_TOKEN:
        log.error("No TELEGRAM_BOT_TOKEN_ADMIN")
        return

    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    bot = Bot(token=BOT_TOKEN)

    now = datetime.now(HKT).strftime("%Y-%m-%d")

    async def _send(text, reply_markup=None):
        return await bot.send_message(
            chat_id=ADMIN_CHAT_ID, message_thread_id=ADMIN_THREAD_ID,
            text=text, parse_mode="HTML", reply_markup=reply_markup,
            disable_web_page_preview=True,
        )

    await _send(f"🧬 <b>Self-Evolution Digest</b> — {now}\n{len(proposals)} proposed mutations:")
    await asyncio.sleep(0.5)

    pending = load_pending()

    # Auto-expire proposals older than 24h — don't resend stale ones
    now_ts = datetime.now(HKT).timestamp()
    expired = [k for k, v in pending.items()
               if now_ts - v.get("created_at", 0) > 86400]
    for k in expired:
        del pending[k]
    if expired:
        log.info(f"Auto-expired {len(expired)} stale proposals")

    for p in proposals:
        pid = p["id"]
        if pid in pending:
            continue  # Already sent and not yet acted on
        p["created_at"] = now_ts
        pending[pid] = p

        stars = f" ★{p['stars']}" if p.get("stars") else ""
        score = f" ⬆{p['score']}" if p.get("score") else ""
        url_line = f"\n{p['url']}" if p.get("url") else ""

        text = (
            f"{p['type']}{stars}{score}\n"
            f"<b>{p['title']}</b>\n"
            f"{p['description'][:150]}\n"
            f"➡️ <i>{p['action']}</i>"
            f"{url_line}"
        )

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 Study", callback_data=f"evolve:study:{pid}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"evolve:no:{pid}"),
        ]])

        try:
            await _send(text, reply_markup=kb)
        except Exception as e:
            log.warning(f"Failed to send proposal {pid}: {e}")
        await asyncio.sleep(0.5)

    save_pending(pending)
    log.info(f"Sent {len(proposals)} evolution proposals")


# ── Main ─────────────────────────────────────────────────────────────

async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    if os.path.exists(SENT_FLAG):
        with open(SENT_FLAG) as f:
            if today in f.read():
                log.info(f"Already sent for {today}")
                return

    log.info("Starting Self-Evolution Digest...")

    cache = load_cache()
    all_posts = []

    # Phase 1: External intelligence
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = []
        for sub in REDDIT_SUBS:
            futures.append(pool.submit(fetch_reddit, sub))
        futures.append(pool.submit(fetch_github_trending))
        futures.append(pool.submit(fetch_skillsmp_trending))
        futures.append(pool.submit(fetch_openclaw_trending))
        futures.append(pool.submit(fetch_anthropic_news))
        futures.append(pool.submit(fetch_hackernews))
        futures.append(pool.submit(fetch_producthunt))
        futures.append(pool.submit(fetch_mcp_registries))

        for f in futures:
            try:
                all_posts.extend(f.result(timeout=20))
            except Exception:
                pass

    # Phase 2: Internal intelligence (our own failure logs)
    all_posts.extend(fetch_our_failure_logs())

    log.info(f"Collected {len(all_posts)} raw items")

    # Phase 3: Classify and propose mutations
    proposals = classify_and_propose(all_posts, cache)
    log.info(f"Generated {len(proposals)} evolution proposals")

    if proposals:
        try:
            await send_proposals(proposals)
        except Exception as e:
            log.error(f"Failed to send proposals: {e}")
            return  # Do NOT write flag — Auto Healer will catch this

        # Only write flag AFTER confirmed send
        with open(SENT_FLAG, "w") as f:
            f.write(today + "\n")
        log.info(f"✅ Flag written for {today}")

        # Update evolution database
        db = load_evolution_db()
        for p in proposals:
            db.append({**p, "proposed_at": today, "status": "proposed"})
        save_evolution_db(db[-200:])

        # Store + mark sent in shared content intelligence DB
        try:
            ci.store_stories_batch([
                {"title": p.get("title", ""), "url": p.get("url", ""),
                 "source": p.get("source", "evolution"),
                 "summary": p.get("description", "")}
                for p in proposals if p.get("title") and p.get("url")
            ])
            ci.mark_sent_by_urls(
                [p["url"] for p in proposals if p.get("url")],
                "ai_evolution"
            )
        except Exception as e:
            log.warning("content_intelligence failed: %s", e)

        # Update cache
        new_urls = [p["url"] for p in proposals if p.get("url")]
        cache["seen_urls"] = (cache.get("seen_urls", []) + new_urls)[-500:]
        cache["last_run"] = today
        save_cache(cache)
    else:
        log.info("No evolution proposals today — sending notice")
        # Still notify user so they know it ran
        try:
            from telegram import Bot
            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                message_thread_id=ADMIN_THREAD_ID,
                text="🧬 Self-Evolution Digest: 今日冇新 proposals。系統正常運行。",
            )
            with open(SENT_FLAG, "w") as f:
                f.write(today + "\n")
        except Exception as e:
            log.error(f"Failed to send empty notice: {e}")


if __name__ == "__main__":
    asyncio.run(main())
