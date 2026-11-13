#!/bin/bash
# Install SINCOR2 git hooks (pre-commit: secret scan + claim-blocklist scan + smoke suite).
# Run once per clone: bash scripts/setup-hooks.sh
# Worktrees share the main repo's hooks automatically.
set -e
REPO_ROOT=$(git rev-parse --show-toplevel)
HOOK_SRC="$REPO_ROOT/scripts/pre-commit-sincor"
HOOK_DST="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -f "$HOOK_SRC" ]; then
  echo "Hook source not found: $HOOK_SRC"
  exit 1
fi
cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"
echo "Installed pre-commit hook -> $HOOK_DST"
echo "Every commit now runs: secret scan + claim-blocklist scan + smoke suite."
