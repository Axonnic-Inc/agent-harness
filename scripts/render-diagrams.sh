#!/bin/bash
# Render docs/diagrams/*.mmd to light + dark PNGs (transparent background, 2x) with mermaid-cli.
# Pages embed them with <picture>, so they look the same on GitHub web, GitHub mobile, MkDocs and editors,
# without depending on GitHub's live Mermaid iframe. Re-run after editing any .mmd file.
#   scripts/render-diagrams.sh            render all
#   scripts/render-diagrams.sh --check    render to a temp dir only (CI: fails on a syntax error)
set -euo pipefail
cd "$(dirname "$0")/../docs/diagrams"
out=.; [ "${1:-}" = --check ] && out=$(mktemp -d)
cfg=$(mktemp -d)
font="-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
printf '{"theme":"default","flowchart":{"wrappingWidth":280},"themeVariables":{"fontFamily":"%s"}}' "$font" > "$cfg/light.json"
printf '{"theme":"dark","flowchart":{"wrappingWidth":280},"themeVariables":{"fontFamily":"%s"}}' "$font" > "$cfg/dark.json"
pp=(); [ -n "${CI:-}" ] && { echo '{"args":["--no-sandbox"]}' > "$cfg/pptr.json"; pp=(-p "$cfg/pptr.json"); }
for f in *.mmd; do
  n=${f%.mmd}
  for t in light dark; do
    npx -y @mermaid-js/mermaid-cli@11 ${pp[@]+"${pp[@]}"} -q -i "$f" -o "$out/$n-$t.png" -c "$cfg/$t.json" -b transparent -s 2 -w 1100
  done
  echo "rendered $n"
done
