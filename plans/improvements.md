# Improvements Backlog

Collected "Could be even better" items across sessions. Check off as implemented.

## In Progress (agents running)
- [ ] Memory.py: contradiction detection, static/dynamic split, query expansion, task-aware retrieval
- [ ] Weekly usage report to TG thread 152
- [ ] Pin menu button via set_chat_menu_button
- [ ] Persona bot layered menu (bot1, bot2)
- [ ] Anti-sycophancy gate for evolution review
- [ ] Build skill auto-detect complexity

## Pending
- [ ] MiniMax cost logging in bot_base.py — cost_tracker.py ready but not wired
- [ ] Pre-deploy grep check for `chat_id=ADMIN_USER_ID` in scheduler functions
- [ ] `warn-settings-overwrite` hookify rule (needs conditions syntax)
- [ ] `warn-api-key-in-code` hookify rule (catch sk- patterns in source)
- [ ] Weekly top 10 commands in usage report → spot unused commands for /skillcleaning
- [ ] Add contradiction detection ideas from Supermemory to memory-maintenance skill docs

## Done (2026-03-20)
- [x] Security audit: Mac + VPS + Claude Code + TG bots (11 agents)
- [x] User whitelist + rate limiting + cost cap on persona bots
- [x] Sanitizer wired into all 6 content pipelines
- [x] SSRF protection + HTML escape fixes
- [x] API key moved from .zshrc to secure file
- [x] VPS sudo restricted to specific commands
- [x] MCP versions pinned, servers bound to localhost
- [x] File permissions fixed (Mac + VPS)
- [x] Fail2ban 24h, clipboard server localhost, runaway process cron
- [x] 10 hookify rules (5 block + 5 warn)
- [x] Skills: content-humanizer, autoresearch-agent, mcp-server-builder, skill-security-auditor
- [x] Skills: docx, pdf, pptx, xlsx (office suite)
- [x] Skills: extractskill, skillcleaning, build, singlesourceoftruth
- [x] Memory-maintenance upgraded with auto-promotion pipeline
- [x] Extracted patterns from 6 community skills + Supermemory research
- [x] Skills repo on GitHub (nardovibecoding/claude-skills) synced Mac + VPS
- [x] Admin bot: /menu layered inline menu, /skills, /config, /trends, /usage, /export, /unsent
- [x] Usage tracking + auto-resort command menu every 6h
- [x] Functional review: 7 bugs found and fixed
- [x] Gmail env bug fixed, dead bot.py removed, devasini persona deleted
- [x] SDK skills support enabled for TG Claude
- [x] "Could be even better" rule in CLAUDE.md + hookify
- [x] "Send to topic not DM" rule in CLAUDE.md + feedback memory
- [ ] Create /voiceover skill: video → transcribe → diarize → rewrite → voice clone → splice

## Skill Library (crawl + catalog)
- [ ] Build skill_feed.py — daily crawl of skill sources (GitHub trending, OpenClaw, awesome-claude-skills, npm/PyPI)
- [ ] skill_library.json — database of discovered skills with fields:
  - name, source_url, description, category (memory/crawl/evolution/security/office/dev)
  - platform (claude/openclaw/both), overlap_pct with our system
  - status (discovered/evaluated/installed/extracted/skipped)
  - discovered_date, evaluated_date
- [ ] Categories: memory, crawl, evolution, security, office, dev-tools, content, automation
- [ ] /library TG command — browse skill library by category
- [ ] Wire into evolution_feed.py — when crawling, also check for skills
- [ ] Evaluation: auto-run /extractskill on new discoveries
- [ ] Dedup: don't re-evaluate skills already in the library
