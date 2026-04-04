---
name: cookie-health
description: Check and refresh cookie health for Twitter, XHS, Douyin
trigger: cookie check, cookies stale, cookie refresh, auth expired
tags: [cookies, auth, twitter, xhs, douyin, health]
---

# Cookie Health Check

## Twitter Cookies

### 1. Check age
```bash
ssh <user>@<vps-ip> "stat -c '%Y %n' ~/telegram-claude-bot/twitter_cookies.json && echo 'now:' && date +%s"
```
- Auto-refreshed every 12h via Mac Playwright
- If older than 36 hours: stale

### 2. If stale
```bash
# Signal Mac to refresh
ssh <user>@<vps-ip> "touch ~/telegram-claude-bot/.cookies_need_refresh"
```
- Mac sync script picks this up within 10 minutes
- Mac runs `refresh_cookies.py` via Playwright
- VPS IP is blocked by Cloudflare — NEVER attempt refresh on VPS

### 3. Verify after refresh
```bash
# Wait 10-15 min, then check
ssh <user>@<vps-ip> "ls -la ~/telegram-claude-bot/twitter_cookies.json"
```

## XHS (Xiaohongshu) Cookies

### 1. Check login status
Use MCP tool: `check_login_status`

Or via TG: send `/xhscheck` to admin bot

### 2. If expired
Use MCP tool: `get_login_qrcode`

Or via TG: send `/xhslogin` to admin bot — scan QR code

## Douyin Cookies

### 1. Check status
```bash
ssh <user>@<vps-ip> "curl -s http://localhost:18070/health"
```

### 2. If expired
- Douyin requires manual Chrome cookie export
- Export from browser → update cookie file on VPS
- No automated refresh available

## Cookie Lock Files
- Twitter refresh uses a lock file to prevent concurrent refreshes
- If lock is stuck for >5 minutes:
```bash
ssh <user>@<vps-ip> "rm -f ~/telegram-claude-bot/.cookie_refresh_lock"
```

## Bookmark Sync
- Piggybacks on Twitter cookie refresh job
- Runs automatically after successful cookie refresh
- Syncs bookmarks → auto-classifies (en/zh/ai)
- Needs 3+ bookmarks per category to activate taste boost
