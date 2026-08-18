#!/usr/bin/env bash
# Install the repo's pre-commit/pre-push git hooks.
# Usage: ./scripts/install-git-hooks.sh
# Run once after cloning (and again if scripts/git-hooks/* changes).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_SRC="$ROOT/scripts/git-hooks"
HOOKS_DST="$ROOT/.git/hooks"

if [[ ! -d "$HOOKS_DST" ]]; then
  echo "not a git checkout (missing .git/hooks): $HOOKS_DST" >&2
  exit 1
fi

for hook in pre-commit pre-push; do
  src="$HOOKS_SRC/$hook"
  dst="$HOOKS_DST/$hook"
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    echo "skipping $hook: $dst already exists and is not a symlink we manage" >&2
    continue
  fi
  ln -sf "$src" "$dst"
  chmod +x "$src"
  echo "installed: $hook"
done
