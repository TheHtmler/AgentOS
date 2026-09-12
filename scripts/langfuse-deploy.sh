#!/usr/bin/env bash
# Enable/update the AgentOS Langfuse integration on the Mac mini.
#
# Usage:
#   ./scripts/langfuse-deploy.sh              # pull, sync, restart API, verify
#   ./scripts/langfuse-deploy.sh --no-pull    # use the current checkout
#   ./scripts/langfuse-deploy.sh --check-only # validate config without restart
#
# Langfuse itself is hosted by Langfuse Cloud in this phase. This script does
# not start a self-hosted ClickHouse/Redis/Postgres stack on the Mac mini.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${AGENTOS_API_ENV_FILE:-$ROOT/services/agent-api/.env}"
API_PORT="${AGENTOS_API_PORT:-8100}"
DO_PULL=1
CHECK_ONLY=0

log() { printf '==> %s\n' "$*"; }
die() { printf '!!  %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-pull) DO_PULL=0; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -f "$ENV_FILE" ]] || die "missing Agent API env file: $ENV_FILE"

# Read simple KEY=value lines without sourcing the file (the env contains secrets
# and must never be executed as shell code).
env_value() {
  local key="$1"
  sed -n "s/^[[:space:]]*${key}=//p" "$ENV_FILE" | tail -1 \
    | sed 's/[[:space:]]*#.*$//' | sed 's/^['\''"]//; s/['\''"]$//' | tr -d '\r'
}

enabled="$(env_value LANGFUSE_ENABLED)"
public_key="$(env_value LANGFUSE_PUBLIC_KEY)"
secret_key="$(env_value LANGFUSE_SECRET_KEY)"
base_url="$(env_value LANGFUSE_BASE_URL)"

enabled_normalized="$(printf '%s' "$enabled" | tr '[:upper:]' '[:lower:]')"
case "$enabled_normalized" in
  true|1|yes) ;;
  *) die "LANGFUSE_ENABLED is not true in $ENV_FILE" ;;
esac
[[ "$public_key" == pk-lf-* ]] || die "LANGFUSE_PUBLIC_KEY is missing or invalid"
[[ "$secret_key" == sk-lf-* ]] || die "LANGFUSE_SECRET_KEY is missing or invalid"
[[ "$base_url" == http://* || "$base_url" == https://* ]] || die "LANGFUSE_BASE_URL must be an HTTP(S) URL"

log "Langfuse configuration validated (content capture is not displayed)"
log "endpoint: $base_url"

if (( CHECK_ONLY )); then
  log "check-only complete"
  exit 0
fi

if (( DO_PULL )); then
  log "update checkout and Agent API dependencies"
  "$ROOT/scripts/macmini-deploy.sh" api
else
  log "update Agent API without pulling"
  "$ROOT/scripts/macmini-deploy.sh" --no-pull api
fi

log "verify Agent API health"
code="$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' \
  --connect-timeout 3 --max-time 8 "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || true)"
[[ "$code" == "200" ]] || die "Agent API is not healthy (HTTP ${code:-down}); inspect /tmp/agentos-api.err.log"

log "Langfuse integration deployed; inspect the Langfuse project for the next completed run"
