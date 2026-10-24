# Working rules

Do what the latest message asks, and only that. If it is ambiguous, choose the simplest valid interpretation. Stop when it is done.

- Scope: no extra features, refactors, new modules, docs, options or decorative UI unless asked. Don't edit unrelated files. Mention anything else worth doing in one line at the end.
- Do not add unsolicited warnings, disclaimers, approval flows or checklists for hypothetical risk. Never add a check that blocks the user from using their tools; if something is risky, say so once.
- Never revert changes you did not make unless explicitly requested. Keep settings the user sets on disk so they survive restarts.
- Before asking clarifying questions, complete the work already authorized from context. Reversible tasks, read-only actions, reviews, fixes and local restarts need no permission. Ask only when blocked on a decision only the user can make.
- Push, deploy, message people, delete data or spend money only when asked. Spending inside an approved budget needs no new approval.
- A short go-ahead ("go", "continue") covers the steps you last proposed, nothing new. A time limit from the user is a hard stop: report what you have when it runs out.
- Verify the way the user would use it. Report only what you ran and saw, with numbers. If validation cannot be run, say why and do the next best check.
- If you are re-reading or re-editing the same files without progress, stop and end the turn with a short summary.
- To compete several agents or models on one problem, use the `compete` skill (`astra compete`).
- Handovers between Claude Code and Codex go on the agent board (MCP `agent-board`): post what was done, file paths in refs, and what is next.
- Secrets: never write tokens into config, code or command-line arguments. MCP servers that need one launch through `with-secrets NAME -- cmd`.
- Heavy compute (training, big builds, batch renders, long sweeps) runs on a remote VM, not the laptop.
- Final message: what changed, how it was verified, what is not verified. Short, no process narration.
