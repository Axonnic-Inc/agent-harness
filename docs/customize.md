# Customize

Everything is plain files. Edit them in the repo; because the install is symlinks and directory-sourced plugins,
most changes take effect on the next session (`/reload-plugins` in Claude Code).

## Add a skill

```bash
mkdir -p plugins/harness-core/skills/my-skill
$EDITOR plugins/harness-core/skills/my-skill/SKILL.md
```

```markdown
---
name: my-skill
description: One sentence on what it does and the phrases that should trigger it.
---

Instructions Claude follows when the skill is used.
```

Codex skills go in `codex/skills/<name>/SKILL.md`; add a matching line to the `LINKS` list in `install.sh`.

## Add an MCP server that needs a token

1. Store the token: `with-secrets --set ACME_API_KEY`.
2. Add it to a plugin's `.mcp.json`, launching through `with-secrets`. `install.sh` links it into `~/.local/bin`; if that
   isn't on the PATH Claude Code starts with, use the absolute path. Inside harness-core itself, use
   `${CLAUDE_PLUGIN_ROOT}/bin/with-secrets`.

```json
{
  "mcpServers": {
    "acme": {
      "command": "with-secrets",
      "args": ["ACME_API_KEY", "--", "npx", "-y", "@acme/mcp-server"]
    }
  }
}
```

3. Add `ACME_API_KEY=` to `.env.example` so others know the name.
4. `claude plugin validate plugins/<plugin> --strict`, then `/reload-plugins`.

## Add a plugin

```text
plugins/my-plugin/
├── .claude-plugin/plugin.json    {"name": "my-plugin", "version": "0.1.0", "description": "..."}
├── README.md
├── skills/  hooks/hooks.json  .mcp.json  bin/  agents/   (any subset)
```

Register it in `.claude-plugin/marketplace.json`:

```json
{ "name": "my-plugin", "source": "./plugins/my-plugin", "description": "..." }
```

## Edit a diagram

Diagrams live as Mermaid sources in `docs/diagrams/*.mmd`. Pages embed pre-rendered PNGs (a light and a dark variant,
picked by the reader's color scheme), so they render the same on GitHub, GitHub mobile and the docs site.

```bash
$EDITOR docs/diagrams/astra-compete.mmd
scripts/render-diagrams.sh          # needs Node; writes <name>-light.png and <name>-dark.png
```

CI fails if a source has a syntax error or a PNG is missing.

## Tune astra

| What | Where |
|---|---|
| default models | `CODEX_MODELS` and the `--models` default in `harness/astra.py` |
| starting angles | `ANGLES` in `harness/astra.py` |
| solver / verifier / judge prompts | `SOLVER`, `VERIFIER`, `INSIGHTS`, `REFINE`, `JUDGE` in `harness/astra.py` |
| required brief sections | `BRIEF_SECTIONS` in `harness/astra_ext.py` |
| token prices for `astra usage` | `harness/prices.json` |

Run `python3 -m unittest discover -s harness` after changes.

## Share your fork

```bash
gh repo fork Axonnic-Inc/agent-harness --clone   # your own copy to customize
cd agent-harness && ./install.sh --dry-run
```

Friends then install from your fork, either with the full installer or, in Claude Code only:
`/plugin marketplace add <your-github-user>/agent-harness` → `/plugin install harness-core@agent-harness`.
