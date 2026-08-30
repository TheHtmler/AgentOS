#!/usr/bin/env bash
# Install/refresh LaunchAgents for api / web / ops / sandbox / asr on Mac mini.
# Usage:
#   ./scripts/install-launchd.sh           # all
#   ./scripts/install-launchd.sh api web
#   ./scripts/install-launchd.sh ops
#   ./scripts/install-launchd.sh sandbox
#   ./scripts/install-launchd.sh asr

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

resolve_node() {
  local node_bin
  node_bin="$(command -v node || true)"
  if [[ -z "$node_bin" ]]; then
    for candidate in /opt/homebrew/bin/node /usr/local/bin/node "$HOME/.nvm/versions/node"/*/bin/node; do
      if [[ -x "${candidate:-}" ]]; then
        node_bin="$candidate"
        break
      fi
    done
  fi
  if [[ -z "${node_bin:-}" || ! -x "$node_bin" ]]; then
    echo "node not found on PATH" >&2
    exit 1
  fi
  printf '%s' "$node_bin"
}

resolve_uv() {
  local uv_bin
  uv_bin="$(command -v uv || true)"
  if [[ -z "$uv_bin" ]]; then
    for candidate in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv"; do
      if [[ -x "$candidate" ]]; then
        uv_bin="$candidate"
        break
      fi
    done
  fi
  if [[ -z "${uv_bin:-}" || ! -x "$uv_bin" ]]; then
    echo "uv not found on PATH" >&2
    exit 1
  fi
  printf '%s' "$uv_bin"
}

resolve_whisper_server() {
  local whisper_server_bin
  whisper_server_bin="$(command -v whisper-server || true)"
  if [[ -z "$whisper_server_bin" ]]; then
    echo "whisper-server not found on PATH; install with: brew install whisper-cpp" >&2
    exit 1
  fi
  printf '%s' "$whisper_server_bin"
}

escape_sed() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

install_one() {
  local target="$1"
  local label src dst node_bin uv_bin whisper_server_bin esc_root esc_node esc_uv esc_whisper_server

  case "$target" in
    api)
      label="com.local.agentos-api"
      src="$ROOT/infra/launchd/com.local.agentos-api.plist.example"
      ;;
    web)
      label="com.local.agentos-web"
      src="$ROOT/infra/launchd/com.local.agentos-web.plist.example"
      ;;
    ops)
      label="com.local.agentos-ops"
      src="$ROOT/infra/launchd/com.local.agentos-ops.plist.example"
      ;;
    sandbox)
      label="com.local.agentos-sandbox-manager"
      src="$ROOT/infra/launchd/com.local.agentos-sandbox-manager.plist.example"
      ;;
    asr)
      label="com.local.agentos-asr"
      src="$ROOT/infra/launchd/com.local.agentos-asr.plist.example"
      ;;
    *)
      echo "unknown target: $target (api|web|ops|sandbox|asr)" >&2
      exit 1
      ;;
  esac

  if [[ ! -f "$src" ]]; then
    echo "missing template: $src" >&2
    exit 1
  fi

  dst="${HOME}/Library/LaunchAgents/${label}.plist"
  mkdir -p "$(dirname "$dst")"
  if [[ "$target" == "asr" ]]; then
    mkdir -p /tmp/agentos-asr
  fi

  esc_root="$(escape_sed "$ROOT")"
  if [[ "$target" == "asr" ]]; then
    whisper_server_bin="$(resolve_whisper_server)"
    esc_whisper_server="$(escape_sed "$whisper_server_bin")"
  else
    node_bin="$(resolve_node)"
    uv_bin="$(resolve_uv)"
    esc_node="$(escape_sed "$node_bin")"
    esc_uv="$(escape_sed "$uv_bin")"
  fi

  sed \
    -e "s|__AGENTOS_ROOT__|${esc_root}|g" \
    -e "s|__NODE_BIN__|${esc_node}|g" \
    -e "s|__UV_BIN__|${esc_uv}|g" \
    -e "s|__WHISPER_SERVER_BIN__|${esc_whisper_server}|g" \
    "$src" >"$dst"

  if launchctl print "${DOMAIN}/${label}" >/dev/null 2>&1; then
    launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
  fi
  if ! launchctl bootstrap "$DOMAIN" "$dst" 2>/dev/null; then
    launchctl unload "$dst" 2>/dev/null || true
    launchctl load "$dst"
  fi
  launchctl kickstart -k "${DOMAIN}/${label}"
  echo "installed + started: $label"
  echo "  plist: $dst"
}

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=(api web ops)
fi

for t in "${targets[@]}"; do
  install_one "$t"
done
