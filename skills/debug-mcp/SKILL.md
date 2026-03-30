---
name: debug-mcp
description: Diagnose and fix MCP server issues (XHS, Douyin)
trigger: mcp down, xhs not working, douyin error, mcp health
tags: [mcp, debug, xiaohongshu, douyin, health]
---

# Debug MCP Servers

## MCP Endpoints
- **Xiaohongshu (XHS)**: `localhost:18060` on VPS
- **Douyin**: `localhost:18070` on VPS

## Steps

### 1. Check health
```bash
# XHS
ssh YOUR_VPS_USER@YOUR_VPS_IP "curl -s http://localhost:18060/health"

# Douyin
ssh YOUR_VPS_USER@YOUR_VPS_IP "curl -s http://localhost:18070/health"
```

### 2. If down, check logs
```bash
# XHS logs
ssh YOUR_VPS_USER@YOUR_VPS_IP "tail -30 /tmp/xhs-mcp-py.log"

# Douyin logs
ssh YOUR_VPS_USER@YOUR_VPS_IP "tail -30 /tmp/dy-mcp-py.log"
```

### 3. Restart the service
```bash
# XHS
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user restart xhs-mcp"

# Douyin
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user restart douyin-mcp"
```

### 4. Verify after restart
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "sleep 3 && curl -s http://localhost:18060/health"
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user status xhs-mcp"
```

### 5. Check service status
```bash
ssh YOUR_VPS_USER@YOUR_VPS_IP "systemctl --user status xhs-mcp douyin-mcp"
```

## Common issues
| Issue | Cause | Fix |
|-------|-------|-----|
| Connection refused | Service not running | `systemctl --user restart` |
| MustWaitStable panic | Anti-bot blocking (VPS IP) | Cannot fix — VPS IP is blocked |
| Login expired | Cookies stale | Use `/xhslogin` in TG for QR |
| Search returns empty | Blocked or rate limited | Wait, or use from Mac IP |

## Notes
- XHS search is blocked on VPS IP — use MCP tools directly, don't curl workarounds
- Douyin search needs auth cookies — homepage feed works without
- ALWAYS use MCP tools directly instead of curl/bash workarounds
- XHS login: `/xhslogin` (QR on TG), `/xhscheck` (verify status)
