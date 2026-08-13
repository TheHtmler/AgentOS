#!/usr/bin/env bash
# One-shot Mac mini deploy: git pull → install/migrate/build → restart launchd services.
#
# Usage (on Mac mini, from anywhere; resolves repo root):
#   ./scripts/macmini-deploy.sh              # api + web + ops
#   ./scripts/macmini-deploy.sh api          # Agent API only
#   ./scripts/macmini-deploy.sh web ops      # frontends only
#   ./scripts/macmini-deploy.sh --no-pull all
#   ./scripts/macmini-deploy.sh --migrate    # force alembic even if api skipped? (with api)
#
# Env overrides:
#   AGENTOS_API_LABEL=com.local.agentos-api
#   AGENTOS_WEB_LABEL=com.local.agentos-web
#   AGENTOS_OPS_LABEL=com.local.agentos-ops

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

API_LABEL="${AGENTOS_API_LABEL:-com.local.agentos-api}"
WEB_LABEL="${AGENTOS_WEB_LABEL:-com.local.agentos-web}"
OPS_LABEL="${AGENTOS_OPS_LABEL:-com.local.agentos-ops}"

DO_PULL=1
DO_API=0
DO_WEB=0
DO_OPS=0
WANT_MIGRATE=1

log() { printf '==> %s\n' "$*"; }
warn() { printf '!!  %s\n' "$*" >&2; }

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

parse_targets() {
  local t
  for t in "$@"; do
    case "$t" in
      all)
        DO_API=1
        DO_WEB=1
        DO_OPS=1
        ;;
      api) DO_API=1 ;;
      web) DO_WEB=1 ;;
      ops) DO_OPS=1 ;;
      frontends | fe)
        DO_WEB=1
        DO_OPS=1
        ;;
      *)
        echo "unknown target: $t" >&2
        usage
        exit 1
        ;;
    esac
  done
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --no-pull)
      DO_PULL=0
      shift
      ;;
    --no-migrate)
      WANT_MIGRATE=0
      shift
      ;;
    --migrate)
      WANT_MIGRATE=1
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#ARGS[@]} -eq 0 ]]; then
  parse_targets all
else
  parse_targets "${ARGS[@]}"
fi

service_loaded() {
  launchctl print "${DOMAIN}/$1" >/dev/null 2>&1
}

kick() {
  local label="$1"
  if service_loaded "$label"; then
    log "kickstart $label"
    launchctl kickstart -k "${DOMAIN}/${label}"
  else
    warn "launchd service missing: ${label}"
    warn "install with: ./scripts/install-launchd.sh ${label##com.local.agentos-}"
    return 1
  fi
}

http_code() {
  local url="$1"
  curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 8 "$url" 2>/dev/null || echo "down"
}

if [[ "$DO_PULL" -eq 1 ]]; then
  log "git pull --ff-only"
  git pull --ff-only
else
  log "skip git pull"
fi

if [[ "$DO_API" -eq 1 ]]; then
  log "uv sync (agent-api)"
  uv sync --directory "$ROOT/services/agent-api"
  if [[ "$WANT_MIGRATE" -eq 1 ]]; then
    log "alembic upgrade head"
    uv run --directory "$ROOT/services/agent-api" alembic upgrade head
  fi
  kick "$API_LABEL" || true
fi

if [[ "$DO_WEB" -eq 1 || "$DO_OPS" -eq 1 ]]; then
  log "pnpm install"
  # Non-interactive; avoid supply-chain prompt hangs on CI/Mac mini.
  CI=1 pnpm install
fi

if [[ "$DO_WEB" -eq 1 ]]; then
  log "build web"
  CI=1 pnpm --filter web build
  kick "$WEB_LABEL" || true
fi

if [[ "$DO_OPS" -eq 1 ]]; then
  log "clean ops .next (avoid stale prerender cache)"
  rm -rf "$ROOT/apps/ops/.next"
  log "build ops"
  CI=1 pnpm --filter ops build
  kick "$OPS_LABEL" || true
fi

sleep 2
log "health"
printf '  api  :8000/health  => %s\n' "$(http_code 'http://127.0.0.1:8000/health')"
printf '  web  :3000/        => %s\n' "$(http_code 'http://127.0.0.1:3000/')"
printf '  ops  :3001/login   => %s\n' "$(http_code 'http://127.0.0.1:3001/login')"

if [[ "$DO_OPS" -eq 1 ]]; then
  # Authenticated shell HTML is gated; verify built artifact contains tab nav marker.
  if ! rg -q "ops-tabs" "$ROOT/apps/ops/.next" 2>/dev/null; then
    warn "ops build artifact missing ops-tabs — shell nav may not have shipped"
  else
    log "ops build contains ops-tabs marker"
  fi
  # Local unauthenticated /knowledge should redirect to login (new shell), not serve old static list.
  know_code="$(http_code 'http://127.0.0.1:3001/knowledge')"
  printf '  ops  :3001/knowledge => %s (expect 307/308 to login)\n' "$know_code"
  if [[ "$know_code" == "200" ]]; then
    warn "local /knowledge returned 200 — still looks like a stale static page; check launchd WorkingDirectory / .next"
  fi
fi

log "public"
printf '  web  https://agentos.lemonbabycare.cn/\n'
printf '  ops  https://ops-agentos.lemonbabycare.cn/\n'
log "If public ops still shows old UI: purge 宝塔/nginx cache for ops-agentos, then hard-refresh."
log "done"
