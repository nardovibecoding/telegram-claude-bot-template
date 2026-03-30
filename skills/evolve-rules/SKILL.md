---
name: evolve-rules
description: Add new rules or lessons learned to CLAUDE.md
trigger: claude.md, add rule, new rule, lesson learned, save lesson
tags: [rules, claude.md, learning, memory]
---

# Evolve Rules (CLAUDE.md)

## File location
`~/telegram-claude-bot/CLAUDE.md`

## Steps

### 1. Understand the insight
- What went wrong or what was learned?
- Is this a general rule or project-specific?
- Is it actionable (not just an observation)?

### 2. Check for duplicates
```bash
grep -i "<keyword>" ~/telegram-claude-bot/CLAUDE.md
```
- Don't add rules that already exist
- If similar rule exists, strengthen it instead of duplicating

### 3. Determine the section
CLAUDE.md has these sections:
- **Before implementing anything** — planning/evaluation rules
- **Architecture & quality** — code quality, reviews
- **Auto-save progress** — commit/memory discipline
- **Continuous self-improvement** — learning habits
- **Failure handling** — error diagnosis, debugging
- **Operations** — mobile-first, monitoring
- **External data & tools** — API/MCP/data handling
- **Config sync** — git/settings/deploy rules
- **Communication style** — response format
- **Bot management** — start_all.sh, restarts
- **Making changes** — verification, testing
- **Safety** — security, destructive operations

### 4. Add the rule
- Write as a clear, actionable directive
- Include context (why this rule exists)
- Reference the incident that taught us this (if applicable)
- Format: `- Rule text. Context/reason.`

### 5. Commit
```bash
cd ~/telegram-claude-bot
git add CLAUDE.md
git commit -m "claude.md: add rule about <topic>"
git push origin main
```

### 6. Sync to VPS
VPS auto-pulls within 10 min, or manually:
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~/telegram-claude-bot && git pull --ff-only"
```

## Rules
- CLAUDE.md rules are the highest priority — they override default behavior
- Every mistake should produce a rule so it never repeats
- Rules should be concise but include enough context to be self-explanatory
- "claude.md" trigger: when Owner says "claude.md", save the rule immediately
