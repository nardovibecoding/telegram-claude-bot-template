---
name: arch-review
description: Review codebase architecture for code smells, bloat, and tech debt
trigger: architecture review, arch review, code health, tech debt
tags: [architecture, review, quality, refactor]
---

# Architecture Review

## Steps

### 1. Count lines per file
```bash
cd ~/telegram-claude-bot
find . -name '*.py' -not -path '*/venv/*' -not -path '*/__pycache__/*' -exec wc -l {} + | sort -rn | head -20
```
- Files >500 lines are candidates for splitting
- Current known large files: bot_base.py, x_curator.py, admin_bot modules

### 2. Find duplicate functions
```bash
# Check for functions defined multiple times across files
grep -rn 'def ' --include='*.py' --exclude-dir=venv --exclude-dir=__pycache__ | \
  sed 's/.*def \([a-zA-Z_]*\).*/\1/' | sort | uniq -c | sort -rn | head -20
```
- Same function name in multiple files = possible duplication
- Investigate if they do the same thing

### 3. Check for unused imports
```bash
ruff check --select F401 --exclude venv .
```
- F401 = unused imports
- Safe to remove unless used dynamically

### 4. Find TODO/FIXME markers
```bash
grep -rn 'TODO\|FIXME\|HACK\|XXX\|WORKAROUND' --include='*.py' --exclude-dir=venv --exclude-dir=__pycache__
```
- Old TODOs may be stale — check if they're still relevant
- FIXMEs indicate known bugs

### 5. Check for stale files
```bash
# Files not modified in 30+ days
find . -name '*.py' -not -path '*/venv/*' -mtime +30 -exec ls -la {} +
```
- May be dead code or abandoned features
- Check if any bots/crons reference them

### 6. Check for hardcoded values
```bash
grep -rn 'localhost\|127.0.0.1\|YOUR_VPS_IP' --include='*.py' --exclude-dir=venv
```
- Should be in config, not hardcoded
- Exception: well-known localhost references

### 7. Report
Format as sections:
- **Large files** (>500 lines): list with line counts
- **Duplicates**: function names found in multiple files
- **Unused imports**: files with dead imports
- **Stale TODOs**: old markers that need resolution
- **Dead code**: unreferenced files
- **Recommendations**: top 3 refactoring priorities

## Rules
- This is a read-only review — don't change code without explicit approval
- Focus on actionable findings, not nitpicks
- Prioritize: bugs > dead code > style > nice-to-haves
- Run every 20 prompts as per CLAUDE.md rules
