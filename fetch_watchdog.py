# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""
Fetch Watchdog — pre-digest health probe + auto-recovery + failure tracking.

Runs 30 min before digest time. Tests all sources, tracks failure history,
auto-fixes common issues, alerts Bernard only when human intervention needed.

Can also run standalone: python fetch_watchdog.py [--check] [--fix] [--report]
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiohttp
import requests

logger = logging.getLogger("fetch_watchdog")

PROJECT_DIR = Path(__file__).parent
HISTORY_FILE = PROJECT_DIR / ".fetch_watchdog_history.json"
MAX_HISTORY_DAYS = 14  # keep 2 weeks of history

# Thresholds
RSS_MIN_ARTICLES = 1       # at least 1 article per feed = alive
SCRAPE_MIN_ARTICLES = 1
CRITICAL_FAIL_PCT = 50     # alert if >50% sources fail
WARNING_FAIL_PCT = 25      # warn if >25% sources fail

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}


@dataclass
class ProbeResult:
    name: str
    category: str  # rss, scrape, crypto_rss, reddit, twitter
    ok: bool
    article_count: int = 0
    error: str = ""
    latency_ms: int = 0


# ── Source definitions (imported from actual modules) ──────────────────

def _get_news_sources() -> dict:
    """Get RSS + scrape + JSON API sources from news.py."""
    try:
        from news import RSS_FEEDS, SCRAPE_TARGETS, JSON_API_TARGETS
        return {
            "rss": RSS_FEEDS,
            "scrape": SCRAPE_TARGETS,
            "json_api": JSON_API_TARGETS,
        }
    except ImportError:
        return {"rss": {}, "scrape": {}, "json_api": {}}


def _get_crypto_sources() -> dict:
    """Get crypto RSS sources from crypto_news.py."""
    try:
        from crypto_news import MAIN_FEEDS, DEALS_FEEDS, PROTOS_FEED
        feeds = dict(MAIN_FEEDS)
        feeds.update(DEALS_FEEDS)
        feeds["Protos"] = PROTOS_FEED
        return feeds
    except ImportError:
        return {}


# ── Probe functions ───────────────────────────────────────────────────

async def _probe_rss(session: aiohttp.ClientSession, name: str, url: str) -> ProbeResult:
    """Test a single RSS feed — just check it responds with valid XML."""
    import feedparser
    t0 = time.monotonic()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                ms = int((time.monotonic() - t0) * 1000)
                return ProbeResult(
                    name, "rss", False, 0,
                    f"HTTP {resp.status}", ms,
                )
            text = await resp.text()
        feed = feedparser.parse(text)
        count = len(feed.entries)
        ms = int((time.monotonic() - t0) * 1000)
        ok = count >= RSS_MIN_ARTICLES
        return ProbeResult(
            name, "rss", ok, count,
            "" if ok else "0 articles", ms,
        )
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(name, "rss", False, 0, str(e)[:120], ms)


async def _probe_scrape(session: aiohttp.ClientSession, name: str, cfg: dict) -> ProbeResult:
    """Test a scrape target — check it returns at least 1 headline."""
    from bs4 import BeautifulSoup
    t0 = time.monotonic()
    try:
        async with session.get(cfg["url"], timeout=aiohttp.ClientTimeout(total=12), headers=HEADERS) as resp:
            html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        count = len(soup.select(cfg["selector"]))
        ms = int((time.monotonic() - t0) * 1000)
        ok = count >= SCRAPE_MIN_ARTICLES
        return ProbeResult(name, "scrape", ok, count, "" if ok else "selector matched 0", ms)
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(name, "scrape", False, 0, str(e)[:120], ms)


