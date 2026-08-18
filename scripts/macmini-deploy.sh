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

# Rebuilding `.next` under a live `next start` is broken: the old process renders
# HTML from its in-memory build while `/_next/static/*` is read from the deleted/
# replaced dir on disk, so every CSS/JS asset 404s (the "unstyled login page").
# Stop the service first, build, then start; keep the old build for rollback.
deploy_frontend() {
  local name="$1" label="$2" cache_mode="$3"
  local app_dir="$ROOT/apps/$name"
  local next_dir="$app_dir/.next"
  local prev_dir="$app_dir/.next.prev"

  if service_loaded "$label"; then
    log "stop $label while .next is rebuilt"
    launchctl bootout "${DOMAIN}/${label}"
  fi

  rm -rf "$prev_dir"
  if [[ -d "$next_dir" ]]; then
    mv "$next_dir" "$prev_dir"
  fi
  if [[ "$cache_mode" == "reuse" && -d "$prev_dir/cache" ]]; then
    mkdir -p "$next_dir"
    mv "$prev_dir/cache" "$next_dir/cache"
  fi

  log "build $name"
  if ! CI=1 pnpm --filter "$name" build; then
    warn "$name build failed — restoring previous .next"
    rm -rf "$next_dir"
    if [[ -d "$prev_dir" ]]; then
      mv "$prev_dir" "$next_dir"
    fi
    start_service "$label" || true
    exit 1
  fi

  rm -rf "$prev_dir"
  start_service "$label" || true
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
  deploy_frontend web "$WEB_LABEL" reuse
fi

if [[ "$DO_OPS" -eq 1 ]]; then
  # ops: clean rebuild (no cache carry-over) — stale prerender cache has bitten before.
  deploy_frontend ops "$OPS_LABEL" clean
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
log "If public ops still shows old UI: purge 宝塔/nginx cache for ops-agentos, then hard-refresh."

if [[ -n "${OCR_BASE_URL:-}" ]]; then
  ocr_health="${OCR_BASE_URL%/}/health"
  printf '  ocr  %s => %s\n' "$ocr_health" "$(http_code "$ocr_health")"
else
  log "OCR: set OCR_BASE_URL in agent-api .env for Ops PDF import + chat report uploads; verify with: curl \$OCR_BASE_URL/health"
fi

log "done"
