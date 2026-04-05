#!/usr/bin/env python3
# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Team Andrea Scout — daily digest of app ideas to build via vibe-coding.

Scans ProductHunt, HN Show HN, Reddit, TechCrunch for AI-themed app opportunities
in traditional industry verticals. Generates full investment-style briefs.
Top 5 ideas get a Dragons' Den evaluation via 6-model cross-check.

Schedule: daily at 09:00 HKT (01:00 UTC)
Usage: python andrea_scout.py
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

from utils import PROJECT_DIR

load_dotenv()

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("andrea_scout")

# Optional: multi-model cross-check for top ideas
try:
    from llm_client import chat_completion, cross_check
    HAS_CROSS_CHECK = True
except ImportError:
    try:
        from llm_client import chat_completion
    except ImportError:
        chat_completion = None
    HAS_CROSS_CHECK = False
    logger.warning("cross_check not available - Dragons Den evaluation disabled")

ADMIN_TOKEN = os.environ["TELEGRAM_BOT_TOKEN_ADMIN"]

SEEN_FILE = Path(PROJECT_DIR) / ".andrea_scout_seen.json"

SCOUT_CHANNEL_ID = -1003832114726
SCOUT_THREAD_ID = 3  # Market Research topic

MAX_AGE_HOURS = 48  # look back 48h to catch weekend gaps

# ── Verticals ─────────────────────────────────────────────────────────────────

VERTICALS = [
    "Legal", "Healthcare", "Finance", "Education", "Real Estate",
    "HR/Recruiting", "Gov/Compliance", "Trades", "Insurance", "Accounting",
    "Logistics", "Food & Beverage", "Retail", "Hospitality", "Beauty & Wellness",
    "Architecture/Construction", "Marketing Agencies", "Media/Publishing",
    "Nonprofits", "Agriculture", "Auto", "Childcare/Education Admin",
    "Mental Health", "Senior Care", "Events/Weddings", "Pet Care",
    "Sports/Fitness", "Travel", "E-commerce Sellers", "Freelancers/Solopreneurs",
]

# ── Data sources ───────────────────────────────────────────────────────────────

RSS_FEEDS = {
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "TechCrunch Apps":     "https://techcrunch.com/category/apps/feed/",
    "VentureBeat AI":      "https://venturebeat.com/category/ai/feed/",
    "TheNextWeb":          "https://thenextweb.com/feed/",
    "Indie Hackers":       "https://www.indiehackers.com/feed.xml",
}

REDDIT_SUBS = [
    "SideProject", "startups", "AppIdeas", "Entrepreneur",
    "webdev", "nocode", "indiehackers",
]

HN_API = "https://hacker-news.firebaseio.com/v0"
PH_RSS = "https://www.producthunt.com/feed"

UA = "AndreaScout/1.0"


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_seen(seen: set) -> None:
    # Keep max 2000 IDs
    ids = list(seen)[-2000:]
    SEEN_FILE.write_text(json.dumps(ids))


# ── Fetchers ───────────────────────────────────────────────────────────────────

def _cutoff_ts() -> float:
    return (datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)).timestamp()


def fetch_rss_signals() -> list[dict]:
    signals = []
    cutoff = _cutoff_ts()
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                pub = entry.get("published_parsed")
                if pub:
                    ts = time.mktime(pub)
                    if ts < cutoff:
                        continue
                uid = entry.get("link", entry.get("id", ""))
                signals.append({
                    "id": uid,
                    "source": source,
                    "title": entry.get("title", ""),
                    "summary": BeautifulSoup(
                        entry.get("summary", ""), "html.parser"
                    ).get_text()[:400],
                    "url": entry.get("link", ""),
                })
        except Exception as e:
            logger.warning("RSS %s failed: %s", source, e)
    return signals


