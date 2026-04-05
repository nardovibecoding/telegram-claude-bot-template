#!/bin/bash
# Wrapper for mcp-proxy MCP server — logs errors for debugging
LOG="/tmp/mcp-proxy-mcp.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting mcp-proxy MCP server" >> "$LOG"
exec node ~/.claude/claude-mcp-proxy/server.js 2>> "$LOG"
