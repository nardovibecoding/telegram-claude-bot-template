---
name: install-mcp
description: Install a new MCP server on Mac and/or VPS
trigger: install mcp, add mcp server, new mcp, setup mcp
tags: [mcp, install, setup, integration]
---

# Install MCP Server

## Steps

### 1. Research the MCP server
- Read the GitHub README (use WebFetch)
- Determine install method: npm, pip, git clone, or docker
- Check if free tier available
- Check for overlap with existing tools

### 2. Install on VPS
```bash
# For npm-based:
ssh YOUR_VPS_USER@YOUR_VPS_IP "npm install -g <package>"

# For pip-based:
ssh YOUR_VPS_USER@YOUR_VPS_IP "pip install <package>"

# For git-based:
ssh YOUR_VPS_USER@YOUR_VPS_IP "cd ~ && git clone <repo> && cd <repo> && pip install -r requirements.txt"
```

### 3. Add to settings.json on BOTH Mac and VPS
**CRITICAL: NEVER overwrite settings.json — always read, merge, write.**

```bash
# Read current config
cat ~/.claude/settings.json | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['mcpServers'], indent=2))"
```

Add the new server entry — paths differ between Mac and VPS:
- Mac: `~/...`
- VPS: `~/...`

The sync script `~/sync_claude_config.py` handles platform-specific path translation, but you must add the entry to at least one side.

### 4. Test the MCP server
```bash
# For HTTP-based MCPs:
curl -s http://localhost:<port>/health

# For stdio-based MCPs:
# Restart Claude Code and verify MCP tools appear
```

### 5. Commit settings (if git-tracked)
Note: `~/.claude/settings.json` is NOT git-tracked. Update on both Mac and VPS manually, or rely on `sync_claude_config.py` (runs every 10 min).

## Existing MCP servers
- **Xiaohongshu**: port 18060, `~/xiaohongshu-mcp/`
- **Douyin**: port 18070, `~/douyin-mcp-server/`
- Both installed on Mac + VPS

## Rules
- Always check if the MCP server overlaps with existing tools before installing
- Test on VPS first (production environment)
- Update both Mac and VPS configs
- Never overwrite settings.json wholesale — read, merge, write