def fetch_reddit_signals() -> list[dict]:
    signals = []
    cutoff = _cutoff_ts()
    for sub in REDDIT_SUBS:
        try:
            r = requests.get(
                f"https://old.reddit.com/r/{sub}/top.json?t=day&limit=25",
                headers={"User-Agent": UA, "Accept": "application/json"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            for child in r.json().get("data", {}).get("children", []):
                d = child["data"]
                if d.get("created_utc", 0) < cutoff:
                    continue
                signals.append({
                    "id": f"reddit_{d['id']}",
                    "source": f"r/{sub}",
                    "title": d.get("title", ""),
                    "summary": (d.get("selftext") or "")[:400],
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                    "score": d.get("score", 0),
                })
        except Exception as e:
            logger.warning("Reddit r/%s failed: %s", sub, e)
    return signals


def fetch_hn_signals() -> list[dict]:
    """Fetch Show HN and Launch posts from Hacker News."""
    signals = []
    cutoff = _cutoff_ts()
    try:
        r = requests.get(f"{HN_API}/newstories.json", timeout=15)
        story_ids = r.json()[:200]

        def _get_story(sid):
            try:
                s = requests.get(f"{HN_API}/item/{sid}.json", timeout=8).json()
                if not s or s.get("type") != "story":
                    return None
                if s.get("time", 0) < cutoff:
                    return None
                title = s.get("title", "")
                if not any(kw in title.lower() for kw in ["show hn", "launch", "i built", "ask hn: who"]):
                    return None
                return {
                    "id": f"hn_{sid}",
                    "source": "Hacker News",
                    "title": title,
                    "summary": s.get("text", "")[:400],
                    "url": s.get("url") or f"https://news.ycombinator.com/item?id={sid}",
                    "score": s.get("score", 0),
                }
            except Exception:
                return None

        # Fetch in parallel via threads
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(_get_story, story_ids[:150]))
        signals = [s for s in results if s]
    except Exception as e:
        logger.warning("HN fetch failed: %s", e)
    return signals


def fetch_producthunt_signals() -> list[dict]:
    signals = []
    try:
        feed = feedparser.parse(PH_RSS)
        for entry in feed.entries[:30]:
            signals.append({
                "id": entry.get("link", entry.get("id", "")),
                "source": "Product Hunt",
                "title": entry.get("title", ""),
                "summary": BeautifulSoup(
                    entry.get("summary", ""), "html.parser"
                ).get_text()[:400],
                "url": entry.get("link", ""),
            })
    except Exception as e:
        logger.warning("ProductHunt RSS failed: %s", e)
    return signals


# ── AI Analysis ────────────────────────────────────────────────────────────────

def _ai_generate_digest(signals: list[dict]) -> list[dict]:
    """
    Feed all signals to LLM. Ask it to pick 5-7 best opportunities
    and generate full briefs as structured JSON.
    """
    verticals_str = ", ".join(VERTICALS)

    signals_text = "\n\n".join(
        f"[{i+1}] SOURCE: {s['source']}\nTITLE: {s['title']}\nSUMMARY: {s['summary']}\nURL: {s['url']}"
        for i, s in enumerate(signals[:80])
    )

    prompt = f"""You are a startup scout for Bernard, a solo developer who builds apps via vibe-coding (AI-assisted coding).

TARGET VERTICALS: {verticals_str}

CRITERIA for a great opportunity:
- Disrupts a traditional industry using AI
- Can be built solo in 1 day to 1 month (webapp, app, or website)
- Has real monetization potential (SaaS, freemium, marketplace)
- AI is core to the value (not just bolted on)
- There is funding/traction signal or clear market gap

TODAY'S SIGNALS:
{signals_text}

TASK:
1. Identify 5-7 of the best app opportunities from these signals (or inspired by them + your knowledge)
2. For each, generate a complete brief in this EXACT JSON format

Return a JSON array. No other text. Example structure:
[
  {{
    "name": "App Name",
    "vertical": "Legal",
    "problem": "One sentence: what sucks today and who suffers.",
    "why_now": "Trend, regulation change, tech unlock, or market gap driving this moment.",
    "ai_angle": "Specifically what AI does — the actual mechanic, not just 'AI-powered'.",
    "complexity": "Weekend / 1 week / 1 month",
    "stack_hint": "e.g. Next.js + Claude API + Stripe",
    "monetization": "e.g. SaaS $29/mo, freemium, marketplace 15% cut",
    "apps_already_built": [
      {{"name": "Competitor", "stage": "Seed", "raised": "$500K", "weakness": "No mobile app"}}
    ],
    "funding_landscape": "Brief note on angel/seed activity in this niche.",
    "competitor_analysis": "Top 2-3 players, their moat, their blind spots, your wedge.",
    "viral_element": "How does the product spread organically?",
    "scale_score": 7,
    "scale_reasoning": "TAM, switching cost, distribution path.",
    "monetization_ceiling": "Can this be $1M ARR solo? Or needs a team?",
    "risk": "Main reason this fails.",
    "signal_source": "Where this idea came from."
  }}
]"""

    try:
        raw = chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=6000,
        )
        # Extract JSON array
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            logger.error("No JSON array in AI response")
            return []
        return json.loads(match.group())
    except Exception as e:
        logger.error("AI generation failed: %s", e)
        return []