async def _probe_json_api(session: aiohttp.ClientSession, name: str, cfg: dict) -> ProbeResult:
    """Test a JSON API endpoint — check it returns valid data."""
    t0 = time.monotonic()
    try:
        async with session.get(cfg["url"], timeout=aiohttp.ClientTimeout(total=12), headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
        # Count items (HK01 nests under items/data)
        items = data
        if isinstance(items, dict):
            items = items.get("items", items)
        if isinstance(items, dict):
            items = items.get("data", items)
        if isinstance(items, dict):
            items = items.get("items", [])
        count = len(items) if isinstance(items, list) else 0
        ms = int((time.monotonic() - t0) * 1000)
        ok = count >= 1
        return ProbeResult(name, "json_api", ok, count, "" if ok else "0 items", ms)
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(name, "json_api", False, 0, str(e)[:120], ms)


async def _probe_reddit() -> list[ProbeResult]:
    """Test all Reddit fetch paths: CF Worker proxy + direct old.reddit.com."""
    import random
    results = []
    browser_ua = random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ])

    # Probe 1: CF Worker proxy
    t0 = time.monotonic()
    proxy_url = os.environ.get("REDDIT_PROXY_URL", "https://reddit-proxy.okaybernard-6fe.workers.dev")
    try:
        r = requests.get(
            f"{proxy_url}/?sub=personalfinance&sort=top&t=day&limit=5",
            headers={"User-Agent": browser_ua, "Accept": "application/json"},
            timeout=15,
        )
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            posts = r.json().get("data", {}).get("children", [])
            count = len(posts)
            results.append(ProbeResult("Reddit Proxy", "reddit", count > 0, count, "" if count > 0 else "0 posts", ms))
        else:
            results.append(ProbeResult("Reddit Proxy", "reddit", False, 0, f"HTTP {r.status_code}", ms))
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        results.append(ProbeResult("Reddit Proxy", "reddit", False, 0, str(e)[:120], ms))

    # Probe 2: Direct old.reddit.com
    t0 = time.monotonic()
    try:
        r = requests.get(
            "https://old.reddit.com/r/personalfinance/top.json",
            params={"t": "day", "limit": 5},
            headers={"User-Agent": browser_ua, "Accept": "application/json"},
            timeout=15,
        )
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            posts = r.json().get("data", {}).get("children", [])
            count = len(posts)
            results.append(ProbeResult("Reddit Direct", "reddit", count > 0, count, "" if count > 0 else "0 posts", ms))
        else:
            results.append(ProbeResult("Reddit Direct", "reddit", False, 0, f"HTTP {r.status_code}", ms))
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        results.append(ProbeResult("Reddit Direct", "reddit", False, 0, str(e)[:120], ms))

    return results


async def _probe_twitter_cookies() -> ProbeResult:
    """Check if Twitter cookies are valid and fresh enough."""
    t0 = time.monotonic()
    cookie_file = PROJECT_DIR / "twitter_cookies.json"
    try:
        if not cookie_file.exists():
            return ProbeResult("Twitter Cookies", "twitter", False, 0, "file missing", 0)
        cookies = await asyncio.to_thread(json.loads, cookie_file.read_bytes())
        has_auth = isinstance(cookies, dict) and "auth_token" in cookies and "ct0" in cookies
        age_hours = (time.time() - cookie_file.stat().st_mtime) / 3600
        ms = int((time.monotonic() - t0) * 1000)
        if not has_auth:
            return ProbeResult("Twitter Cookies", "twitter", False, 0, "missing auth_token/ct0", ms)
        if age_hours > 36:
            return ProbeResult("Twitter Cookies", "twitter", False, 0, f"stale ({int(age_hours)}h old)", ms)
        return ProbeResult("Twitter Cookies", "twitter", True, 1, "", ms)
    except Exception as e:
        return ProbeResult("Twitter Cookies", "twitter", False, 0, str(e)[:120], 0)


async def _probe_minimax() -> ProbeResult:
    """Quick health check on MiniMax API."""
    t0 = time.monotonic()
    try:
        api_key = os.environ.get("MINIMAX_API_KEY", "")
        if not api_key:
            return ProbeResult("MiniMax API", "api", False, 0, "no API key", 0)
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.minimaxi.com/v1")
        resp = client.chat.completions.create(
            model="MiniMax-M2.5-highspeed",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult("MiniMax API", "api", True, 1, "", ms)
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        err = str(e)[:120]
        if "insufficient_balance" in err.lower():
            err = "INSUFFICIENT BALANCE"
        return ProbeResult("MiniMax API", "api", False, 0, err, ms)


# ── Run all probes ────────────────────────────────────────────────────

async def run_all_probes() -> list[ProbeResult]:
    """Run all fetch probes in parallel. Returns list of ProbeResults."""
    results: list[ProbeResult] = []
    sources = _get_news_sources()
    crypto_feeds = _get_crypto_sources()

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        tasks = []

        # News RSS feeds
        for name, url in sources.get("rss", {}).items():
            tasks.append(_probe_rss(session, name, url))

        # News scrape targets
        for name, cfg in sources.get("scrape", {}).items():
            tasks.append(_probe_scrape(session, name, cfg))

        # JSON API targets
        for name, cfg in sources.get("json_api", {}).items():
            tasks.append(_probe_json_api(session, name, cfg))

        # Crypto RSS feeds
        for name, url in crypto_feeds.items():
            tasks.append(_probe_rss(session, f"Crypto:{name}", url))

        # Gather all HTTP probes
        if tasks:
            results.extend(await asyncio.gather(*tasks, return_exceptions=False))

    # Non-HTTP probes (run sequentially to avoid issues)
    results.extend(await _probe_reddit())
    results.append(await _probe_twitter_cookies())
    results.append(await _probe_minimax())
    results.extend(await _probe_bot_processes())
    results.extend(await _probe_cron_jobs())

    return results


# ── History tracking ──────────────────────────────────────────────────

def _load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return {"runs": []}


def _save_history(history: dict) -> None:
    # Prune old entries
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_HISTORY_DAYS)).isoformat()
    history["runs"] = [r for r in history["runs"] if r.get("ts", "") > cutoff]
    # Atomic write
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", dir=PROJECT_DIR, suffix=".tmp", delete=False)
    try:
        json.dump(history, tmp, indent=2)
        tmp.close()
        os.replace(tmp.name, HISTORY_FILE)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def record_run(results: list[ProbeResult]) -> dict:
    """Record probe results and return analysis with trends."""
    history = _load_history()

    run = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "ok": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "failures": {r.name: r.error for r in results if not r.ok},
    }
    history["runs"].append(run)
    _save_history(history)

    # Analyze trends: sources that failed 3+ times in last 7 days
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_runs = [r for r in history["runs"] if r.get("ts", "") > week_ago]
    fail_counts: dict[str, int] = {}
    for r in recent_runs:
        for name in r.get("failures", {}):
            fail_counts[name] = fail_counts.get(name, 0) + 1

    persistent_failures = {k: v for k, v in fail_counts.items() if v >= 3}

    return {
        "run": run,
        "persistent_failures": persistent_failures,
        "total_runs_this_week": len(recent_runs),
    }


