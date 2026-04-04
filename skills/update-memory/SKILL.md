---
name: update-memory
description: Write or update memory files in Claude's project memory
trigger: update memory, save to memory, remember this, memory update
tags: [memory, persistence, learning, context]
---

# Update Memory

## Memory locations
- **Mac**: `~/.claude/projects/-Users-bernard/memory/`
- **VPS**: `~/.claude/projects/-home-bernard/memory/`
- Synced bidirectionally every 10 min via `~/sync_claude_memory.sh`

## Steps

### 1. Determine content type
- Project details → `<project-name>.md`
- Lessons learned → add to `MEMORY.md` index
- Reference info → `reference_<topic>.md`
- Feedback/preferences → `feedback_<topic>.md`

### 2. Write memory file
Create or update `~/.claude/projects/-Users-bernard/memory/<name>.md`:

```markdown
# Title

## Section
Content here...
```

No YAML frontmatter needed for memory files (unlike SKILL.md files).

### 3. Add pointer to MEMORY.md index
If creating a new file, add a reference in MEMORY.md:
```markdown
### Topic Name
- [Description](memory/<name>.md)
```

### 4. Commit
```bash
cd ~/telegram-claude-bot  # or wherever MEMORY.md lives
git add -A
git commit -m "memory: update <topic>"
git push origin main
```

Note: Memory files in `~/.claude/` are NOT in the git repo — they sync via rsync.
Only `MEMORY.md` at project root (if it exists there) would be git-tracked.

### 5. Verify sync
Wait 10 min for auto-sync, or manually:
```bash
# Trigger sync from Mac
~/sync_claude_memory.sh
```

## What to save
- Architecture decisions and rationale
- Recurring pain points and their solutions
- Bernard's preferences and feedback
- Project-specific configuration details
- Integration details (API endpoints, auth methods, etc.)

## What NOT to save
- Temporary debugging notes
- Secrets, tokens, or credentials
- Duplicate information already in CLAUDE.md

## Rules
- Memory is for PERSISTENT context across sessions
- Keep entries concise — future sessions load all memory files
- Update existing entries rather than creating duplicates
- Always add an index pointer in MEMORY.md for discoverability
- VPS memory dir uses `-home-bernard`, Mac uses `-Users-bernard`