# ── Dragons' Den forcing questions ────────────────────────────────────────────

FORCING_QUESTIONS_PROMPT = (
    "You are a YC partner at a Dragons' Den style pitch evaluation. "
    "A startup just pitched this idea:\n\n"
    "{idea_brief}\n\n"
    "Answer these 6 forcing questions ruthlessly honestly. Be specific, not generic:\n\n"
    "1. DEMAND REALITY: Is there evidence of real demand (not interest, not waitlists)? "
    "Who is desperately paying for a worse solution right now?\n"
    "2. STATUS QUO: What cobbled-together workaround are people using today? "
    'Is "doing nothing" the real competitor?\n'
    "3. DESPERATE SPECIFICITY: Name one specific type of person/company who would "
    "panic if this product disappeared tomorrow.\n"
    "4. NARROWEST WEDGE: What's the smallest MVP that solves the most painful part?\n"
    "5. OBSERVATION: What real-world behavior (not assumption) suggests this is worth building?\n"
    "6. FUTURE-FIT: Where does this go in 3 years? Is it a feature, a product, or a platform?\n\n"
    "For each question, rate confidence 1-5 (5 = strong evidence, 1 = pure speculation).\n"
    "End with: OVERALL PMF SCORE: X/10"
)


DRAGONS_DEN_JUDGE_PROMPT = (
    "You received evaluations from {n} different AI models acting as YC partners, "
    "all evaluating the same startup pitch. Each answered 6 forcing questions and gave a PMF score.\n\n"
    "Synthesize their evaluations into a structured Dragons' Den brief. "
    "Be specific and actionable.\n\n"
    "For each of the 6 questions, synthesize the best insights across all models.\n"
    "Calculate the average PMF score across all models.\n"
    "Highlight where models DISAGREED — this is where the real alpha is.\n"
    "Give a final verdict: Build / Maybe / Skip.\n\n"
    "{responses}\n\n"
    "Return your synthesis in EXACTLY this format (keep the section headers):\n\n"
    "PMF_SCORE: X/10\n\n"
    "DEMAND: [synthesis of Q1 answers across models]\n\n"
    "STATUS_QUO: [synthesis of Q2]\n\n"
    "DESPERATE_USER: [synthesis of Q3]\n\n"
    "NARROWEST_WEDGE: [synthesis of Q4]\n\n"
    "OBSERVATION: [synthesis of Q5]\n\n"
    "THREE_YEAR_VISION: [synthesis of Q6]\n\n"
    "MODEL_DISAGREEMENTS: [where models disagreed — bullet points]\n\n"
    "VERDICT: Build / Maybe / Skip"
)


async def evaluate_with_forcing_questions(idea_brief: str) -> dict | None:
    """Run 6-model cross-check with forcing questions on a single idea brief.

    Returns dict with keys: pmf_score, demand, status_quo, desperate_user,
    narrowest_wedge, observation, three_year_vision, disagreements, verdict,
    raw_synthesis, model_count.
    Or None if cross_check unavailable or all models fail.
    """
    if not HAS_CROSS_CHECK:
        return None

    prompt = FORCING_QUESTIONS_PROMPT.format(idea_brief=idea_brief)

    try:
        result = await cross_check(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            timeout=45,
            judge_prompt=DRAGONS_DEN_JUDGE_PROMPT,
        )
    except Exception as e:
        logger.error("Dragons' Den cross-check failed: %s", e)
        return None

    synthesis = result.get("synthesis", "")
    if not synthesis or "All models failed" in synthesis:
        return None

    # Parse structured fields from synthesis
    def _extract(key: str) -> str:
        pattern = rf"{key}:\s*(.+?)(?=\n[A-Z_]+:|$)"
        match = re.search(pattern, synthesis, re.DOTALL)
        if match:
            return match.group(1).strip()
        return "N/A"

    # Extract PMF score
    pmf_match = re.search(r"PMF_SCORE:\s*(\d+(?:\.\d+)?)/10", synthesis)
    pmf_score = float(pmf_match.group(1)) if pmf_match else 0.0

    # Extract verdict
    verdict_match = re.search(r"VERDICT:\s*(Build|Maybe|Skip)", synthesis, re.IGNORECASE)
    verdict = verdict_match.group(1).title() if verdict_match else "Maybe"

    return {
        "pmf_score": pmf_score,
        "demand": _extract("DEMAND"),
        "status_quo": _extract("STATUS_QUO"),
        "desperate_user": _extract("DESPERATE_USER"),
        "narrowest_wedge": _extract("NARROWEST_WEDGE"),
        "observation": _extract("OBSERVATION"),
        "three_year_vision": _extract("THREE_YEAR_VISION"),
        "disagreements": _extract("MODEL_DISAGREEMENTS"),
        "verdict": verdict,
        "raw_synthesis": synthesis,
        "model_count": len(result.get("responses", {})),
        "individual_responses": result.get("responses", {}),
    }


