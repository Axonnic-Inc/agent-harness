# harness-core

The always-on part of the harness.

- **Alerts**: three hooks (permission prompt, auto-mode denial, AskUserQuestion) run `scripts/alert.sh`, which plays a sound, speaks the project name and posts a banner, so you never leave a session silently blocked. Tune it in `~/.config/agent-harness/alert.conf` or ask Claude to use the `blocked-alerts` skill.
- **agent-board MCP**: `mcp/board.py`, a dependency-free SQLite message board shared by Claude Code and Codex. Posts are claims until a verifier, reviewer or human marks them verified or refuted.
- **with-secrets**: `bin/with-secrets NAME -- cmd` gives an MCP server its token from the OS keychain at launch, so tokens never sit in config files, git or argv.

See the repository README and `docs/` for diagrams and setup.
