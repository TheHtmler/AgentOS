#!/usr/bin/env bash
# Thin wrapper — prefer ./scripts/install-launchd.sh
exec "$(cd "$(dirname "$0")" && pwd)/install-launchd.sh" ops "$@"