def _format_dragons_den_brief(idea_name: str, evaluation: dict) -> str:
    """Format a Dragons' Den evaluation as a Telegram HTML message."""
    verdict_map = {"Build": "\u2705", "Maybe": "\u26a0\ufe0f", "Skip": "\u274c"}
    verdict_emoji = verdict_map.get(evaluation["verdict"], "\u2753")

    msg = (
        f"\U0001f3c6 <b>DRAGONS' DEN BRIEF \u2014 {idea_name}</b>\n"
        f"\n"
        f"\U0001f4ca <b>PMF Score: {evaluation['pmf_score']}/10</b> "
        f"(consensus from {evaluation['model_count']} AI models)\n"
        f"\n"
        f"\U0001f525 <b>DEMAND</b>\n"
        f"{evaluation['demand']}\n"
        f"\n"
        f"\u2694\ufe0f <b>STATUS QUO</b>\n"
        f"{evaluation['status_quo']}\n"
        f"\n"
        f"\U0001f3af <b>DESPERATE USER</b>\n"
        f"{evaluation['desperate_user']}\n"
        f"\n"
        f"\U0001f52a <b>NARROWEST WEDGE</b>\n"
        f"{evaluation['narrowest_wedge']}\n"
        f"\n"
        f"\U0001f441 <b>OBSERVATION</b>\n"
        f"{evaluation['observation']}\n"
        f"\n"
        f"\U0001f680 <b>3-YEAR VISION</b>\n"
        f"{evaluation['three_year_vision']}\n"
        f"\n"
        f"\u26a1 <b>MODEL DISAGREEMENTS</b>\n"
        f"{evaluation['disagreements']}\n"
        f"\n"
        f"{verdict_emoji} <b>VERDICT: {evaluation['verdict']}</b>"
    )

    if len(msg) > 4000:
        msg = msg[:3990] + "\n[\u2026]"
    return msg


# ── Telegram formatting ────────────────────────────────────────────────────────

