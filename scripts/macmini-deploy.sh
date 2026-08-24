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
#   AGENTOS_API_PORT=8100   # agent-api moved off :8000 (OCR service owns it)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

API_LABEL="${AGENTOS_API_LABEL:-com.local.agentos-api}"
WEB_LABEL="${AGENTOS_WEB_LABEL:-com.local.agentos-web}"
OPS_LABEL="${AGENTOS_OPS_LABEL:-com.local.agentos-ops}"
WEB_PORT="${AGENTOS_WEB_PORT:-3000}"
OPS_PORT="${AGENTOS_OPS_PORT:-3001}"

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

start_service() {
  local label="$1"
  local plist="${HOME}/Library/LaunchAgents/${label}.plist"
  if service_loaded "$label"; then
    kick "$label" || true
    return
  fi
  if [[ ! -f "$plist" ]]; then
    warn "launchd plist missing: ${plist}"
    warn "install with: ./scripts/install-launchd.sh ${label##com.local.agentos-}"
    return 1
  fi
  log "bootstrap $label"
  launchctl bootstrap "$DOMAIN" "$plist"
}

# Build first, swap after: the live `next start` keeps serving the old build while
# the new one compiles into `.next.new` (NEXT_DIST_DIR); downtime is just the
# bootout → swap → start window (seconds) instead of the whole build (minutes).
# A failed build leaves the running old build completely untouched.
#
# bootout is asynchronous: the old process may hold the listen socket (and the
# service registry entry) for seconds — longer with open SSE connections. Starting
# the new instance before the port frees crashes it on EADDRINUSE, and launchd's
# KeepAlive throttle then stretches the outage (the old "502 until the next
# deploy" bug). Always wait for the registry entry AND the port to go away.
wait_port_free() {
  local port="$1" label="$2" tries=60
  while ((tries-- > 0)); do
    if ! service_loaded "$label" && ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  warn "port $port still busy after bootout of $label — continuing anyway"
}

wait_http_ready() {
  local url="$1" name="$2" tries=60 code
  while ((tries-- > 0)); do
    code="$(http_code "$url")"
    if [[ "$code" != "down" && "$code" != "000" ]]; then
      return 0
    fi
    sleep 0.5
  done
  warn "$name did not come up at $url after restart — check launchd logs (/tmp/agentos-${name}.out.log)"
  return 1
}

deploy_frontend() {
  local name="$1" label="$2" cache_mode="$3" port="$4"
  local app_dir="$ROOT/apps/$name"
  local next_dir="$app_dir/.next"
  local new_dir="$app_dir/.next.new"
  local prev_dir="$app_dir/.next.prev"

  rm -rf "$new_dir"
  if [[ "$cache_mode" == "reuse" && -d "$next_dir/cache" ]]; then
    mkdir -p "$new_dir"
    cp -a "$next_dir/cache" "$new_dir/cache"
  fi

  log "build $name (service keeps serving the old build until the swap)"
  if ! CI=1 NEXT_DIST_DIR=.next.new pnpm --filter "$name" build; then
    warn "$name build failed — running build untouched"
    rm -rf "$new_dir"
    exit 1
  fi

  if service_loaded "$label"; then
    log "stop $label for the .next swap"
    launchctl bootout "${DOMAIN}/${label}"
    wait_port_free "$port" "$label"
  fi
  rm -rf "$prev_dir"
  if [[ -d "$next_dir" ]]; then
    mv "$next_dir" "$prev_dir"
  fi
  mv "$new_dir" "$next_dir"
  if start_service "$label"; then
    rm -rf "$prev_dir"
    wait_http_ready "http://127.0.0.1:${port}/" "$name" || true
  else
    warn "$name failed to start after swap; previous build kept at $prev_dir"
  fi
}

http_code() {
  local url="$1"
  curl -sS --noproxy '*' -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 8 "$url" 2>/dev/null || echo "down"
}