# ── Auto-fix actions ─────────────────────────────────────────────────

async def auto_fix(results: list[ProbeResult]) -> list[str]:
    """Attempt to auto-fix common issues. Returns list of actions taken."""
    actions = []

    for r in results:
        if r.ok:
            continue

        # Twitter cookies stale → flag for Mac refresh
        if r.name == "Twitter Cookies" and "stale" in r.error:
            flag = PROJECT_DIR / ".cookies_need_refresh"
            if not flag.exists():
                flag.write_text("stale")
                actions.append("🔧 Flagged Twitter cookies for Mac auto-refresh")

        # Reddit cache might be stale → clear it if ALL reddit probes failed
        if r.name in ("Reddit Proxy", "Reddit Direct") and r.error:
            cache = PROJECT_DIR / ".reddit_cache.json"
            if cache.exists():
                cache.unlink()
                actions.append("🔧 Cleared stale Reddit cache")

        # Stale prefetch cache → clear it
        if r.category == "twitter":
            prefetch = PROJECT_DIR / ".xcurate_prefetch.json"
            if prefetch.exists():
                try:
                    age = time.time() - prefetch.stat().st_mtime
                    if age > 3600:  # older than 1h
                        prefetch.unlink()
                        actions.append("🔧 Cleared stale X prefetch cache")
                except OSError:
                    pass

        # Bot process dead -> restart systemd service
        if r.category == "process" and "not running" in r.error:
            import subprocess
            subprocess.run(["sudo", "systemctl", "restart", "telegram-bots"], timeout=10)
            actions.append(f"Restarted telegram-bots ({r.name} was dead)")
            break

    return actions


# ── Format report ─────────────────────────────────────────────────────

