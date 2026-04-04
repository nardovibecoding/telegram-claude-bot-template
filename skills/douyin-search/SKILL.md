---
name: douyin-search
description: Search Douyin content using MCP tools
trigger: douyin search, tiktok china, search douyin
tags: [douyin, mcp, search, video, social]
---

# Douyin Search

## MCP Server
- Endpoint: `localhost:18070` on VPS
- Based on F2 (douyin downloader framework)

## Available MCP tools
- `parse_douyin_video_info` — parse video from URL
- `get_douyin_download_link` — get download URL for a video
- `extract_douyin_text` — extract text/captions from video
- `recognize_audio_url` — transcribe audio from URL
- `recognize_audio_file` — transcribe local audio file

## Steps

### 1. Search for content
Use MCP tool: `search_feeds` (if available with auth)
- Note: search requires auth cookies
- Homepage feed works without auth

### 2. For specific video analysis
If given a Douyin URL:
```
parse_douyin_video_info → get video metadata
extract_douyin_text → get captions/text overlay
recognize_audio_url → transcribe spoken content
```

### 3. Format results
Include: title, author, likes, comments, shares, duration, URL

## Alternative: MediaCrawler
If MCP search is unavailable:
```bash
ssh <user>@<vps-ip> "cd ~/MediaCrawler && source venv/bin/activate && xvfb-run python main.py --platform douyin --type search --keywords 'keyword'"
```

## Known limitations
- Search needs auth cookies (may expire)
- Homepage feed works without auth
- Video download may require valid cookies
- VPS IP may be rate-limited

## Rules
- Try MCP tools first, MediaCrawler as fallback
- Don't scrape aggressively — respect rate limits
- If cookies expired, manual Chrome export is needed (no auto-refresh)
