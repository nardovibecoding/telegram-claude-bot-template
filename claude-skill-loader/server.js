// Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 license.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { readdir, readFile } from "fs/promises";
import { join, basename } from "path";

const SKILLS_DIR = join(process.env.HOME, ".claude", "skills");
const PLUGIN_SKILLS_DIRS = [
  join(process.env.HOME, ".claude", "plugins"),
];

async function findSkillFiles() {
  const skills = [];

  // Scan ~/.claude/skills/*/SKILL.md{,.disabled}
  try {
    const dirs = await readdir(SKILLS_DIR, { withFileTypes: true });
    for (const d of dirs) {
      if (!d.isDirectory()) continue;
      const dir = join(SKILLS_DIR, d.name);
      for (const ext of ["SKILL.md", "SKILL.md.disabled"]) {
        try {
          const path = join(dir, ext);
          const content = await readFile(path, "utf-8");
          skills.push({ name: d.name, path, disabled: ext.endsWith(".disabled"), content });
        } catch {}
      }
    }
  } catch {}

  // Scan plugin skills recursively
  for (const base of PLUGIN_SKILLS_DIRS) {
    try {
      await scanPluginSkills(base, skills);
    } catch {}
  }

  return skills;
}

async function scanPluginSkills(dir, skills, depth = 0) {
  if (depth > 5) return;
  try {
    const entries = await readdir(dir, { withFileTypes: true });
    for (const e of entries) {
      const full = join(dir, e.name);
      if (e.isDirectory()) {
        await scanPluginSkills(full, skills, depth + 1);
      } else if (e.name === "SKILL.md" || e.name === "SKILL.md.disabled") {
        try {
          const content = await readFile(full, "utf-8");
          const parent = basename(join(full, ".."));
          const grandparent = basename(join(full, "../.."));
          const name = grandparent === "skills" ? parent : `${grandparent}:${parent}`;
          skills.push({ name, path: full, disabled: e.name.endsWith(".disabled"), content });
        } catch {}
      }
    }
  } catch {}
}

function parseFrontmatter(content) {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)/);
  if (!match) return { meta: {}, body: content };
  const meta = {};
  for (const line of match[1].split("\n")) {
    const m = line.match(/^(\w[\w-]*):\s*(.+)/);
    if (m) meta[m[1]] = m[2].trim();
  }
  return { meta, body: match[2] };
}

const server = new McpServer({
  name: "claude-skill-loader",
  version: "1.0.0",
});

server.tool(
  "list_skills",
  "List all available skills with one-line descriptions. Use this to see what skills can be loaded on demand.",
  {},
  async () => {
    const skills = await findSkillFiles();
    const lines = skills.map((s) => {
      const { meta } = parseFrontmatter(s.content);
      const desc = (meta.description || "").split("\n")[0].trim();
      return `${s.name} — ${desc}`;
    });
    return { content: [{ type: "text", text: lines.join("\n") }] };
  }
);

server.tool(
  "load_skill",
  "Load a skill's full instructions on demand. Call this when you need a skill's instructions (e.g. user says /critic, or you need systematic-debugging). Returns the full SKILL.md content.",
  { name: z.string().describe("Skill name from list_skills") },
  async ({ name }) => {
    const skills = await findSkillFiles();
    const skill = skills.find((s) => s.name === name);
    if (!skill) {
      return { content: [{ type: "text", text: `Skill "${name}" not found. Use list_skills to see available skills.` }] };
    }
    return { content: [{ type: "text", text: skill.content }] };
  }
);

server.tool(
  "reload_skill",
  "Re-read a skill's instructions if context was compressed and instructions were lost. Same as load_skill but signals intent to re-inject.",
  { name: z.string().describe("Skill name to reload") },
  async ({ name }) => {
    const skills = await findSkillFiles();
    const skill = skills.find((s) => s.name === name);
    if (!skill) {
      return { content: [{ type: "text", text: `Skill "${name}" not found.` }] };
    }
    return { content: [{ type: "text", text: `[Re-injected]\n\n${skill.content}` }] };
  }
);

server.tool(
  "generate_stubs",
  "Regenerate minimal stub SKILL.md files from all SKILL.md.disabled files. Run after adding/removing skills to keep /slash routing in sync.",
  {},
  async () => {
    const { execFile } = await import("child_process");
    const { promisify } = await import("util");
    const exec = promisify(execFile);
    try {
      const { stdout } = await exec("node", [join(import.meta.dirname, "generate-stubs.js")]);
      return { content: [{ type: "text", text: stdout }] };
    } catch (e) {
      return { content: [{ type: "text", text: `Error: ${e.message}` }] };
    }
  }
);

const transport = new StdioServerTransport();
await server.connect(transport);
