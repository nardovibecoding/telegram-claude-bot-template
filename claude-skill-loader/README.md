# claude-skill-loader

You wrote 35 skills. Now Claude's context is half full of instructions it doesn't need yet.

This MCP server loads them on demand via stubs, cutting injection to ~2K tokens.

## How it works

Full SKILL.md files are renamed to SKILL.md.disabled. A stub generator creates minimal SKILL.md files that register with Claude Code's /slash routing but contain only a one-liner telling Claude to call the MCP tool for full instructions.

4 MCP tools:

- `list_skills` -- compact list of all available skills
- `load_skill(name)` -- returns full SKILL.md content on demand
- `reload_skill(name)` -- re-inject after context compression
- `generate_stubs` -- regenerate stubs from all .disabled files

## Setup

```bash
git clone https://github.com/<github-user>/claude-skill-loader.git ~/.claude/claude-skill-loader
cd ~/.claude/claude-skill-loader
npm install
```

Add to Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "skill-loader": {
      "command": "node",
      "args": ["~/.claude/claude-skill-loader/server.js"]
    }
  }
}
```

## Creating stubs

For each skill you want to lazy-load:

1. Rename `SKILL.md` to `SKILL.md.disabled`
2. Run `generate_stubs` via the MCP tool (or `node generate-stubs.js`)
3. A minimal stub SKILL.md is created that routes to the loader

Skills without a .disabled file keep their full SKILL.md and load normally.

## License

AGPL-3.0 -- see [LICENSE](LICENSE)
