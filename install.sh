#!/bin/bash
# agent-harness installer. Idempotent: run it again after `git pull` and nothing duplicates.
#
#   ./install.sh                     Codex side + Claude plugins harness-core and dev-mcp
#   ./install.sh --plugins all       also motion-design (or a comma list: harness-core,motion-design)
#   ./install.sh --merge-settings    deep-merge claude/settings.example.json into ~/.claude/settings.json (backed up)
#   ./install.sh --no-claude | --no-codex
#   ./install.sh --dry-run           print what would change, change nothing
#   ./install.sh --uninstall         remove links/registrations made by this script, restore backups
#
# Files are symlinked to this checkout, so the repo IS your harness: edit here, `git pull` to update.
# Anything replaced is kept next to it as <name>.pre-agent-harness.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
CODEX_HOME=${CODEX_HOME:-$HOME/.codex}
BIN=$HOME/.local/bin
CONF=$HOME/.config/agent-harness
PLUGINS=harness-core,dev-mcp
DRY=0 CLAUDE=1 CODEX=1 MERGE=0 UNINSTALL=0
BK=.pre-agent-harness

while [ $# -gt 0 ]; do
  case $1 in
    --dry-run) DRY=1 ;; --no-claude) CLAUDE=0 ;; --no-codex) CODEX=0 ;;
    --merge-settings) MERGE=1 ;; --uninstall) UNINSTALL=1 ;;
    --plugins) PLUGINS=$2; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1 (see --help)" >&2; exit 2 ;;
  esac; shift
done
[ "$PLUGINS" = all ] && PLUGINS=$(jq -r '[.plugins[].name] | join(",")' "$REPO/.claude-plugin/marketplace.json")

c_ok=$'\e[32m' c_dim=$'\e[2m' c_warn=$'\e[33m' c_off=$'\e[0m'
say()  { printf '%s\n' "${c_ok}==>${c_off} $*"; }
note() { printf '%s\n' "    ${c_dim}$*${c_off}"; }
warn() { printf '%s\n' "${c_warn}!!${c_off}  $*" >&2; }
run()  { if [ "$DRY" = 1 ]; then note "would run: $*"; else "$@"; fi; }
has()  { command -v "$1" >/dev/null 2>&1; }

# link TARGET LINK: point LINK at TARGET, keeping whatever was there as LINK.pre-agent-harness
link() {
  local target=$1 l=$2
  if [ -L "$l" ] && [ "$(readlink "$l")" = "$target" ]; then note "ok      ${l/#$HOME/~}"; return; fi
  run mkdir -p "$(dirname "$l")"
  if [ -e "$l" ] || [ -L "$l" ]; then
    if [ -L "$l" ]; then run rm "$l"; else run mv "$l" "$l$BK"; note "backup  ${l/#$HOME/~}$BK"; fi
  fi
  run ln -s "$target" "$l"; note "link    ${l/#$HOME/~} -> ${target/#$REPO/<repo>}"
}

