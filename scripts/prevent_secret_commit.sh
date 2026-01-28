#!/usr/bin/env bash
# Prevent committing files that contain obvious secret patterns in staged changes.
# Install by copying to .git/hooks/pre-commit (chmod +x) or run as CI check.

set -euo pipefail

# Patterns to detect: 0x + 64 hex (likely ETH private key), 'PRIVATE_KEY' or 'SECRET' literals
STAGED_FILES=$(git diff --cached --name-only | tr '\n' ' ')
if [ -z "$STAGED_FILES" ]; then
  exit 0
fi

FOUND=0
for f in $STAGED_FILES; do
  # only check text files
  if file "$f" | grep -q text; then
    # check for 0x + 64 hex
    if git show :"$f" | grep -En "0x[a-fA-F0-9]{64}" >/dev/null; then
      echo "❌ SECRET PATTERN (possible private key) found in staged file: $f"
      FOUND=1
    fi
    # check for PRIVATE_KEY or HISTORY_CLEANER_SECRET
    if git show :"$f" | grep -En "PRIVATE_KEY|HISTORY_CLEANER_SECRET|DEPLOYER_PRIVATE_KEY" >/dev/null; then
      echo "❌ Secret variable name found in staged file: $f"
      FOUND=1
    fi
  fi
done

if [ "$FOUND" -eq 1 ]; then
  echo "Aborting commit. Remove secrets from staged files or move them to environment variables (.env and .env.example are allowed)."
  exit 1
fi

exit 0
