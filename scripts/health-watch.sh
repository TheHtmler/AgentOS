#!/usr/bin/env bash
# HTTP-level health watchdog for AgentOS services.
#
# launchd KeepAlive only restarts a service after its process EXITS; a wedged
# process that still holds its port (uvicorn stuck, Next.js deadlocked) keeps
# the service "running" while every request times out. This watchdog probes
# each service's HTTP endpoint and kickstarts the launchd job after
# consecutive failures.
#
# Usage:
#   ./scripts/health-watch.sh             # check api + web + ops once
#   ./scripts/health-watch.sh api web     # specific services
#   ./scripts/health-watch.sh --once      # single pass, exit code reflects result
#
# Installed by ./scripts/install-launchd.sh health → runs every 60s.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
TIMEOUT="${HEALTH_TIMEOUT_SECONDS:-5}"
FAIL_LIMIT="${HEALTH_FAIL_LIMIT:-3}"
STATE_DIR="${TMPDIR:-/tmp}/agentos-health"

declare -A URLS=(
  [api]="http://127.0.0.1:8100/health"
  [web]="http://127.0.0.1:3000/api/health"
  [ops]="http://127.0.0.1:3001/api/ops/me"
)
declare -A LABELS=(
  [api]="com.local.agentos-api"
  [web]="com.local.agentos-web"
  [ops]="com.local.agentos-ops"
)

is_alive() {
  local url="$1"
  local code
  code="$(curl -s -o /dev/null -m "$TIMEOUT" -w '%{http_code}' "$url" 2>/dev/null || true)"
  # Any HTTP answer (< 500) proves the process is responsive; 401/404 are fine.
  [[ "$code" =~ ^[0-9]+$ ]] && [[ "$code" -lt 500 ]]
}

check_one() {
  local name="$1"
  local url="${URLS[$name]:-}"
  local label="${LABELS[$name]:-}"
  if [[ -z "$url" || -z "$label" ]]; then
    echo "unknown service: $name" >&2
    return 2
  fi
  mkdir -p "$STATE_DIR"
  local count_file="$STATE_DIR/$name.fails"
  local fails=0
  [[ -f "$count_file" ]] && fails="$(cat "$count_file" 2>/dev/null || echo 0)"

  if is_alive "$url"; then
    if [[ "$fails" -gt 0 ]]; then
      echo "==> $name recovered (was failing $fails×)"
      rm -f "$count_file"
    fi
    return 0
  fi

  fails=$((fails + 1))
  echo "$fails" >"$count_file"
  echo "!!  $name unhealthy ($url) — failure $fails/$FAIL_LIMIT"
  if [[ "$fails" -ge "$FAIL_LIMIT" ]]; then
    echo "==> restarting $label"
    launchctl kickstart -k "${DOMAIN}/${label}" 2>/dev/null \
      || launchctl kickstart "${DOMAIN}/${label}" 2>/dev/null \
      || echo "!!  kickstart failed for $label (service may be unloaded)"
    rm -f "$count_file"
    return 1
  fi
  return 1
}

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=(api web ops)
fi

failures=0
for t in "${targets[@]}"; do
  check_one "$t" || failures=$((failures + 1))
done
[[ "$failures" -eq 0 ]]
