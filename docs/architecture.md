# Architecture

The harness is four small, independent mechanisms that share two things: **a message board** and **a secrets launcher**.
Each mechanism works alone; together they let Claude Code and Codex act as one team while you stay in the loop only when you are actually needed.

## Component map

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/components-dark.png">
  <img alt="Component map: the repo's plugins, harness and Codex files, and where each is linked or registered in your home directory." src="diagrams/components-light.png">
</picture>

<sub>Diagram source: <a href="diagrams/components.mmd"><code>components.mmd</code></a></sub>

**Why symlinks?** The repo stays the single source of truth. `git pull` updates the live harness; editing a file in the repo is
editing the harness. The installer never copies harness code into your home directory (the one exception is `AGENTS.md`,
which is copied only if you have none, because it is meant to be personalized).

**Why plugins for Claude Code?** Plugins are Claude Code's native unit of distribution: they bundle hooks, MCP servers and skills,
resolve paths with `${CLAUDE_PLUGIN_ROOT}`, can be enabled or disabled per user or project, and install from a GitHub repo with one
command. Nothing is written into your `settings.json` unless you ask for `--merge-settings`.

## Where state lives

| State | Path | Written by | Safe to delete? |
|---|---|---|---|
| Board posts, status history, read markers | `~/.agent-board/board.sqlite` | board.py (WAL mode, many writers) | Yes: history is lost, the board recreates itself |
| Per-run board (`compete --board`) | `~/.codex/harness/runs/<run>/board/board.sqlite` | board.py | With the run |
| astra runs: prompts, logs, diffs, `REPORT.md` | `~/.codex/harness/runs/<stamp>-<kind>-<slug>/` | astra | Yes: `astra clean <run>` keeps reports, drops workspaces |
| Alert settings | `~/.config/agent-harness/alert.conf` | you | Yes: defaults are all-on, no pop-up |
| Alert log | `~/Library/Logs/claude-alert.log` (Linux: `~/.local/state/`) | alert.sh | Yes |
| Secrets | OS keychain, service `agent-harness` | `with-secrets --set` | Use `with-secrets --rm NAME` |
| Secrets fallback | `~/.config/agent-harness/.env` (chmod 600) | you | Yes |

## Life of a request

A typical day: you ask Claude to fix something, it hits a permission prompt, you get pulled back in, and later Codex picks up the follow-up from the board.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="diagrams/request-lifecycle-dark.png">
  <img alt="Sequence: a permission prompt alerts you, Claude posts an artifact and a claim to the board, Codex picks it up and a verifier confirms it." src="diagrams/request-lifecycle-light.png">
</picture>

<sub>Diagram source: <a href="diagrams/request-lifecycle.mmd"><code>request-lifecycle.mmd</code></a></sub>

## Design principles

1. **Never block the owner.** Every check in astra is a warning when *you* launch it, and a hard stop only when an *agent* launched it (`CLAUDECODE=1`) without `--force`.
2. **Claims are hypotheses.** The board labels every post `UNVERIFIED` until a different agent with a judging role checks it. No author can verify their own claim.
3. **Diversity beats repetition.** astra gives parallel solvers different starting angles and verifies each answer with a *different model family*.
4. **Secrets are fetched, not stored.** A token exists only in the environment of the process that needs it, for as long as it runs.
5. **Zero runtime dependencies.** astra and the board are Python standard library only; the alert is plain bash. `npx` is needed only for the third-party MCP servers.