def _format_brief(idx: int, brief: dict, total: int) -> str:
    """Format a single brief as Telegram HTML message."""
    DIV = "\u2501" * 26
    DIV_SM = "\u2501\u2501\u2501 "

    competitors = brief.get("apps_already_built", [])
    if competitors:
        col_w = [16, 8, 8, 20]
        header = "{:<{}} {:<{}} {:<{}} {:<{}}".format(
            "App", col_w[0], "Stage", col_w[1], "Raised", col_w[2], "Weakness", col_w[3]
        )
        sep = "-" * col_w[0] + " " + "-" * col_w[1] + " " + "-" * col_w[2] + " " + "-" * col_w[3]
        rows = [header, sep]
        for c in competitors[:4]:
            rows.append("{:<{}} {:<{}} {:<{}} {:<{}}".format(
                c.get("name", "?")[:col_w[0]], col_w[0],
                c.get("stage", "?")[:col_w[1]], col_w[1],
                c.get("raised", "?")[:col_w[2]], col_w[2],
                c.get("weakness", "?")[:col_w[3]], col_w[3],
            ))
        links = "  ".join(
            f'<a href="{c["url"]}">{c.get("name","?")}</a>'
            for c in competitors[:4] if c.get("url")
        )
        comp_table = "<pre>" + "\n".join(rows) + "</pre>" + (f"\n{links}" if links else "")
    else:
        comp_table = "<i>None found</i>"

    scale_score = brief.get("scale_score", 5)

    msg = (
        f"{DIV}\n"
        f"<b>APP IDEA: {brief.get('name', 'Untitled')} \u2014 {brief.get('vertical', '\u2014')}</b>\n"
        f"{DIV}\n"
        f"\n"
        f"<b>PROBLEM</b>\n"
        f"{brief.get('problem', '\u2014')}\n"
        f"\n"
        f"<b>WHY BUILD NOW</b>\n"
        f"{brief.get('why_now', '\u2014')}\n"
        f"\n"
        f"<b>AI ANGLE</b>\n"
        f"{brief.get('ai_angle', '\u2014')}\n"
        f"\n"
        f"<b>VIBE-BUILDABLE</b>\n"
        f"Complexity: {brief.get('complexity', '?')}\n"
        f"Stack hint: {brief.get('stack_hint', '?')}\n"
        f"\n"
        f"<b>MONETIZATION</b>\n"
        f"{brief.get('monetization', '?')}\n"
        f"\n"
        f"{DIV_SM}MARKET SIGNALS \u2501\u2501\u2501\n"
        f"\n"
        f"<b>APPS ALREADY BUILT</b>\n"
        f"{comp_table}\n"
        f"\n"
        f"<b>FUNDING LANDSCAPE</b>\n"
        f"{brief.get('funding_landscape', '\u2014')}\n"
        f"\n"
        f"<b>COMPETITOR ANALYSIS</b>\n"
        f"{brief.get('competitor_analysis', '\u2014')}\n"
        f"\n"
        f"{DIV_SM}GROWTH &amp; SCALE \u2501\u2501\u2501\n"
        f"\n"
        f"<b>VIRAL ELEMENT</b>\n"
        f"{brief.get('viral_element', '\u2014')}\n"
        f"\n"
        f"<b>LIKELIHOOD TO SCALE</b>\n"
        f"Score: {scale_score}/10\n"
        f"Reasoning: {brief.get('scale_reasoning', '\u2014')}\n"
        f"\n"
        f"<b>MONETIZATION CEILING</b>\n"
        f"{brief.get('monetization_ceiling', '\u2014')}\n"
        f"\n"
        f"<b>RISK</b>\n"
        f"{brief.get('risk', '\u2014')}\n"
        f"\n"
        f"<i>SIGNAL SOURCE: {brief.get('signal_source', '\u2014')}</i>"
    )

    if len(msg) > 4000:
        msg = msg[:3990] + "\n[\u2026]"
    return msg


def _format_header(date_str: str, count: int) -> str:
    return (
        f"<b>Andrea Scout \u2014 Daily App Digest</b>\n"
        f"{date_str}  \u00b7  {count} opportunities\n\n"
        f"AI-native \u00b7 vibe-codeable \u00b7 disrupts traditional industries"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def _send_message(bot: Bot, chat_id: int, text: str) -> bool:
    for attempt in range(1, 4):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                message_thread_id=SCOUT_THREAD_ID,
                parse_mode="HTML",
                read_timeout=30,
                write_timeout=30,
            )
            return True
        except TelegramError as e:
            if attempt < 3:
                await asyncio.sleep(attempt * 3)
            else:
                logger.error("Failed to send after 3 attempts: %s", e)
                return False
    return False


