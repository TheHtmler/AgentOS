#!/usr/bin/env bash
# Daily PostgreSQL backup for AgentOS with rotation + integrity check.
#
# Usage:
#   ./scripts/backup-postgres.sh            # one backup now
#   ./scripts/backup-postgres.sh --dry-run  # print what would run, change nothing
#
# Env overrides:
#   BACKUP_DIR       default: $ROOT/data/backups/postgres  (data/ is gitignored)
#   BACKUP_KEEP      default: 14  (backups kept, oldest deleted)
#   POSTGRES_CONTAINER  default: agentos-postgres (docker compose service name)
#
# Install as a daily launchd job:
#   ./scripts/install-launchd.sh backup
# (writes ~/Library/LaunchAgents/com.local.agentos-backup.plist, runs 03:30)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/infra/postgres/.env"
CONTAINER="${POSTGRES_CONTAINER:-agentos-postgres}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/data/backups/postgres}"
KEEP="${BACKUP_KEEP:-14}"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

log() { printf '==> %s\n' "$*"; }
warn() { printf '!!  %s\n' "$*" >&2; }

if [[ "$DRY_RUN" -eq 0 && ! -f "$ENV_FILE" ]]; then
  warn "missing $ENV_FILE (copy infra/postgres/.env.example)"
  exit 1
fi

# Load POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_PORT.
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

: "${POSTGRES_DB:?POSTGRES_DB must be set (see infra/postgres/.env)}"
: "${POSTGRES_USER:?POSTGRES_USER must be set (see infra/postgres/.env)}"

if ! command -v docker >/dev/null 2>&1; then
  warn "docker not found on PATH"
  exit 1
fi

# The dump runs inside the Postgres container, so no host port is needed and
# the password never appears in a process list here.
run_pg_dump() {
  docker compose --env-file "$ENV_FILE" -f "$ROOT/infra/postgres/compose.yaml" \
    exec -T "$CONTAINER" \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run: would dump $POSTGRES_DB to $BACKUP_DIR and keep $KEEP backups"
  exit 0
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  warn "container $CONTAINER is not running; skipping backup"
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/agentos-$STAMP.sql.gz"
TMP="$OUT.tmp"

log "dumping $POSTGRES_DB -> $OUT"
if run_pg_dump | gzip >"$TMP"; then
  # Integrity: the gzip stream must decode and the dump must look like a dump.
  if ! gzip -t "$TMP"; then
    warn "gzip integrity check failed; removing corrupt backup"
    rm -f "$TMP"
    exit 1
  fi
  if ! gunzip -c "$TMP" | grep -q "PostgreSQL database dump"; then
    warn "dump does not look like a PostgreSQL dump; removing it"
    rm -f "$TMP"
    exit 1
  fi
  mv "$TMP" "$OUT"
else
  warn "pg_dump failed; keeping no partial file"
  rm -f "$TMP"
  exit 1
fi

SIZE="$(du -h "$OUT" | cut -f1)"
log "backup ok: $OUT ($SIZE)"

# Rotation: keep the newest $KEEP, delete the rest.
COUNT="$(find "$BACKUP_DIR" -name 'agentos-*.sql.gz' | wc -l | tr -d ' ')"
if [[ "$COUNT" -gt "$KEEP" ]]; then
  find "$BACKUP_DIR" -name 'agentos-*.sql.gz' -print0 \
    | sort -z \
    | head -z -n "$((COUNT - KEEP))" \
    | xargs -0 -r rm --
  log "rotated: kept newest $KEEP of $COUNT backups"
fi
