---
name: git-resolve
description: Resolve git conflicts between Mac and VPS using stash + rebase
trigger: git conflict, merge conflict, cannot push, diverged
tags: [git, conflict, merge, rebase]
---

# Resolve Git Conflicts

## Context
- Conflicts happen when VPS admin bot commits (via commit button) while Mac also has local changes.
- NEVER force push. Always rebase local on top of remote.

## Steps

### 1. Stash local changes
```bash
cd ~/telegram-claude-bot
git stash
```

### 2. Pull with rebase
```bash
git pull origin main --rebase
```

### 3. Pop stash and check for conflicts
```bash
git stash pop
```

### 4. If conflicts exist
```bash
# List conflicted files
git diff --name-only --diff-filter=U

# Open each file — look for <<<<<<< markers
# Resolve by keeping the correct version (usually merge both changes)
# After resolving each file:
git add <resolved-file>
```

### 5. Commit the resolution
```bash
git commit -m "resolve merge conflict in <files>"
git push origin main
```

### 6. Sync VPS
```bash
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && git pull --ff-only"
```

## Rules
- NEVER use `git push --force` — it destroys VPS commits
- NEVER use `git rebase -i` — requires interactive input
- Always check `git log --oneline -5` on both Mac and VPS to confirm sync
- If stash pop conflicts are too messy: `git stash drop` and manually re-apply your changes

## Prevention
- Always `git pull origin main` before starting any edits
- VPS has auto-pull every 10 min via cron — check before editing there too