if [[ "$DO_PULL" -eq 1 ]]; then
  # Avoid bare `git pull --ff-only`: with multi-merge / multi-ref pull.* config
  # Git errors with "Cannot fast-forward to multiple branches."
  branch="$(git rev-parse --abbrev-ref HEAD)"
  remote="$(git config --get "branch.${branch}.remote" 2>/dev/null || echo origin)"
  log "git fetch ${remote} ${branch} && merge --ff-only"
  git fetch "$remote" "$branch"
  git merge --ff-only "${remote}/${branch}"
else
  log "skip git pull"
fi

ensure_upload_root() {
  local upload_root="${UPLOAD_ROOT:-}"
  local env_file="$ROOT/services/agent-api/.env"
  if [[ -z "$upload_root" && -f "$env_file" ]]; then
    upload_root="$(grep -E '^[[:space:]]*UPLOAD_ROOT=' "$env_file" | tail -1 | sed 's/^[^=]*=//' | tr -d ' "'\''"')"
  fi
  upload_root="${upload_root:-$ROOT/services/agent-api/data/uploads}"
  if [[ "$upload_root" != /* ]]; then
    upload_root="$ROOT/$upload_root"
  fi
  log "mkdir -p UPLOAD_ROOT ($upload_root)"
  mkdir -p "$upload_root"
}

if [[ "$DO_API" -eq 1 ]]; then
  log "uv sync (agent-api)"
  uv sync --directory "$ROOT/services/agent-api"
  if [[ "$WANT_MIGRATE" -eq 1 ]]; then
    log "alembic upgrade head"
    uv run --directory "$ROOT/services/agent-api" alembic upgrade head
  fi
  # Idempotent upserts: refresh built-in agents (incl. prompt overlays) every deploy.
  log "seed agents"
  uv run --directory "$ROOT/services/agent-api" python scripts/seed_agents.py
  # Core knowledge doc is seeded only when absent so Ops-side edits are not clobbered;
  # after changing seed/knowledge/mma_pa_chunks.json, re-run scripts/seed_knowledge.py manually.
  log "seed core knowledge (skip when present)"
  if ! uv run --directory "$ROOT/services/agent-api" python -c "
import asyncio, sys
from sqlalchemy import select
from agent_api.db.models import KnowledgeDocument
from agent_api.db.session import close_database, session_factory

async def main() -> int:
    async with session_factory() as session:
        doc = await session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.slug == 'mma-pa-core-v1')
        )
    await close_database()
    return 0 if doc is not None else 1

sys.exit(asyncio.run(main()))
"; then
    uv run --directory "$ROOT/services/agent-api" python scripts/seed_knowledge.py ||
      warn "knowledge seed failed (Ollama embedding down?) — run scripts/seed_knowledge.py manually"
  else
    log "core knowledge doc already present, skip"
  fi
  ensure_upload_root
  kick "$API_LABEL" || true
fi

if [[ "$DO_WEB" -eq 1 || "$DO_OPS" -eq 1 ]]; then
  log "pnpm install"
  # Non-interactive; avoid supply-chain prompt hangs on CI/Mac mini.
  CI=1 pnpm install
fi

if [[ "$DO_WEB" -eq 1 ]]; then
  deploy_frontend web "$WEB_LABEL" reuse "$WEB_PORT"
fi

if [[ "$DO_OPS" -eq 1 ]]; then
  # ops: clean rebuild (no cache carry-over) — stale prerender cache has bitten before.
  deploy_frontend ops "$OPS_LABEL" clean "$OPS_PORT"
fi

sleep 2
log "health"
API_PORT="${AGENTOS_API_PORT:-8100}"
printf '  api  :%s/health  => %s\n' "$API_PORT" "$(http_code "http://127.0.0.1:${API_PORT}/health")"
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
log "If a browser tab still shows old UI after a deploy: hard-refresh (Cmd+Shift+R)."

if [[ -n "${OCR_BASE_URL:-}" ]]; then
  ocr_health="${OCR_BASE_URL%/}/health"
  printf '  ocr  %s => %s\n' "$ocr_health" "$(http_code "$ocr_health")"
else
  log "OCR: set OCR_BASE_URL in agent-api .env for Ops PDF import + chat report uploads; verify with: curl \$OCR_BASE_URL/health"
fi

log "done"
