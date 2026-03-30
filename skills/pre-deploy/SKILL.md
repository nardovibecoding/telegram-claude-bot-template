---
name: pre-deploy
description: Run all pre-deployment checks before pushing to VPS
trigger: pre-deploy, pre deploy check, ready to deploy, check before deploy
tags: [deploy, check, lint, verify, safety]
---

# Pre-Deploy Checks

Run ALL checks before deploying. If any fail, fix before proceeding.

## Steps

### 1. Syntax check all changed files
```bash
cd ~/telegram-claude-bot
for f in $(git diff --name-only --diff-filter=ACMR HEAD | grep '\.py$'); do
  echo "Checking $f..."
  python3 -c "import py_compile; py_compile.compile('$f', doraise=True)"
done
```

### 2. Lint check (fatal errors)
```bash
ruff check --select F $(git diff --name-only --diff-filter=ACMR HEAD | grep '\.py$')
```
- F = Pyflakes: undefined vars, unused imports, redefined names
- These are real bugs, not style issues

### 3. Security check
```bash
# If bandit is installed:
bandit -ll $(git diff --name-only --diff-filter=ACMR HEAD | grep '\.py$') 2>/dev/null || echo "bandit not installed, skipping"
```
- `-ll` = only medium and high severity
- Check for hardcoded secrets, SQL injection, etc.

### 4. Review the diff
```bash
git diff HEAD
```
- Look for:
  - Accidental debug prints
  - Hardcoded API keys or tokens
  - Commented-out code that shouldn't be committed
  - Files that shouldn't be tracked (.env, credentials)

### 5. Check for uncommitted data files
```bash
git status
```
- Make sure runtime-modified files (domain_groups.json, etc.) are committed
- `git reset --hard` on VPS will revert uncommitted changes

### 6. If all pass → proceed with deploy
Use deploy-vps skill to push and deploy.

## Checklist summary
- [ ] py_compile passes on all changed .py files
- [ ] ruff check --select F passes (no fatal lint errors)
- [ ] No hardcoded secrets in diff
- [ ] No accidental debug code
- [ ] Runtime data files committed
- [ ] Ready to deploy

## Rules
- NEVER skip pre-deploy checks — they exist because we've been burned
- py_compile would have caught both historical UnboundLocalError bugs
- If any check fails: fix first, don't deploy broken code
