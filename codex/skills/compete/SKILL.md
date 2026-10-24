---
name: compete
description: Race several agents/models (Astra, Sol, Claude Opus) on one hard problem, verify each answer with other models and pick the best. Use when the user asks to compete, race or pit agents or models against each other.
---

`astra compete` starts other agents and writes to `~/.codex/harness/runs`, which this chat's sandbox does not allow. So write the exact command for the user to paste into a terminal, run from the repo the problem lives in:

    cd <repo> && astra compete "<problem>" --bg     # or @path/to/problem.md for a long statement

- Add `--check "<cmd>"` when a command can score an answer (tests, an eval script, `lake build`).
- Add `--budget <minutes>` when the user gives a time limit; it is a hard stop.
- Add `-n <count>` when the user names a number of agents, and `--network` if solvers need the internet.

If this chat has full access, run the command yourself instead. Reading progress works from the sandbox: `astra status`, then the run's REPORT.md for the winner, the table and the caveats. Apply the winner with `astra apply` only if the user asks.
