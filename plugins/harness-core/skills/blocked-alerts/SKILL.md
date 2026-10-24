---
name: blocked-alerts
description: Turn on, off, test or tune the loud alert (sound, spoken line, banner) that fires when a Claude Code session is blocked on the user by a permission prompt, an auto-mode denial or a question. Use when the user says they missed a prompt, wants alerts louder or quieter, or asks to test them.
---

# Blocked alerts

The harness-core plugin's hooks run `${CLAUDE_PLUGIN_ROOT}/scripts/alert.sh`:

| Hook | Fires when | Banner title |
|---|---|---|
| Notification `permission_prompt\|elicitation_dialog` | a permission prompt or input dialog is waiting | Claude: permission needed |
| PermissionDenied | the auto-mode classifier blocked a step | Claude: auto mode blocked a step |
| PreToolUse `AskUserQuestion` | Claude asks a question | Claude: question for you |

macOS: plays Sosumi, says "Claude needs you in <project>", posts a banner via `terminal-notifier` (does not steal focus).
Linux: `paplay`, `spd-say`, `notify-send` when installed. Every alert is logged to `~/Library/Logs/claude-alert.log`
(macOS) or `~/.local/state/claude-alert.log` (Linux).

## Controls

Edit `~/.config/agent-harness/alert.conf` (`ENABLED`, `SOUND`, `VOICE`, `NOTIFY`, `POPUP`; 1 = on, 0 = off). The script
re-reads it on every alert, so no restart. "Mute alerts" = `ENABLED=0`; "no voice" = `VOICE=0`. `POPUP=1` adds a
focus-stealing dialog; never turn it on without asking.

## Test

1. Ask a one-line AskUserQuestion such as "Did you get the sound, voice and banner?". That fires the PreToolUse hook.
2. Nothing arrived: `tail` the log. No new line means the hook didn't fire: check `/hooks` and `/plugin` (is harness-core enabled?).
3. Logged but silent: check `alert.conf`, volume/mute and Focus mode. On macOS, `terminal-notifier` needs Notifications
   allowed in System Settings; exit code 3 + "Notifications are turned off" means it isn't allowed yet.
