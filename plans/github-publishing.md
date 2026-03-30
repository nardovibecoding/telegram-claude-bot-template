---
Status: in-progress
Created: 2026-03-26
---

# GitHub Publishing Plan

## Phase 1: claude-curated cleanup ✅
- [x] Remove github-publish from claude-curated
- [x] Update README: 8 skills, battle-tested positioning, origin stories, audience routing
- [x] Add more SEO topics (20 total)
- [x] Privacy scan + push

## Phase 2: Awesome list PRs ✅
- [x] BehiSecc/awesome-claude-skills (7.8K) — PR #162
- [x] ComposioHQ/awesome-claude-skills (47.7K) — PR #488
- [x] travisvn/awesome-claude-skills (9.7K) — PR #412
- [x] VoltAgent/awesome-claude-code-subagents (15K) — PR #143
- [x] Puliczek/awesome-mcp-security (671) — PR #83
- [ ] hesreallyhim/awesome-claude-code (32K) — TODO: resubmit via web form: https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml

## Phase 3: Extract standalone repos ✅
- [x] claude-telegram-bridge — extracted from admin_bot (bot.py + sdk_client.py, ~300 lines total, generic)
- [x] claude-sanitizer — sanitizer.py + README (171 lines, zero deps)
- [x] Both: privacy scan CLEAN → created PRIVATE repos
- [ ] Both: flip to public (after owner reviews)

## Phase 4: Auto-tweet skill
- [x] X API free tier signed up — @nardovibecoding authenticated
- [x] Keys saved in .env (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_CLIENT_ID, X_CLIENT_SECRET)
- [x] Tweepy installed + tested — authenticated as @nardovibecoding
- [ ] Build skill: git log → tweet draft → score → post via Tweepy
- [ ] 5 viral formulas: speed flex, tool drop, honest fail, metric proof, hot take
- [ ] Reply-inviter endings, under 110 chars
- [ ] Cron schedule (8-10 AM EST, Tue-Thu peak)
- [ ] Research at: memory/research_vibecoding_tweets_2026-03-26.md

## Phase 5: Publish github-publish as standalone repo ✅
- [x] No competition found (research confirms: zero direct competitors)
- [x] v2 skill: 14 steps, privacy guard, VHS demos, platform detection, quality scoring
- [x] 5 reference files: readme-playbook, project-types, topics-by-category, description-formulas, vhs-templates
- [x] Privacy scan (sanitized bot1/bot2/bot_base references from readme-playbook.md)
- [x] Created PRIVATE repo: nardovibecoding/github-publish
- [x] Viral README: before/after hook, comparison table, 14-step pipeline, quality scoring, battle-tested section
- [ ] Flip to public (after owner reviews)
- [ ] Submit to awesome lists
- [ ] Add before-after screenshot to assets/

## Phase 6: Convert to plugin format ✅
- [x] Added .claude-plugin/plugin.json manifest (v1.0.0)
- [x] Reorganized into skills/security, skills/maintenance, skills/workflow, skills/discovery
- [x] Install via: /plugin marketplace add nardovibecoding/claude-curated
- [x] Updated README with plugin install + category table + new structure

## Phase 7: README polish ✅ (partial)
- [x] v1.0.0 release tag on claude-curated
- [ ] Demo GIFs for skills that don't have them (deferred — needs VHS recordings)
- [ ] Before/after screenshot for github-publish (deferred — needs manual creation)

## Phase 5.5: Add debate + singlesourceoftruth to claude-curated ✅
- [x] Generalized debate → rd-council (added configurable model roster, quick/full modes)
- [x] Generalized singlesourceoftruth → single-source (env vars instead of hardcoded IPs/users, added 4 common setups)
- [x] Privacy scan: CLEAN (removed YOUR_VPS_PROVIDER reference)
- [x] Updated README: 8 → 10 skills, added origin stories and highlights
- [x] Pushed to claude-curated

## Current repo state (2026-03-26)
PUBLIC:
- claude-curated (10 skills: red-alert, dependency-tracker, tldr-eli5, skill-extractor, claude-md-trim, skill-profile, memory-keeper, skill-guard, rd-council, single-source)
- claude-voice (macOS voice control)
- linkedin-autosloth (Chrome extension)
- nardovibecoding (profile README)
- awesome-claude-skills (fork for PR)
- awesome-claude-skills-1 (fork for PR)
- awesome-claude-skills-2 (fork for PR)
- awesome-claude-code-subagents (fork for PR)
- awesome-mcp-security (fork for PR)

PRIVATE:
- telegram-claude-bot (main system)
- claude-skills (skills mirror Mac↔VPS)
- douyin-mcp-py (Douyin MCP)
- xiaohongshu-mcp-py (XHS MCP)
- github-publish (standalone skill — pending flip to public)
- claude-sanitizer (prompt injection defense — pending flip to public)
- claude-telegram-bridge (TG↔Claude SDK bridge — pending flip to public)

## Built this session
- privacy_guard.py (6-layer scanner) at ~/.claude/skills/skill-security-auditor/scripts/
- privacy_patterns.json at ~/.claude/
- github-publish v2 (14 steps) at ~/.claude/skills/github-publish/
- rescan_skills.py + skill_rescan_watch.sh (fswatch watcher)
- Sanitizer patches: voice transcripts, documents, photos in bridge.py
- Douyin MCP fix (dashscope API key guard)
- VHS installed (brew install vhs)
- launchd: com.claude.skill-rescan (fswatch watcher)

## Combos saved this session
- feedback_stranger_test.md — before publishing, read as a stranger
- feedback_private_first.md — never go straight to public
- feedback_auto_plan.md — auto-plan before 3+ step tasks
- feedback_platform_check.md — scan for OS-specific APIs before publishing
