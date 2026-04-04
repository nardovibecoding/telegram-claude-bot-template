# Copyright (c) 2026 Nardo. AGPL-3.0 — see LICENSE
"""Camofox browser REST client — wraps the camofox-browser server on localhost:19377.

Provides:
  - scrape_page(url)               → plain text content (JS-rendered)
  - get_reddit_posts(sub, limit)   → list of post dicts (via JSON endpoint)
  - health()                       → True if server is up

Tab lifecycle is fully managed internally (create → use → delete).
All calls are synchronous (requests). Designed for VPS usage only.
"""
import json
import logging
import re
import time
import requests

log = logging.getLogger(__name__)

BASE_URL = "http://localhost:19377"
_DEFAULT_TIMEOUT = 30  # seconds per request
_NAV_TIMEOUT = 35      # navigate can be slower


def health() -> bool:
    """Return True if camofox server is reachable."""
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        return r.status_code == 200 and r.json().get("ok") is True
    except Exception:
        return False


def _create_tab(user_id: str, url: str) -> str | None:
    """Create a new tab and return its tabId, or None on failure."""
    try:
        r = requests.post(
            f"{BASE_URL}/tabs",
            json={"userId": user_id, "sessionKey": "digest", "url": url},
            timeout=_NAV_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("tabId")
        log.warning("camofox create_tab %d: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("camofox create_tab error: %s", e)
    return None


def _get_snapshot(tab_id: str, user_id: str) -> str:
    """Return accessibility snapshot text for the current tab page."""
    try:
        r = requests.get(
            f"{BASE_URL}/tabs/{tab_id}/snapshot",
            params={"userId": user_id},
            timeout=_DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("snapshot", "")
        log.warning("camofox snapshot %d: %s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("camofox snapshot error: %s", e)
    return ""


def _delete_tab(tab_id: str, user_id: str) -> None:
    """Delete tab silently."""
    try:
        requests.delete(
            f"{BASE_URL}/tabs/{tab_id}",
            params={"userId": user_id},
            timeout=5,
        )
    except Exception:
        pass


def scrape_page(url: str, max_chars: int = 3000) -> str:
    """Scrape a URL using camofox (JS-rendered) and return plain text content.

    Returns empty string on any failure.
    """
    uid = f"scraper_{int(time.time())}"
    tab_id = _create_tab(uid, url)
    if not tab_id:
        return ""
    try:
        time.sleep(2)  # brief wait for page render
        snapshot = _get_snapshot(tab_id, uid)
        # Snapshot is accessibility tree — extract readable text
        # Strip role labels like [button e1], [link e2] etc.
        text = re.sub(r"\[[\w\s]+\s+e\d+\]", "", snapshot)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:max_chars]
    finally:
        _delete_tab(tab_id, uid)


def get_reddit_posts(subreddit: str, limit: int = 50, cutoff: float = 0.0) -> list[dict]:
    """Fetch top posts from a subreddit via camofox.

    Uses old.reddit.com JSON endpoint for structured data.
    Returns list of post dicts matching the format in reddit_digest.py.
    Falls back to empty list on any failure.
    """
    url = f"https://old.reddit.com/r/{subreddit}/top.json?t=day&limit={limit}"
    uid = f"reddit_{subreddit}_{int(time.time())}"
    tab_id = _create_tab(uid, url)
    if not tab_id:
        return []
    try:
        time.sleep(3)  # wait for JSON to fully load
        snapshot = _get_snapshot(tab_id, uid)
        if not snapshot:
            return []

        # The JSON endpoint renders as raw text in the browser
        # Extract JSON from snapshot text
        json_match = re.search(r'\{.*"kind"\s*:\s*"Listing".*\}', snapshot, re.DOTALL)
        if not json_match:
            # Try finding any JSON object in the snapshot
            json_match = re.search(r'\{.*"children".*\}', snapshot, re.DOTALL)
        if not json_match:
            log.warning("camofox reddit r/%s: no JSON found in snapshot", subreddit)
            return []

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            log.warning("camofox reddit r/%s: JSON parse failed", subreddit)
            return []

        children = data.get("data", {}).get("children", [])
        posts = _parse_children(children, cutoff, subreddit)
        log.info("camofox r/%s: %d posts", subreddit, len(posts))
        return posts

    except Exception as e:
        log.warning("camofox get_reddit_posts r/%s error: %s", subreddit, e)
        return []
    finally:
        _delete_tab(tab_id, uid)


def _parse_children(children: list, cutoff: float, sub: str) -> list[dict]:
    """Parse Reddit API children into post dicts (mirrors reddit_digest._parse_reddit_children)."""
    posts = []
    for child in children:
        d = child.get("data", {})
        created = d.get("created_utc", 0)
        if cutoff and created < cutoff:
            continue
        selftext = (d.get("selftext") or "")[:300].replace("\n", " ").strip()
        full_selftext = d.get("selftext") or ""
        is_self = d.get("is_self", False)
        link_url = d.get("url", "") if not is_self else ""
        permalink = f"https://reddit.com{d.get('permalink', '')}"
        posts.append({
            "subreddit": d.get("subreddit", sub),
            "title": d.get("title", ""),
            "preview": selftext[:200] if selftext else "",
            "link_url": link_url,
            "permalink": permalink,
            "score": d.get("score", 0),
            "upvote_ratio": d.get("upvote_ratio", 0),
            "num_comments": d.get("num_comments", 0),
            "author": d.get("author", "[deleted]"),
            "created_utc": created,
            "is_self": is_self,
            "flair": d.get("link_flair_text") or "",
            "word_count": len(full_selftext.split()) if full_selftext else 0,
        })
    return posts
