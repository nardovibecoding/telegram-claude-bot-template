---
name: feature-branch
description: Develop features on a branch, test, merge to main, deploy
trigger: new feature, big change, branch, feature branch
tags: [git, branch, feature, development]
---

# Feature Branch Development

## When to use
- Any change over 50 lines
- New subsystems or modules
- Risky refactors
- Never develop big features directly on main.

## Steps

### 1. Create branch
```bash
cd ~/telegram-claude-bot
git pull origin main
git checkout -b feature/<name>
```

### 2. Develop
- Make changes in small increments
- Commit frequently with descriptive messages
- Test locally where possible

### 3. Verify before merge
```bash
# Syntax check all changed Python files
for f in $(git diff --name-only main...HEAD | grep '\.py$'); do
  python3 -c "import py_compile; py_compile.compile('$f', doraise=True)"
done

# Lint check
ruff check --select F $(git diff --name-only main...HEAD | grep '\.py$')
```

### 4. Merge to main
```bash
git checkout main
git pull origin main
git merge feature/<name>
```

### 5. Push and deploy
```bash
git push origin main

# Deploy to VPS
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && git fetch && git reset --hard origin/main"

# Restart affected bot
ssh <user>@<vps-ip> "kill \$(pgrep -f 'python admin_bot' | head -1)"
```

### 6. Clean up
```bash
git branch -d feature/<name>
```

## Rollback
If the feature breaks production:
```bash
git revert HEAD
git push origin main
ssh <user>@<vps-ip> "cd ~/telegram-claude-bot && git pull --ff-only"
# Restart affected bot
```

## Rules
- Never push feature branches to remote — merge locally then push main
- If merge conflicts: resolve carefully, test, then push
- Always verify with `pgrep` + log check after deploy
