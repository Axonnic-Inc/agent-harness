#!/bin/bash
# Loud alert when a Claude Code session (or an astra board agent) is blocked on you. $1 = title.
# Sound + spoken line + a notification banner (terminal-notifier on macOS, notify-send on Linux).
# Hook JSON on stdin gives the detail line and the project name. Always exits 0: an alert must never block Claude.
#
# Settings: ${HARNESS_ALERT_CONF:-~/.config/agent-harness/alert.conf}, re-read on every alert (no restart).
#   ENABLED=1 SOUND=1 VOICE=1 NOTIFY=1 POPUP=0      1 = on, 0 = off. POPUP is a focus-stealing dialog (macOS).
ENABLED=1 SOUND=1 VOICE=1 NOTIFY=1 POPUP=0
conf=${HARNESS_ALERT_CONF:-$HOME/.config/agent-harness/alert.conf}
# shellcheck source=/dev/null
[ -f "$conf" ] && . "$conf"
in=$( [ -t 0 ] || cat )
[ "$ENABLED" = 1 ] || exit 0

field() { printf '%s' "$in" | jq -r "$1" 2>/dev/null; }
msg=$(field '.message // (if .tool_name then "Blocked: \(.tool_name)" else empty end)')
msg=${msg:-Needs you}
proj=$(basename "$(field '.cwd // empty')")
title="Claude: ${1:-needs you}"

if [ "$(uname)" = Darwin ]; then logdir=$HOME/Library/Logs; else logdir=${XDG_STATE_HOME:-$HOME/.local/state}; fi
mkdir -p "$logdir" && echo "$(date '+%F %T') ${1:-} | $proj | $msg" >> "$logdir/claude-alert.log"
msg=${msg//\"/\'}

if [ "$(uname)" = Darwin ]; then
  [ "$SOUND" = 1 ] && afplay /System/Library/Sounds/Sosumi.aiff >/dev/null 2>&1
  [ "$VOICE" = 1 ] && (say "Claude needs you in ${proj:-a session}" >/dev/null 2>&1 &)
  if [ "$NOTIFY" = 1 ] && command -v terminal-notifier >/dev/null; then
    (terminal-notifier -title "$title" -subtitle "$proj" -message "$msg" -group "claude-$proj" >/dev/null 2>&1 &)
  fi
  [ "$POPUP" = 1 ] && (osascript -e "display dialog \"$msg\" with title \"$title — $proj\" buttons {\"OK\"} default button 1 with icon caution giving up after 60" >/dev/null 2>&1 &)
else
  [ "$SOUND" = 1 ] && command -v paplay >/dev/null && (paplay /usr/share/sounds/freedesktop/stereo/bell.oga >/dev/null 2>&1 &)
  [ "$VOICE" = 1 ] && command -v spd-say >/dev/null && (spd-say "Claude needs you in ${proj:-a session}" >/dev/null 2>&1 &)
  [ "$NOTIFY" = 1 ] && command -v notify-send >/dev/null && (notify-send -u critical "$title" "$proj: $msg" >/dev/null 2>&1 &)
fi
exit 0
