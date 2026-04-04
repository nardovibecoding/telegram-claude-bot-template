#!/bin/bash
# Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 — see LICENSE
# Setup MCP proxy + skill loader on VPS.
# Run: bash ~/telegram-claude-bot/scripts/setup_vps_mcp.sh

set -e
REPO=~/telegram-claude-bot
PROXY_DIR=$REPO/claude-mcp-proxy
SKILLS_DIR=$REPO/claude-skill-loader
CLAUDE_DIR=~/.claude

echo "=== VPS MCP proxy + skill loader setup ==="

# 1. Install npm deps if needed
if [ ! -d "$PROXY_DIR/node_modules" ]; then
  echo "Installing mcp-proxy deps..."
  cd $PROXY_DIR && npm install
fi
if [ ! -d "$SKILLS_DIR/node_modules" ]; then
  echo "Installing skill-loader deps..."
  cd $SKILLS_DIR && npm install
fi

# 2. Write servers.json for the proxy (VPS paths)
cat > $PROXY_DIR/servers.json << 'EOF'
{
  "xiaohongshu": {
    "type": "http",
    "url": "http://localhost:18060/mcp"
  },
  "douyin": {
    "type": "http",
    "url": "http://localhost:18070/mcp"
  },
  "lenny-transcripts": {
    "type": "http",
    "url": "https://lenny-mcp.onrender.com/mcp"
  }
}
EOF
echo "Written: $PROXY_DIR/servers.json"

# 3. Ensure ~/.claude/skills/ exists (for skill loader)
mkdir -p $CLAUDE_DIR/skills

# 4. Update ~/.claude/settings.json — replace direct MCPs with proxy + loader
python3 << 'PYEOF'
import json
from pathlib import Path

settings_path = Path.home() / ".claude" / "settings.json"
settings = json.loads(settings_path.read_text())

proxy_dir = str(Path.home() / "telegram-claude-bot" / "claude-mcp-proxy")
skills_dir = str(Path.home() / "telegram-claude-bot" / "claude-skill-loader")

# Replace mcpServers with proxy + skill-loader
settings["mcpServers"] = {
    "mcp-proxy": {
        "type": "stdio",
        "command": "node",
        "args": [proxy_dir + "/server.js"]
    },
    "skill-loader": {
        "type": "stdio",
        "command": "node",
        "args": [skills_dir + "/server.js"]
    }
}

settings_path.write_text(json.dumps(settings, indent=2))
print(f"Updated: {settings_path}")
PYEOF

echo ""
echo "=== Done ==="
echo "MCP proxy: $(node $PROXY_DIR/server.js --version 2>/dev/null || echo 'ready')"
echo "servers.json: $(cat $PROXY_DIR/servers.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(list(d.keys()))')"
echo ""
echo "Start a new Claude Code session to verify low token baseline."
