#!/bin/bash
# Wrapper for skill-loader MCP server — logs errors for debugging
LOG="/tmp/skill-loader-mcp.log"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting skill-loader MCP server" >> "$LOG"
exec node ~/.claude/claude-skill-loader/server.js 2>> "$LOG"