# unlink LINK: remove LINK only if it points into this repo, then restore its backup
unlink_one() {
  local l=$1
  if [ -L "$l" ] && [[ "$(readlink "$l")" == "$REPO"/* ]]; then run rm "$l"; note "removed ${l/#$HOME/~}"; fi
  if [ -e "$l$BK" ]; then run mv "$l$BK" "$l"; note "restored ${l/#$HOME/~}"; fi
}

LINKS=(
  "$REPO/harness/astra.py|$CODEX_HOME/harness/astra.py"
  "$REPO/harness/astra_ext.py|$CODEX_HOME/harness/astra_ext.py"
  "$REPO/harness/prices.json|$CODEX_HOME/harness/prices.json"
  "$REPO/plugins/harness-core/mcp/board.py|$CODEX_HOME/harness/board.py"
  "$REPO/codex/skills/compete|$CODEX_HOME/skills/compete"
  "$REPO/harness/astra.py|$BIN/astra"
  "$REPO/plugins/harness-core/bin/with-secrets|$BIN/with-secrets"
  "$REPO/plugins/harness-core/scripts/alert.sh|$BIN/harness-alert"
)

# ------------------------------------------------------------------ uninstall
if [ "$UNINSTALL" = 1 ]; then
  say "Removing links"
  for pair in "${LINKS[@]}"; do unlink_one "${pair#*|}"; done
  codex_board=$( { has codex && codex mcp get agent-board 2>/dev/null; } || true)
  if [ "$CODEX" = 1 ] && [[ "$codex_board" == *"$CODEX_HOME/harness/board.py"* ]]; then
    say "Codex: removing agent-board MCP"; run codex mcp remove agent-board
  fi
  if [ "$CLAUDE" = 1 ] && has claude; then
    say "Claude: removing plugins and marketplace"
    for p in $(jq -r '.plugins[].name' "$REPO/.claude-plugin/marketplace.json"); do
      run claude plugin uninstall "$p@agent-harness" >/dev/null 2>&1 || true
    done
    run claude plugin marketplace remove agent-harness >/dev/null 2>&1 || true
  fi
  say "Done. Secrets in the keychain are untouched (with-secrets --list / --rm NAME)."
  exit 0
fi

# ------------------------------------------------------------------ preflight
say "Preflight"
missing=0
for t in git python3 jq; do has "$t" || { warn "$t is required"; missing=1; }; done
[ "$missing" = 0 ] || { warn "install the missing tools (macOS: brew bundle) and re-run"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { warn "python3 >= 3.9 required"; exit 1; }
has node || warn "node/npx not found: dev-mcp servers and some skills need it"
[ "$(uname)" = Darwin ] && ! has terminal-notifier && warn "terminal-notifier not found: alerts will be sound/voice only (brew install terminal-notifier)"
note "repo $REPO"; [ "$DRY" = 1 ] && note "dry run: nothing will change"

# ------------------------------------------------------------------ shared pieces
say "Links (astra, agent board, with-secrets, alert)"
for pair in "${LINKS[@]}"; do
  if [ "$CODEX" = 0 ] && [[ "${pair#*|}" == "$CODEX_HOME"/* ]]; then continue; fi
  link "${pair%%|*}" "${pair#*|}"
done
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on PATH: add  export PATH=\"\$HOME/.local/bin:\$PATH\"  to your shell profile" ;; esac

if [ ! -f "$CONF/alert.conf" ]; then
  say "Alert settings -> ${CONF/#$HOME/~}/alert.conf"
  run mkdir -p "$CONF"
  [ "$DRY" = 1 ] || printf '# Read by harness-alert on every alert. 1 = on, 0 = off.\nENABLED=1\nSOUND=1\nVOICE=1\nNOTIFY=1\nPOPUP=0\n' > "$CONF/alert.conf"
fi

# ------------------------------------------------------------------ Codex
if [ "$CODEX" = 1 ]; then
  say "Codex"
  if [ ! -f "$CODEX_HOME/AGENTS.md" ]; then
    run cp "$REPO/codex/AGENTS.md" "$CODEX_HOME/AGENTS.md"; note "wrote   ~/.codex/AGENTS.md"
  elif ! cmp -s "$REPO/codex/AGENTS.md" "$CODEX_HOME/AGENTS.md"; then
    note "kept    your ~/.codex/AGENTS.md (compare: diff ~/.codex/AGENTS.md $REPO/codex/AGENTS.md)"
  fi
  if ! has codex; then
    warn "codex CLI not found: skipped MCP registration (brew install --cask codex, then re-run)"
  elif codex mcp get agent-board >/dev/null 2>&1; then
    note "ok      agent-board MCP already registered"
  else
    run codex mcp add agent-board --env BOARD_AGENT=codex --env BOARD_ROLE=agent \
      --env "BOARD_DB=$HOME/.agent-board/board.sqlite" -- python3 "$CODEX_HOME/harness/board.py"
    note "added   agent-board MCP"
  fi
fi

# ------------------------------------------------------------------ Claude Code
if [ "$CLAUDE" = 1 ]; then
  say "Claude Code plugins ($PLUGINS)"
  if ! has claude; then
    warn "claude CLI not found: skipped (brew install --cask claude-code, then re-run)"
  else
    if claude plugin marketplace list 2>/dev/null | grep -q 'agent-harness'; then
      run claude plugin marketplace update agent-harness >/dev/null 2>&1 || true; note "ok      marketplace agent-harness"
    else
      run claude plugin marketplace add "$REPO"; note "added   marketplace agent-harness"
    fi
    IFS=, read -r -a want <<< "$PLUGINS"
    for p in "${want[@]}"; do run claude plugin install "$p@agent-harness" --scope user >/dev/null && note "plugin  $p"; done

    # Duplicates from a pre-plugin setup would double-fire alerts or register the board twice.
    s=$HOME/.claude/settings.json
    if [ -f "$s" ] && jq -e '[.hooks // {} | .. | .command? // empty | select(test("alert\\.sh"))] | length > 0' "$s" >/dev/null 2>&1; then
      warn "$s has its own alert.sh hooks: remove them or alerts fire twice (jq .hooks $s)"
    fi
    if [ -f "$HOME/.claude.json" ] && jq -e '.mcpServers["agent-board"]' "$HOME/.claude.json" >/dev/null 2>&1; then
      warn "user-scope agent-board MCP already exists: claude mcp remove agent-board -s user  (the plugin provides it)"
    fi
  fi
fi

if [ "$MERGE" = 1 ]; then
  s=$HOME/.claude/settings.json
  say "Merging claude/settings.example.json into ${s/#$HOME/~}"
  if [ "$DRY" = 1 ]; then note "would merge (arrays are unioned, your values win on conflicts)"
  else
    mkdir -p "$(dirname "$s")"; [ -f "$s" ] || echo '{}' > "$s"
    cp "$s" "$s$BK.$(date +%s)"
    # recursive merge: objects merge, arrays union, scalars keep YOUR value
    jq -s 'def merge($a; $b): if ($a|type) == "object" and ($b|type) == "object"
             then reduce (($a + $b) | keys_unsorted[]) as $k ({}; .[$k] = merge($a[$k]; $b[$k]))
             elif ($a|type) == "array" and ($b|type) == "array" then ($a + $b | unique)
             elif $b == null then $a else $b end;
           merge(.[1] | del(._comment, ."$schema"); .[0])' "$s" "$REPO/claude/settings.example.json" > "$s.tmp"
    mv "$s.tmp" "$s"; note "merged (backup: $s$BK.*)"
  fi
fi

say "Done."
cat <<EOF
    Next:
      - store any MCP tokens:      with-secrets --set SUPABASE_ACCESS_TOKEN
      - restart Claude Code / Codex so they load the plugins and the agent board
      - try it:                    astra --help     |   ask Claude: "test my blocked alerts"
EOF
