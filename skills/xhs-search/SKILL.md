---
name: xhs-search
description: Search Xiaohongshu (XHS) using MCP tools
trigger: xhs search, xiaohongshu, search xhs, red book
tags: [xhs, xiaohongshu, mcp, search, social]
---

# XHS (Xiaohongshu) Search

## MCP Server
- Endpoint: `localhost:18060` on VPS
- ALWAYS use MCP tools directly — never curl/bash workarounds

## Steps

### 1. Search for content
Use MCP tool: `search_feeds`
- Pass search keywords
- Returns list with metrics (likes, favorites, shares)
- This is ONE call — efficient, don't over-fetch

### 2. Sort by engagement
Sort results by total engagement (likes + favorites + shares + comments)

### 3. Deep analysis (selective)
Only call `get_feed_detail` for posts that need full content or comment analysis.
- Don't call it for every result — wasteful
- Use for top 3-5 most relevant results

### 4. Format as table
```
| # | Title | Likes | Favs | Shares | Author |
|---|-------|-------|------|--------|--------|
| 1 | ...   | 1.2K  | 890  | 234    | @user  |
```

## Available MCP tools
- `search_feeds` — search by keywords, returns metrics
- `get_feed_detail` — full post content + comments (use selectively)
- `user_profile` — get user info
- `list_feeds` — browse feeds
- `like_feed` — like a post
- `favorite_feed` — favorite/bookmark a post
- `post_comment_to_feed` — comment on a post
- `publish_content` — publish new content
- `check_login_status` — verify auth
- `get_login_qrcode` — get QR for login

## Known limitations
- XHS search is BLOCKED on VPS IP (anti-bot)
- Search only works from Mac IP
- If MustWaitStable panic appears: it's IP blocking, not a code bug
- Login: `/xhslogin` (QR on TG), `/xhscheck` (verify)

## Rules
- Use MCP tools, not curl workarounds
- search_feeds for discovery (one call), get_feed_detail only for deep dives
- If search fails on VPS, it's likely IP blocking — say so immediately