def format_report(results: list[ProbeResult], analysis: dict, actions: list[str]) -> str:
    """Format a concise Telegram-ready report."""
    ok = sum(1 for r in results if r.ok)
    total = len(results)
    fail_pct = (total - ok) / total * 100 if total else 0

    # Header with severity
    if fail_pct >= CRITICAL_FAIL_PCT:
        header = f"🔴 <b>Fetch Watchdog — CRITICAL</b>"
    elif fail_pct >= WARNING_FAIL_PCT:
        header = f"🟡 <b>Fetch Watchdog — WARNING</b>"
    else:
        header = f"🟢 <b>Fetch Watchdog — OK</b>"

    hkt = datetime.now(timezone(timedelta(hours=8))).strftime("%H:%M HKT")
    lines = [f"{header}  ({hkt})", f"Sources: {ok}/{total} OK ({100-fail_pct:.0f}%)", ""]

    # Failed sources grouped by category
    failures = [r for r in results if not r.ok]
    if failures:
        lines.append("<b>Failed:</b>")
        by_cat: dict[str, list[ProbeResult]] = {}
        for r in failures:
            by_cat.setdefault(r.category, []).append(r)
        for cat, probes in sorted(by_cat.items()):
            cat_label = {"rss": "📡 RSS", "scrape": "🕸 Scrape", "json_api": "🔗 API",
                         "reddit": "🟧 Reddit", "twitter": "🐦 Twitter",
                         "api": "🤖 AI API", "crypto_rss": "₿ Crypto"}.get(cat, cat)
            for p in probes:
                lines.append(f"  {cat_label}: {p.name} — {p.error}")
        lines.append("")

    # Persistent failures (trending down)
    persistent = analysis.get("persistent_failures", {})
    if persistent:
        lines.append("<b>⚠️ Persistent (3+ fails this week):</b>")
        for name, count in sorted(persistent.items(), key=lambda x: -x[1]):
            lines.append(f"  {name}: {count}x in {analysis['total_runs_this_week']} runs")
        lines.append("")

    # Auto-fix actions
    if actions:
        lines.append("<b>Auto-fixes applied:</b>")
        for a in actions:
            lines.append(f"  {a}")
        lines.append("")

    # Slow sources (>5s latency)
    slow = [r for r in results if r.latency_ms > 5000 and r.ok]
    if slow:
        lines.append("<b>Slow (>5s):</b>")
        for r in sorted(slow, key=lambda x: -x.latency_ms):
            lines.append(f"  {r.name}: {r.latency_ms}ms")

    return "\n".join(lines)


# ── Scheduler integration ─────────────────────────────────────────────

async def watchdog_job(context) -> None:
    """Run as a scheduled job from admin_bot or bot_base."""
    from admin_bot.config import ADMIN_USER_ID, PERSONAL_GROUP, _HEARTBEAT_THREAD

    logger.info("Fetch watchdog starting probe run...")
    results = await run_all_probes()
    actions = await auto_fix(results)
    analysis = record_run(results)

    ok = sum(1 for r in results if r.ok)
    total = len(results)
    fail_pct = (total - ok) / total * 100 if total else 0

    logger.info("Fetch watchdog: %d/%d OK (%.0f%% fail rate)", ok, total, fail_pct)

    # Only alert if there are failures worth reporting
    if fail_pct >= WARNING_FAIL_PCT or analysis.get("persistent_failures"):
        report = format_report(results, analysis, actions)
        try:
            await context.bot.send_message(
                chat_id=PERSONAL_GROUP,
                message_thread_id=_HEARTBEAT_THREAD,
                text=report,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Watchdog alert failed: %s", e)
            # Fallback: DM admin
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=report,
                    parse_mode="HTML",
                )
            except Exception:
                pass
    elif actions:
        # Log auto-fixes even if not alerting
        logger.info("Watchdog auto-fixes: %s", "; ".join(actions))


# ── Post-digest validation ────────────────────────────────────────────

async def validate_digest_output(messages: list, digest_type: str = "news") -> dict:
    """Validate digest output quality. Returns {ok, issues}."""
    issues = []

    if not messages:
        issues.append(f"{digest_type} digest returned 0 messages")
        return {"ok": False, "issues": issues}

    # Check for suspiciously short digest
    total_chars = sum(len(m) if isinstance(m, str) else len(m.get("text", "")) for m in messages)
    if total_chars < 200:
        issues.append(f"Digest too short ({total_chars} chars)")

    # Check for empty sections (strings that are mostly whitespace)
    empty_count = sum(1 for m in messages if isinstance(m, str) and len(m.strip()) < 20)
    if empty_count > len(messages) * 0.3:
        issues.append(f"{empty_count}/{len(messages)} sections near-empty")

    return {"ok": len(issues) == 0, "issues": issues}


# ── CLI interface ─────────────────────────────────────────────────────

async def _cli_main():
    """Run watchdog from command line."""
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(message)s",
        level=logging.INFO,
    )

    print("🔍 Running fetch watchdog probes...\n")
    results = await run_all_probes()
    actions = await auto_fix(results)
    analysis = record_run(results)

    # Print results table
    ok_count = sum(1 for r in results if r.ok)
    print(f"Results: {ok_count}/{len(results)} OK\n")

    # Group by category
    by_cat: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    for cat, probes in sorted(by_cat.items()):
        print(f"── {cat.upper()} ──")
        for p in sorted(probes, key=lambda x: x.ok):
            status = "✅" if p.ok else "❌"
            extra = f" ({p.article_count} articles, {p.latency_ms}ms)" if p.ok else f" — {p.error}"
            print(f"  {status} {p.name}{extra}")
        print()

    if actions:
        print("Auto-fixes:")
        for a in actions:
            print(f"  {a}")
        print()

    persistent = analysis.get("persistent_failures", {})
    if persistent:
        print("⚠️  Persistent failures (3+ this week):")
        for name, count in sorted(persistent.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}x")
        print()

    # Exit code: 0 if OK, 1 if warning, 2 if critical
    fail_pct = (len(results) - ok_count) / len(results) * 100 if results else 0
    if fail_pct >= CRITICAL_FAIL_PCT:
        sys.exit(2)
    elif fail_pct >= WARNING_FAIL_PCT:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(_cli_main())


