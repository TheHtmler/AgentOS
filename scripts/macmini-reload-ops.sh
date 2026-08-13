#!/usr/bin/env bash
# Thin wrapper — prefer ./scripts/macmini-deploy.sh
exec "$(cd "$(dirname "$0")" && pwd)/macmini-deploy.sh" ops "$@"
