# claude-mcp-proxy

You added 8 MCP servers. Now every session starts 22K tokens deep before you say anything.

This proxy wraps them behind 2 generic tools, spawning servers lazily on first use.

## How it works

Instead of loading all tool schemas at startup, the proxy exposes just 2 tools:

- `list_server_tools(server)` -- discover tools from a proxied server
- `call_tool(server, tool, params)` -- forward a call to the real server

Servers are spawned on first use and cached for the session. Supports both stdio and HTTP transports.

## Setup

```bash
git clone https://github.com/<github-user>/claude-mcp-proxy.git ~/.claude/claude-mcp-proxy
cd ~/.claude/claude-mcp-proxy
npm install
cp servers.example.json servers.json
# Edit servers.json with your MCP server configs
```

Add to Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "mcp-proxy": {
      "command": "node",
      "args": ["~/.claude/claude-mcp-proxy/server.js"]
    }
  }
}
```

## servers.json format

```json
{
  "my-server": {
    "type": "stdio",
    "command": "node",
    "args": ["/path/to/server.js"],
    "env": { "API_KEY": "${MY_API_KEY}" }
  },
  "my-http-server": {
    "type": "http",
    "url": "http://localhost:8080/mcp"
  }
}
```

Environment variables in `${VAR}` syntax are resolved from the process environment at startup.

## License

AGPL-3.0 -- see [LICENSE](LICENSE)