async def _probe_bot_processes() -> list[ProbeResult]:
    """Check all bot processes + systemd service are alive."""
    import subprocess
    results = []

    # Check systemd service
    try:
        r = subprocess.run(['systemctl', 'is-active', 'telegram-bots'],
                          capture_output=True, text=True, timeout=5)
        ok = r.stdout.strip() == 'active'
        results.append(ProbeResult('systemd service', 'process', ok, 1 if ok else 0,
                                   '' if ok else r.stdout.strip(), 0))
    except Exception as e:
        results.append(ProbeResult('systemd service', 'process', False, 0, str(e)[:80], 0))

    # Check individual bot processes
    for name, pattern in [('admin_bot', 'admin_bot'), ('daliu', 'run_bot.py daliu'), ('sbf', 'run_bot.py sbf')]:
        try:
            r = subprocess.run(['pgrep', '-f', pattern], capture_output=True, timeout=5)
            ok = r.returncode == 0
            results.append(ProbeResult(f'Bot: {name}', 'process', ok, 1 if ok else 0,
                                       '' if ok else 'not running', 0))
        except Exception as e:
            results.append(ProbeResult(f'Bot: {name}', 'process', False, 0, str(e)[:80], 0))

    return results


async def _probe_cron_jobs() -> list[ProbeResult]:
    """Check if all daily cron jobs ran today by checking their log files."""
    from datetime import datetime, timezone, timedelta
    HKT = timezone(timedelta(hours=8))
    today = datetime.now(HKT).strftime("%Y-%m-%d")
    hour = datetime.now(HKT).hour

    # Jobs with expected run time (HKT) and log file
    jobs = [
        ("X Digest", 11, "/tmp/xdigest.log"),
        ("Daliu News", 12, "/tmp/digest_daliu.log"),
        ("SBF Crypto", 13, "/tmp/digest_sbf.log"),
        ("Reddit Digest", 14, "/tmp/reddit_digest.log"),
        ("Evolution Digest", 12, "/tmp/ai_digest.log"),
        ("China Trends", 14, "/tmp/china_trends.log"),
        ("Code Review", 18, "/tmp/code_review.log"),
        ("YouTube Digest", 15, "/tmp/youtube_digest.log"),
        ("Podcast EN", 15, "/tmp/podcast_en.log"),
        ("Podcast CN", 15, "/tmp/podcast_cn.log"),
        ("Gmail Check", 19, "/tmp/gmail_check.log"),
    ]

    results = []
    for name, expected_hour, log_path in jobs:
        # Only check if past expected time
        if hour < expected_hour:
            continue
        try:
            import subprocess
            r = subprocess.run(["grep", "-c", today, log_path],
                             capture_output=True, text=True, timeout=5)
            ran_today = int(r.stdout.strip()) > 0 if r.returncode == 0 else False
            results.append(ProbeResult(
                f"Cron: {name}", "cron", ran_today, 1 if ran_today else 0,
                "" if ran_today else f"not run today (expected {expected_hour}:00 HKT)",
                0
            ))
        except Exception as e:
            results.append(ProbeResult(f"Cron: {name}", "cron", False, 0, str(e)[:80], 0))

    # Gmail token health — detect expired OAuth
    try:
        r = subprocess.run(
            ["tail", "-5", "/tmp/gmail_check.log"],
            capture_output=True, text=True, timeout=5)
        if "RefreshError" in r.stdout or "invalid_grant" in r.stdout:
            results.append(ProbeResult(
                "Gmail OAuth Token", "cron", False, 0,
                "TOKEN EXPIRED — reauth on Mac then scp to VPS", 0))
    except Exception:
        pass

    # Public repo staleness check
    try:
        sync_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "scripts", "sync_public_repos.py")
        if os.path.exists(sync_script):
            r = subprocess.run(
                [sys.executable, sync_script],
                capture_output=True, text=True, timeout=15)
            stale_count = r.stdout.count("STALE") + r.stdout.count("MISSING")
            if stale_count > 0:
                results.append(ProbeResult(
                    "Public Repos", "cron", False, 0,
                    f"{stale_count} files stale — run sync_public_repos.py --sync",
                    0))
    except Exception:
        pass

    return results