async def main() -> None:
    chat_id = SCOUT_CHANNEL_ID

    logger.info("Fetching signals...")
    all_signals: list[dict] = []
    all_signals += fetch_rss_signals()
    all_signals += fetch_reddit_signals()
    all_signals += fetch_hn_signals()
    all_signals += fetch_producthunt_signals()

    # Deduplicate against seen cache
    seen = _load_seen()
    new_signals = [s for s in all_signals if s["id"] not in seen]
    logger.info("Total signals: %d, new: %d", len(all_signals), len(new_signals))

    if len(new_signals) < 5:
        logger.warning("Too few new signals (%d), including already-seen ones", len(new_signals))
        new_signals = all_signals

    # Generate briefs
    logger.info("Generating AI briefs from %d signals...", len(new_signals))
    briefs = _ai_generate_digest(new_signals)

    if not briefs:
        logger.error("No briefs generated \u2014 aborting")
        return

    logger.info("Generated %d briefs", len(briefs))

    # Mark signals as seen
    for s in new_signals:
        seen.add(s["id"])
    _save_seen(seen)

    # Rank briefs by scale_score and run Dragons' Den on top 3
    dragons_den_results = {}
    if HAS_CROSS_CHECK:
        ranked = sorted(briefs, key=lambda b: b.get("scale_score", 0), reverse=True)
        top5 = ranked[:5]
        logger.info("Running Dragons' Den forcing questions on top %d ideas...", len(top5))

        for brief in top5:
            name = brief.get("name", "Untitled")
            # Build a text summary of the brief for evaluation
            brief_text = (
                f"App: {name}\n"
                f"Vertical: {brief.get('vertical', '?')}\n"
                f"Problem: {brief.get('problem', '?')}\n"
                f"Why now: {brief.get('why_now', '?')}\n"
                f"AI angle: {brief.get('ai_angle', '?')}\n"
                f"Complexity: {brief.get('complexity', '?')}\n"
                f"Monetization: {brief.get('monetization', '?')}\n"
                f"Competitor analysis: {brief.get('competitor_analysis', '?')}\n"
                f"Scale score: {brief.get('scale_score', '?')}/10\n"
                f"Risk: {brief.get('risk', '?')}\n"
                f"Viral element: {brief.get('viral_element', '?')}\n"
                f"Monetization ceiling: {brief.get('monetization_ceiling', '?')}"
            )
            try:
                evaluation = await evaluate_with_forcing_questions(brief_text)
                if evaluation:
                    dragons_den_results[name] = evaluation
                    logger.info(
                        "Dragons' Den done for: %s (PMF: %s/10, Verdict: %s)",
                        name, evaluation["pmf_score"], evaluation["verdict"],
                    )
                else:
                    logger.warning("Dragons' Den returned None for: %s", name)
            except Exception as e:
                logger.error("Dragons' Den failed for %s: %s", name, e)

            await asyncio.sleep(3)  # rate limit pause between ideas

    # Send to Telegram
    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT")

    async with Bot(token=ADMIN_TOKEN) as bot:
        # Header
        await _send_message(bot, chat_id, _format_header(date_str, len(briefs)))
        await asyncio.sleep(0.5)

        # One message per brief
        for i, brief in enumerate(briefs, 1):
            msg = _format_brief(i, brief, len(briefs))
            ok = await _send_message(bot, chat_id, msg)
            if ok:
                logger.info("Sent brief %d/%d: %s", i, len(briefs), brief.get("name", "?"))
            await asyncio.sleep(0.8)

            # Send Dragons' Den brief if available for this idea
            name = brief.get("name", "Untitled")
            if name in dragons_den_results:
                dd_msg = _format_dragons_den_brief(name, dragons_den_results[name])
                dd_ok = await _send_message(bot, chat_id, dd_msg)
                if dd_ok:
                    logger.info("Sent Dragons' Den brief for: %s", name)
                await asyncio.sleep(0.8)

    logger.info(
        "Done. Sent %d briefs + %d Dragons' Den evaluations to chat %s",
        len(briefs), len(dragons_den_results), chat_id,
    )


if __name__ == "__main__":
    asyncio.run(main())


# Save Dragons' Den history with individual model reasoning
def _save_dragons_den_history(date_str, briefs, dragons_den_results):
    """Save full Dragons Den evaluation history including per-model reasoning."""
    import json
    history_file = Path(PROJECT_DIR) / "dragons_den_history.json"
    try:
        existing = json.loads(history_file.read_text()) if history_file.exists() else []
    except Exception:
        existing = []
    
    entry = {
        "date": date_str,
        "ideas_evaluated": len(dragons_den_results),
        "evaluations": {}
    }
    for name, eval_data in dragons_den_results.items():
        entry["evaluations"][name] = {
            "pmf_score": eval_data.get("pmf_score"),
            "verdict": eval_data.get("verdict"),
            "demand": eval_data.get("demand"),
            "status_quo": eval_data.get("status_quo"),
            "desperate_user": eval_data.get("desperate_user"),
            "narrowest_wedge": eval_data.get("narrowest_wedge"),
            "observation": eval_data.get("observation"),
            "three_year_vision": eval_data.get("three_year_vision"),
            "disagreements": eval_data.get("disagreements"),
            "model_count": eval_data.get("model_count"),
            "raw_synthesis": eval_data.get("raw_synthesis"),
        }
    
    existing.append(entry)
    existing = existing[-90:]  # Keep last 90 days
    history_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    logger.info("Saved Dragons Den history: %d evaluations", len(dragons_den_results))
