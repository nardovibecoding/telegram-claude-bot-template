---
name: update-mcp-config
description: Update MCP server configuration on both Mac and VPS
trigger: update mcp config, mcp settings, add mcp server config
tags: [mcp, config, settings, sync]
---

# Update MCP Config

## Config file
`~/.claude/settings.json` — NOT git-tracked, must update both sides manually or via sync

## CRITICAL: Never overwrite — always merge

### 1. Read current config
```bash
# Mac
cat ~/.claude/settings.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['mcpServers'], indent=2))"

# VPS
ssh YOUR_VPS_USER@YOUR_VPS_IP "cat ~/.claude/settings.json | python3 -c \"import sys,json; print(json.dumps(json.load(sys.stdin)['mcpServers'], indent=2))\""
```

### 2. Add new entry
Read the full file, add the new server entry, write back:

```python
import json

with open(os.path.expanduser('~/.claude/settings.json')) as f:
    config = json.load(f)

config['mcpServers']['new-server'] = {
    "command": "python",
    "args": ["/path/to/server.py"],
    "env": {}
}

with open(os.path.expanduser('~/.claude/settings.json'), 'w') as f:
    json.dump(config, f, indent=2)
```

### 3. Update BOTH Mac and VPS
- Paths differ between Mac and VPS:
  - Mac: `~/...`
  - VPS: `~/...`
- `~/sync_claude_config.py` runs every 10 min and handles path translation
- But for immediate changes, update both manually

### 4. Verify
```bash
# Mac
cat ~/.claude/settings.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d['mcpServers'], indent=2))"

# VPS
ssh YOUR_VPS_USER@YOUR_VPS_IP "cat ~/.claude/settings.json | python3 -c \"import sys,json; d=json.load(sys.stdin); print(json.dumps(d['mcpServers'], indent=2))\""
```

### 5. Test MCP server
```bash
# For HTTP-based:
curl -s http://localhost:<port>/health

# For stdio-based:
# Restart Claude Code session — MCP servers initialize on startup
```

## Sync mechanism
- `~/sync_claude_config.py` merges MCP configs between Mac and VPS
- Runs every 10 min via launchd on Mac
- Handles platform-specific path translation automatically

## Rules
- NEVER overwrite settings.json wholesale — read, merge, write
- Always update BOTH Mac and VPS
- Use full paths (not relative) for MCP server commands
- Test the MCP server is actually reachable after config update
