---
name: study-tool
description: Research a new tool/library and analyze fit for our system
trigger: study tool, research tool, check out, evaluate tool
tags: [research, tools, evaluation, learning]
---

# Study Tool

## Steps

### 1. Fetch and read
Use WebFetch to read the tool's URL (GitHub README, docs page, etc.)
- If WebFetch fails: try Firecrawl MCP, then Tavily, then Exa

### 2. Analyze
Answer these questions:
- **What is it?** — One-line description
- **What does it do?** — Key features/capabilities
- **Is it free?** — Pricing, limits, free tier
- **How is it deployed?** — npm, pip, docker, binary
- **Does it overlap with existing tools?** — Check against our current stack

### 3. Check overlap with our system
Current tools:
- MiniMax M2.5-highspeed (AI/LLM)
- twikit (Twitter API)
- XHS MCP (Xiaohongshu)
- Douyin MCP (Douyin/TikTok)
- MediaCrawler (Bilibili, Weibo, Zhihu, Kuaishou, Tieba)
- Playwright (browser automation, cookie refresh)
- faster-whisper (speech-to-text)
- python-telegram-bot (Telegram)
- Reddit JSON API (no auth)

### 4. Write analysis
Save to `evolution_drafts/<tool-name>.md`:
```markdown
# Tool: <name>
- URL: <url>
- Type: <MCP server / library / CLI / API>
- Free: <yes/no/freemium>
- Overlap: <none / partial with X>

## What it does
<description>

## Why relevant
<how it helps our system>

## Proposed changes
<what we'd change to integrate it>

## Risks
<compatibility, cost, maintenance burden>

## Verdict
<integrate / skip / revisit later>
```

### 5. If verdict is "integrate"
- Create implementation plan
- Estimate effort (lines of code, config changes)
- Identify which bots/features benefit

## Rules
- Always check if free before recommending
- Always check for overlap with existing tools
- Don't just describe — evaluate fit for OUR system specifically
- Save drafts even if verdict is "skip" — prevents re-research later
- Create evolution_drafts/ dir if it doesn't exist
