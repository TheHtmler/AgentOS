#!/usr/bin/env bash
# Install/refresh ~/Library/LaunchAgents/com.local.agentos-ops.plist with real paths.
# Run on Mac mini from the AgentOS repo root (or any cwd; script resolves its own root).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.local.agentos-ops"
PLIST_SRC="$ROOT/infra/launchd/com.local.agentos-ops.plist.example"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -f "$PLIST_SRC" ]]; then
  echo "missing template: $PLIST_SRC" >&2
  exit 1
fi

NODE_BIN="$(command -v node || true)"
if [[ -z "$NODE_BIN" ]]; then
  for candidate in /opt/homebrew/bin/node /usr/local/bin/node "$HOME/.nvm/versions/node"/*/bin/node; do
    if [[ -x "$candidate" ]]; then
      NODE_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "${NODE_BIN:-}" || ! -x "$NODE_BIN" ]]; then
  echo "node not found; install Node 22+ or put it on PATH" >&2
  exit 1
fi

NEXT_BIN="$ROOT/apps/ops/node_modules/next/dist/bin/next"
if [[ ! -f "$NEXT_BIN" ]]; then
  echo "missing $NEXT_BIN — run: pnpm --filter ops install && pnpm --filter ops build" >&2
  exit 1
fi

mkdir -p "$(dirname "$PLIST_DST")"
# Escape for sed replacement
esc_root="${ROOT//\\/\\\\}"
esc_root="${esc_root//&/\\&}"
esc_node="${NODE_BIN//\\/\\\\}"
esc_node="${esc_node//&/\\&}"

sed \
  -e "s|__AGENTOS_ROOT__|${esc_root}|g" \
  -e "s|__NODE_BIN__|${esc_node}|g" \
  "$PLIST_SRC" >"$PLIST_DST"

uid="$(id -u)"
# Prefer modern bootstrap; fall back to load for older macOS.
if launchctl print "gui/${uid}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "gui/${uid}/${LABEL}" 2>/dev/null || true
fi
if launchctl bootstrap "gui/${uid}" "$PLIST_DST" 2>/dev/null; then
  :
else
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  launchctl load "$PLIST_DST"
fi

launchctl kickstart -k "gui/${uid}/${LABEL}"

echo "installed: $PLIST_DST"
echo "node:      $NODE_BIN"
echo "root:      $ROOT"
sleep 1
code="$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/login || true)"
echo "local /login => HTTP ${code:-down}"
if [[ "${code:-}" != "200" && "${code:-}" != "307" && "${code:-}" != "308" ]]; then
  echo "check logs: /tmp/agentos-ops.err.log /tmp/agentos-ops.out.log" >&2
  exit 1
fi
