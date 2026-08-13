#!/usr/bin/env bash
# Rebuild apps/ops and restart launchd service on Mac mini.
# Usage (from AgentOS repo root):
#   ./scripts/macmini-reload-ops.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

git pull --ff-only
pnpm --filter ops install
pnpm --filter ops build
launchctl kickstart -k "gui/$(id -u)/com.local.agentos-ops"

echo "ops reloaded: http://127.0.0.1:3001 (public: https://ops-agentos.lemonbabycare.cn)"
