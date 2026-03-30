#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Evolution auto-feed: scan AI skills sources for useful agent tools,
MCP servers, Claude skills, and patterns. Push proposals to
evolution_database.json and notify admin via Telegram.

5 sources (pure skills/tools only — no AI news):
1. GitHub (trending AI + SKILL.md search, merged)
2. Anthropic (skills repo + blog, merged)
3. MCP Registries (Smithery + awesome-mcp, no GitHub search)
4. SkillsMP.com
5. InStreet/Coze

Run daily via cron or /rerun evolution."""

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")
from sanitizer import sanitize_external_content
from skill_library import add_skill as _add_to_library

log = logging.getLogger("evolution_feed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN_ADMIN", "")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "0"))
PERSONAL_GROUP = int(os.environ.get("PERSONAL_GROUP_ID", "0"))
EVOLUTION_THREAD = 151  # Evolution / skills thread
HKT = timezone(timedelta(hours=8))
DB_PATH = str(BASE_DIR / "evolution_database.json")
SENT_FLAG = str(BASE_DIR / ".evolution_feed_sent")

AI_SKILLS_KEYWORDS = [
    "agent skill", "agentic", "agent framework", "agent tool",
    "ai agent", "llm agent", "llm tool", "ai skill",
    "adk", "agent development kit",
    "mcp", "model context protocol", "mcp server", "mcp tool",
    "claude code", "claude skill", "anthropic", "claude agent",
    "claude plugin", "claude hook", "claude mcp",
    "cursor", "windsurf", "copilot agent", "gemini cli",
    "langchain agent", "crewai", "autogen", "openai agent",
    "skill.md", "agent config", "prompt engineering",
    "tool use", "function calling", "structured output",
    "telegram bot ai", "whisper api", "tts api", "voice agent",
    "browser automation ai", "web scraper ai",
    "self-improving agent", "self-evolving",
]


VALID_CATEGORIES = [
    "memory", "crawl", "evolution", "security", "office",
    "dev-tools", "content", "automation", "voice", "agent",
]

# Keyword fallback — used when MiniMax is unavailable
_CATEGORY_KEYWORDS = {
    "memory": ["memory", "remember", "recall", "persist", "sqlite", "storage"],
    "crawl": ["crawl", "scrape", "spider", "fetch", "rss", "feed", "news", "digest"],
    "evolution": ["evolve", "evolution", "self-improv", "upgrade", "optimize", "skill.md"],
    "security": ["security", "pentest", "exploit", "vuln", "audit", "offensive", "cve", "hack", "fsociety", "malware", "threat"],
    "office": ["word", "excel", "pdf", "powerpoint", "docx", "xlsx", "spreadsheet", "document"],
    "dev-tools": ["dev", "compiler", "lint", "test", "ci", "deploy", "git", "code review", "template", "workspace"],
    "content": ["content", "blog", "seo", "copywrite", "post", "twitter", "social", "publish"],
    "automation": ["automat", "workflow", "pipeline", "schedule", "cron", "trigger", "zapier", "n8n"],
    "voice": ["voice", "tts", "stt", "speech", "audio", "whisper", "elevenlabs"],
    "agent": ["agent", "multi-agent", "orchestrat", "adk", "crewai", "autogen", "langchain", "composable"],
}


def _keyword_fallback_category(text: str) -> str:
    """Score text against category keywords, return best match or empty string."""
    text_lower = text.lower()
    scores = {}
    for cat, kws in _CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in kws if kw in text_lower)
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else ""


def _fetch_readme_snippet(source_url: str) -> str:
    """Fetch first 600 chars of GitHub README via raw URL."""
    if "github.com" not in source_url:
        return ""
    try:
        # Convert github.com/user/repo → raw README
        parts = source_url.rstrip("/").split("/")
        if len(parts) < 5:
            return ""
        user, repo = parts[3], parts[4]
        import urllib.request
        for branch in ("main", "master"):
            raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/README.md"
            try:
                with urllib.request.urlopen(raw_url, timeout=5) as r:
                    return r.read(800).decode("utf-8", errors="ignore")[:600]
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _classify_category(name: str, description: str, source_url: str) -> str:
    """Classify a skill into a VALID_CATEGORIES using MiniMax + README, fallback to keywords."""
    readme = _fetch_readme_snippet(source_url)
    context = f"Name: {name}\nDescription: {description}\nREADME excerpt: {readme[:400]}"

    try:
        minimax_key = os.environ.get("MINIMAX_API_KEY", "")
        if minimax_key:
            from openai import OpenAI
            client = OpenAI(api_key=minimax_key, base_url="https://api.minimaxi.com/v1")
            cats = ", ".join(VALID_CATEGORIES)
            resp = client.chat.completions.create(
                model="MiniMax-M2.5",
                max_tokens=500,
                messages=[
                    {"role": "system", "content": (
                        f"You are a tool classifier. Given a tool's name, description and README, "
                        f"respond with EXACTLY ONE word from this list: {cats}. "
                        f"No explanation, no punctuation, just one category word."
                    )},
                    {"role": "user", "content": context},
                ],
            )
            raw = resp.choices[0].message.content or ""
            # Strip <think>...</think> reasoning blocks (MiniMax-M2.5-highspeed)
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            result = raw.lower().rstrip(".").strip()
            if result in VALID_CATEGORIES:
                return result
            log.warning("MiniMax returned unknown category '%s', falling back to keywords", result)
    except Exception as e:
        log.warning("MiniMax classify failed: %s", e)

    return _keyword_fallback_category(f"{name} {description} {readme}")


def _load_db():
    try:
        with open(DB_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def _make_id(title):
    return hashlib.md5(title.encode()).hexdigest()[:12]


def _already_exists(db, title):
    return any(e.get("id") == _make_id(title) for e in db)


def _matches(text):
    lower = text.lower()
    return any(kw in lower for kw in AI_SKILLS_KEYWORDS)


async def _fetch(url, timeout=15):
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout),
                             headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status == 200:
                    return sanitize_external_content(await r.text())
    except Exception as e:
        log.warning("Fetch %s: %s", url, e)
    return ""


# ── 1. GitHub: trending AI + SKILL.md search (merged) ─────────────────

async def _scan_github():
    """GitHub trending Python + SKILL.md repo search — AI skills only."""
    findings = []

    # Trending
    html = await _fetch("https://github.com/trending/python?since=daily")
    if html:
        repos = re.compile(r'<h2 class="h3[^"]*">\s*<a href="/([^"]+)"[^>]*>\s*(.*?)\s*</a>', re.DOTALL).findall(html)
        descs = re.compile(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', re.DOTALL).findall(html)
        for i, (path, _name) in enumerate(repos[:25]):
            name = re.sub(r'<[^>]+>', '', _name).strip()
            desc = re.sub(r'<[^>]+>', '', descs[i]).strip() if i < len(descs) else ""
            if _matches(f"{name} {desc}"):
                findings.append({"title": f"GitHub: {path}", "url": f"https://github.com/{path}",
                                 "description": desc[:200], "source": "GitHub"})

    # SKILL.md search
    for q in ["SKILL.md+agent+in:path&sort=updated", "claude+code+skill+in:readme&sort=updated"]:
        raw = await _fetch(f"https://api.github.com/search/repositories?q={q}&order=desc&per_page=5", 10)
        if not raw:
            continue
        try:
            for repo in json.loads(raw).get("items", []):
                name = repo.get("full_name", "")
                desc = repo.get("description", "") or ""
                stars = repo.get("stargazers_count", 0)
                findings.append({"title": f"GitHub: {name}", "url": repo.get("html_url", ""),
                                 "description": f"{desc[:150]} | Stars: {stars}", "source": "GitHub"})
        except Exception:
            continue

    # Dedup
    seen = set()
    return [f for f in findings if f["title"] not in seen and not seen.add(f["title"])][:15]


# ── 2. Anthropic: skills repo commits + blog (merged) ─────────────────

async def _scan_anthropic():
    """Anthropic skills repo (commits + directory) + blog RSS."""
    findings = []

    # Skills repo commits
    raw = await _fetch("https://api.github.com/repos/anthropics/skills/commits?per_page=5", 10)
    if raw:
        try:
            for c in json.loads(raw):
                msg = c.get("commit", {}).get("message", "")
                date = c.get("commit", {}).get("committer", {}).get("date", "")[:10]
                if msg and len(msg) > 5:
                    findings.append({"title": f"Anthropic: {msg[:60]}", "url": c.get("html_url", ""),
                                     "description": f"Skills repo update ({date})", "source": "Anthropic"})
        except Exception:
            pass

    # Skills directory listing
    raw2 = await _fetch("https://api.github.com/repos/anthropics/skills/contents/skills", 10)
    if raw2:
        try:
            for item in json.loads(raw2):
                if item.get("type") == "dir":
                    findings.append({"title": f"Anthropic Skill: {item['name']}", "url": item.get("html_url", ""),
                                     "description": "Official skill from anthropics/skills", "source": "Anthropic"})
        except Exception:
            pass

    # Blog RSS
    raw3 = await _fetch("https://www.anthropic.com/feed", 10)
    if raw3:
        for title, url, desc in re.compile(
            r'<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<description>(.*?)</description>.*?</item>',
            re.DOTALL,
        ).findall(raw3)[:3]:
            title = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', title).strip()
            desc = re.sub(r'<[^>]+>', '', re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', desc))[:200]
            findings.append({"title": f"Anthropic: {title[:70]}", "url": url,
                             "description": desc, "source": "Anthropic"})

    seen = set()
    return [f for f in findings if f["title"] not in seen and not seen.add(f["title"])][:12]


# ── 3. MCP Registries: Smithery + awesome-mcp (no GitHub overlap) ─────

async def _scan_mcp():
    """MCP registries — Smithery API + awesome-mcp-servers commits."""
    findings = []

    # Smithery
    raw = await _fetch("https://registry.smithery.ai/api/servers?sort=updated&limit=15", 10)
    if raw:
        try:
            data = json.loads(raw)
            servers = data if isinstance(data, list) else data.get("servers", [])
            for srv in servers:
                name = srv.get("name", "") or srv.get("qualifiedName", "")
                desc = srv.get("description", "") or ""
                url = srv.get("homepage", "") or f"https://smithery.ai/server/{name}"
                findings.append({"title": f"MCP: {name[:60]}", "url": url,
                                 "description": desc[:200], "source": "MCP Registry"})
        except Exception:
            pass

    # awesome-mcp-servers latest commit
    raw2 = await _fetch("https://api.github.com/repos/punkpeye/awesome-mcp-servers/commits?per_page=1", 10)
    if raw2:
        try:
            c = json.loads(raw2)[0]
            msg = c.get("commit", {}).get("message", "")
            if msg and len(msg) > 10:
                findings.append({"title": f"MCP List: {msg[:60]}",
                                 "url": "https://github.com/punkpeye/awesome-mcp-servers",
                                 "description": msg[:150], "source": "MCP Registry"})
        except Exception:
            pass

    return findings[:12]


# ── 4. SkillsMP.com ───────────────────────────────────────────────────

async def _scan_skillsmp():
    """SkillsMP.com via agentskills.in API."""
    findings = []
    for query in ["mcp", "agent", "telegram", "claude", "automation"]:
        raw = await _fetch(f"https://www.agentskills.in/api/skills?search={query}&limit=5", 10)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            skills = data if isinstance(data, list) else data.get("skills", data.get("data", []))
            for skill in (skills if isinstance(skills, list) else []):
                name = skill.get("name", "") or skill.get("title", "")
                if not name:
                    continue
                desc = skill.get("description", "") or ""
                url = skill.get("url", "") or f"https://skillsmp.com/skills/{name}"
                author = skill.get("author", "") or skill.get("owner", "")
                findings.append({"title": f"SkillsMP: {name[:60]}", "url": url,
                                 "description": f"{desc[:150]} | {author}", "source": "SkillsMP"})
        except Exception:
            continue

    seen = set()
    return [f for f in findings if f["title"] not in seen and not seen.add(f["title"])][:15]


# ── 5. InStreet (Coze) — ByteDance AI agents platform ─────────────────

async def _scan_instreet():
    """Scan InStreet ecosystem: 虾评 XiaPing (skills marketplace) + Coze 技能商店."""
    findings = []

    # 虾评 XiaPing — dedicated skills marketplace (197+ skills)
    html = await _fetch("https://xiaping.coze.site", 10)
    if html:
        for path, name in re.findall(r'href="(/skill[^"]*)"[^>]*>([^<]+)</a>', html)[:15]:
            name = name.strip()
            if name and len(name) >= 2:
                findings.append({"title": f"虾评: {name[:60]}", "url": f"https://xiaping.coze.site{path}",
                                 "description": "XiaPing skill marketplace (Coze/InStreet)", "source": "InStreet"})

    # Coze 技能商店 (official, 1000+ skills)
    html2 = await _fetch("https://www.coze.cn/skills", 10)
    if html2:
        for path, name in re.findall(r'href="(/skill[^"]*)"[^>]*>([^<]+)</a>', html2)[:10]:
            name = name.strip()
            if name and len(name) >= 2:
                findings.append({"title": f"Coze: {name[:60]}", "url": f"https://www.coze.cn{path}",
                                 "description": "Coze official skill store", "source": "InStreet"})

    # InStreet skills forum
    html3 = await _fetch("https://instreet.coze.site/skills", 10)
    if html3:
        for path, name in re.findall(r'href="(/[^"]+)"[^>]*>([^<]{5,80})</a>', html3)[:5]:
            name = name.strip()
            if name:
                findings.append({"title": f"InStreet: {name[:60]}", "url": f"https://instreet.coze.site{path}",
                                 "description": "InStreet skill sharing forum", "source": "InStreet"})

    seen = set()
    return [f for f in findings if f["title"] not in seen and not seen.add(f["title"])][:12]


# ── Main ──────────────────────────────────────────────────────────────

async def main():
    today = datetime.now(HKT).strftime("%Y-%m-%d")

    if os.path.exists(SENT_FLAG):
        with open(SENT_FLAG) as f:
            if today in f.read():
                log.info("Already ran for %s", today)
                return

    log.info("Scanning 5 AI skills sources...")

    results = await asyncio.gather(
        _scan_github(),           # 1. GitHub (trending + SKILL.md search)
        _scan_anthropic(),        # 2. Anthropic (skills repo + blog)
        _scan_mcp(),              # 3. MCP Registries (Smithery + awesome-mcp)
        _scan_skillsmp(),         # 4. SkillsMP.com
        _scan_instreet(),         # 5. InStreet (Coze AI agents)
        return_exceptions=True,
    )

    all_findings = []
    for r in results:
        if isinstance(r, list):
            all_findings.extend(r)
        elif isinstance(r, Exception):
            log.warning("Scanner failed: %s", r)

    if not all_findings:
        log.info("No AI skills findings today")
        with open(SENT_FLAG, "a") as f:
            f.write(today + "\n")
        return

    db = _load_db()
    new_entries = []

    for finding in all_findings:
        if _already_exists(db, finding["title"]):
            continue
        entry = {
            "id": _make_id(finding["title"]),
            "title": finding["title"],
            "type": "auto_discovery",
            "url": finding["url"],
            "description": finding["description"],
            "action": "Needs study — check if useful for our agent system",
            "status": "pending",
            "source": finding["source"],
            "created_at": datetime.now(HKT).isoformat(),
        }
        db.append(entry)
        new_entries.append(entry)

    _save_db(db)
    log.info("Found %d total, %d new", len(all_findings), len(new_entries))

    # Auto-add to skill library
    _SOURCE_MAP = {
        "GitHub": "github",
        "Anthropic": "anthropic",
        "MCP Registry": "smithery",
        "SkillsMP": "openclaw",
        "InStreet": "instreet",
    }
    lib_added = 0
    for entry in new_entries:
        lib_source = _SOURCE_MAP.get(entry.get("source", ""), "github")
        url = entry.get("url", "")
        desc = entry.get("description", "")
        category = _classify_category(entry["title"], desc, url)
        log.info("Classified '%s' → %s", entry["title"], category or "uncategorized")
        added = _add_to_library({
            "name": entry["title"],
            "source_url": url,
            "description": desc,
            "source": lib_source,
            "category": category,
        })
        if added:
            lib_added += 1
    if lib_added:
        log.info("Added %d entries to skill library", lib_added)

    if new_entries and BOT_TOKEN:
        import html as html_mod
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        bot = Bot(token=BOT_TOKEN)
        for entry in new_entries:
            url_line = f"\n🔗 {entry['url']}" if entry['url'] else ""
            safe_title = html_mod.escape(entry['title'][:60])
            safe_desc = html_mod.escape(entry['description'][:300])
            safe_source = html_mod.escape(entry['source'])
            text = (f"🧬 <b>Evolution: {safe_title}</b>{url_line}\n\n"
                    f"{safe_desc}\n\n<i>{safe_source}</i>")
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📖 Study", callback_data=f"evolve:study:{entry['id']}"),
                InlineKeyboardButton("❌ Pass", callback_data=f"evolve:no:{entry['id']}"),
            ]])
            try:
                await bot.send_message(chat_id=PERSONAL_GROUP, message_thread_id=EVOLUTION_THREAD,
                                       text=text[:4000], parse_mode="HTML", reply_markup=kb)
                await asyncio.sleep(0.5)
            except Exception as e:
                log.warning("Send failed: %s", e)

    with open(SENT_FLAG, "a") as f:
        f.write(today + "\n")


if __name__ == "__main__":
    asyncio.run(main())
