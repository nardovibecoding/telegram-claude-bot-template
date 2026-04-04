#!/usr/bin/env node
// Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 license.
// MCP Proxy -- wraps multiple MCP servers behind 2 generic tools.
// Saves ~22K tokens by not loading 74 tool schemas at startup.
// Servers are spawned lazily on first use and cached for the session.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Load server configs, resolve ${VAR} env references
const rawConfig = readFileSync(join(__dirname, "servers.json"), "utf-8");
const serverConfigs = JSON.parse(rawConfig);

// Resolve env var references like ${TAVILY_API_KEY}
for (const cfg of Object.values(serverConfigs)) {
  if (cfg.env) {
    for (const [k, v] of Object.entries(cfg.env)) {
      if (typeof v === "string" && v.startsWith("${") && v.endsWith("}")) {
        const envName = v.slice(2, -1);
        cfg.env[k] = process.env[envName] || "";
      }
    }
  }
}

// Cache: server name -> { client, transport, toolList }
const connections = new Map();

async function getConnection(serverName) {
  if (connections.has(serverName)) return connections.get(serverName);

  const cfg = serverConfigs[serverName];
  if (!cfg) throw new Error(`Unknown server: ${serverName}. Available: ${Object.keys(serverConfigs).join(", ")}`);

  let transport;
  if (cfg.type === "http") {
    transport = new StreamableHTTPClientTransport(new URL(cfg.url));
  } else {
    transport = new StdioClientTransport({
      command: cfg.command,
      args: cfg.args || [],
      env: { ...process.env, ...(cfg.env || {}) },
    });
  }

  const client = new Client({ name: "claude-mcp-proxy", version: "1.0.0" });
  await client.connect(transport);

  const { tools } = await client.listTools();
  const entry = { client, transport, tools };
  connections.set(serverName, entry);
  return entry;
}

// Cleanup on exit
function cleanup() {
  for (const [name, { transport }] of connections) {
    try { transport.close(); } catch {}
  }
  process.exit(0);
}
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);
process.on("exit", () => {
  for (const [, { transport }] of connections) {
    try { transport.close(); } catch {}
  }
});

// --- MCP Server ---

const server = new Server(
  { name: "claude-mcp-proxy", version: "1.0.0" },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "list_server_tools",
      description: `List available tools from a proxied MCP server. Servers: ${Object.keys(serverConfigs).join(", ")}`,
      inputSchema: {
        type: "object",
        properties: {
          server: {
            type: "string",
            description: `Server name: ${Object.keys(serverConfigs).join(", ")}`,
          },
        },
        required: ["server"],
      },
    },
    {
      name: "call_tool",
      description: "Call a tool on a proxied MCP server. Use list_server_tools first to discover available tools and their parameters.",
      inputSchema: {
        type: "object",
        properties: {
          server: {
            type: "string",
            description: `Server name: ${Object.keys(serverConfigs).join(", ")}`,
          },
          tool: {
            type: "string",
            description: "Tool name (from list_server_tools)",
          },
          params: {
            type: "object",
            description: "Tool parameters (from list_server_tools schema)",
            additionalProperties: true,
          },
        },
        required: ["server", "tool"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  if (name === "list_server_tools") {
    const serverName = args.server;
    try {
      const { tools } = await getConnection(serverName);
      const summary = tools.map((t) => ({
        name: t.name,
        description: (t.description || "").slice(0, 200),
        params: t.inputSchema?.properties
          ? Object.keys(t.inputSchema.properties)
          : [],
        required: t.inputSchema?.required || [],
      }));
      return {
        content: [{ type: "text", text: JSON.stringify(summary, null, 2) }],
      };
    } catch (e) {
      return {
        content: [{ type: "text", text: `Error connecting to ${serverName}: ${e.message}` }],
        isError: true,
      };
    }
  }

  if (name === "call_tool") {
    const { server: serverName, tool, params } = args;
    try {
      const { client } = await getConnection(serverName);
      const result = await client.callTool({ name: tool, arguments: params || {} });
      return result;
    } catch (e) {
      return {
        content: [{ type: "text", text: `Error calling ${serverName}.${tool}: ${e.message}` }],
        isError: true,
      };
    }
  }

  return {
    content: [{ type: "text", text: `Unknown tool: ${name}` }],
    isError: true,
  };
});

const transport = new StdioServerTransport();
await server.connect(transport);
