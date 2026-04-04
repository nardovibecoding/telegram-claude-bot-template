---
name: china-trends
description: Run or check China trends digest (Weibo/Zhihu/Douyin/Bilibili/36Kr)
trigger: china trends, weibo, zhihu, trending china, hot topics
tags: [china, trends, weibo, zhihu, digest, daliu]
---

# China Trends

## Script
`china_trends.py` — fetches trending topics from Chinese platforms

## Sources
- Weibo hot search
- Zhihu hot questions
- Douyin trending
- Bilibili trending
- 36Kr news

## Scheduled run
- Daily 15:00 HKT (07:00 UTC) via cron
- Posts to daliu thread 2 (大劉's topic)

## Manual trigger
```bash
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && source venv/bin/activate && python china_trends.py"
```

## Pipeline
1. Fetch trending data from all 5 platforms
2. Feed to MiniMax M2.5-highspeed for analysis
3. Analysis in daliu (大劉) style — HK tycoon Cantonese perspective
4. Post to Telegram thread

## Troubleshooting

### If digest doesn't appear
```bash
# Check logs
ssh <user>@<vps-ip> "grep 'china_trends' /tmp/start_all.log | tail -20"

# Check cron
ssh <user>@<vps-ip> "crontab -l | grep china"

# Check flag file
ssh <user>@<vps-ip> "cat ~/telegram-claude-bot/.last_china_trends_run 2>/dev/null"
```

### If platforms are blocked
- Some Chinese platforms may block VPS IP
- MediaCrawler can be used as fallback for scraping
- `xvfb-run` needed for headless browser on VPS

## Rules
- MiniMax timeout: 45s (tuned down from 120s to avoid long hangs)
- Don't run manually if cron already ran today (check flag file)
- All output goes to /tmp/start_all.log
