---
name: code-review
description: Run automated code quality checks on the codebase
trigger: code review, lint, check code, quality check
tags: [lint, review, quality, ruff, syntax]
---

# Code Review

## Steps

### 1. Lint with ruff (fatal errors only)
```bash
cd ~/telegram-claude-bot
ruff check --select F admin_bot/ *.py
```
- `F` = Pyflakes — catches undefined vars, unused imports, redefined names
- These are real bugs, not style issues

### 2. Syntax check all Python files
```bash
for f in $(find ~/telegram-claude-bot -name '*.py' -not -path '*/venv/*' -not -path '*/__pycache__/*'); do
  python3 -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>&1 | grep -v "^$" || true
done
```

### 3. Check recently changed files specifically
```bash
cd ~/telegram-claude-bot
for f in $(git diff --name-only HEAD~5 | grep '\.py$'); do
  echo "=== $f ==="
  python3 -c "import py_compile; py_compile.compile('$f', doraise=True)"
  ruff check --select F "$f"
done
```

### 4. Check start_all.log for recent runtime errors
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "grep 'ERROR\|Traceback\|NameError\|ImportError\|AttributeError' /tmp/start_all.log | tail -20"
```

### 5. Report findings
For each issue found:
1. File and line number
2. Error type (syntax, undefined var, unused import, runtime)
3. Severity (critical / warning / info)
4. Proposed fix

### 6. Fix and verify
- Fix critical issues immediately
- py_compile each fixed file
- Deploy if fixes were applied (use deploy-vps skill)

## Common issues
| Check | Catches |
|-------|---------|
| `ruff --select F` | Undefined vars, unused imports, shadowed names |
| `py_compile` | Syntax errors, indentation, invalid Python |
| Log grep | Runtime errors the linter can't catch |

## Rules
- Always run py_compile BEFORE deploying any change
- Focus on `F` (Pyflakes) errors — these are real bugs
- Don't fix style issues unless specifically asked
- Check logs for runtime errors that static analysis misses
