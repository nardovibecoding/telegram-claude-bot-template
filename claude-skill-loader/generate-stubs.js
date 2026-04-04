#!/usr/bin/env node
// Copyright (c) 2026 Nardo (<github-user>). AGPL-3.0 license.
// Generates minimal stub SKILL.md files from SKILL.md.disabled files.
// Stubs register skills with Claude Code's /slash routing while keeping
// full content in .disabled files, served on-demand by the skill-loader MCP.

import { readdir, readFile, writeFile, access } from "fs/promises";
import { join, basename } from "path";

const SKILLS_DIR = join(process.env.HOME, ".claude", "skills");

function parseFrontmatter(content) {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)/);
  if (!match) return { raw: "", body: content, fields: {} };
  const fields = {};
  const lines = match[1].split("\n");
  let i = 0;
  while (i < lines.length) {
    const m = lines[i].match(/^([\w-]+):\s*(.*)/);
    if (m) {
      if (m[2] === "|") {
        // Multi-line value - grab first non-empty line as description
        let val = "";
        i++;
        while (i < lines.length && (lines[i].startsWith("  ") || lines[i].trim() === "")) {
          if (lines[i].trim()) val += (val ? " " : "") + lines[i].trim();
          i++;
        }
        fields[m[1]] = val;
        continue;
      } else {
        fields[m[1]] = m[2].replace(/^["']|["']$/g, "");
      }
    }
    i++;
  }
  return { raw: match[1], body: match[2], fields };
}

function shortDesc(text) {
  if (!text) return "";
  // Use full first line, trim to 150 chars
  const line = text.split("\n")[0].trim().replace(/\.$/, "");
  return line.length > 150 ? line.slice(0, 147) + "..." : line;
}

async function fileExists(path) {
  try { await access(path); return true; } catch { return false; }
}

async function main() {
  const dirs = await readdir(SKILLS_DIR, { withFileTypes: true });
  let created = 0, skipped = 0;

  for (const d of dirs) {
    if (!d.isDirectory()) continue;
    const disabledPath = join(SKILLS_DIR, d.name, "SKILL.md.disabled");
    const stubPath = join(SKILLS_DIR, d.name, "SKILL.md");

    if (!await fileExists(disabledPath)) continue;

    const content = await readFile(disabledPath, "utf-8");
    const { fields } = parseFrontmatter(content);

    const name = fields.name || d.name;
    const desc = shortDesc(fields.description || "");
    const userInvocable = fields["user-invocable"] || "true";
    const argHint = fields["argument-hint"] || "";

    let frontmatter = `---\nname: ${name}\ndescription: ${desc}\nuser-invocable: ${userInvocable}`;
    if (argHint) frontmatter += `\nargument-hint: ${argHint}`;
    frontmatter += `\n---`;

    const stub = `${frontmatter}\n<${d.name}>\nCall the skill-loader MCP tool \`load_skill\` with name "${d.name}" to get full instructions, then follow them.\n</${d.name}>\n`;

    await writeFile(stubPath, stub);
    created++;
    console.log(`  stub: ${d.name}`);
  }

  console.log(`\nDone: ${created} stubs created, ${skipped} skipped`);
}

main().catch(console.error);
