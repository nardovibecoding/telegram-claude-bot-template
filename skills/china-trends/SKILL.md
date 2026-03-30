---
name: china-trends
description: Run or check China trends digest (Weibo/Zhihu/Douyin/Bilibili/36Kr)
trigger: china trends, weibo, zhihu, trending china, hot topics
tags: [china, trends, weibo, zhihu, digest, bot1]
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
- Posts to bot1 thread 2 (Bot1's topic)

## Manual trigger
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~/telegram-claude-bot && source venv/bin/activate && python china_trends.py"
```

## Pipeline
1. Fetch trending data from all 5 platforms
2. Feed to MiniMax M2.5-highspeed for analysis
3. Analysis in bot1 style — Cantonese perspective
4. Post to Telegram thread

## Troubleshooting

### If digest doesn't appear
```bash
# Check logs
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep 'china_trends' /tmp/start_all.log | tail -20"

# Check cron
ssh YOUR_VPS_USER@YOUR_VPS_IP "crontab -l | grep china"

# Check flag file
ssh YOUR_VPS_USER@YOUR_VPS_IP "cat ~/telegram-claude-bot/.last_china_trends_run 2>/dev/null"
```

### If platforms are blocked
- Some Chinese platforms may block VPS IP
- MediaCrawler can be used as fallback for scraping
- `xvfb-run` needed for headless browser on VPS

## Rules
- MiniMax timeout: 45s (tuned down from 120s to avoid long hangs)
- Don't run manually if cron already ran today (check flag file)
- All output goes to /tmp/start_all.log
