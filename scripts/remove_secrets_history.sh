#!/usr/bin/env bash
# remove_secrets_history.sh
# Helper to remove sensitive strings from git history using git-filter-repo.
# WARNING: This rewrites repository history. You MUST coordinate with collaborators
# and rotate any keys that were exposed in commits after running this.

set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "git not found; aborting"
  exit 1
fi

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "git-filter-repo not found. Install it first. Example: pip install git-filter-repo"
  exit 1
fi

if [ -z "${SECRET_TO_REMOVE-}" ]; then
  echo "Please set SECRET_TO_REMOVE environment variable to the string you want to remove. Example:"
  echo "  export SECRET_TO_REMOVE='0xYOUR_SECRET_HERE'"
  exit 1
fi

echo "Preparing to remove secret from git history (this will rewrite commits)."
read -p "Are you sure you want to continue? Type 'YES' to proceed: " confirm
if [ "$confirm" != "YES" ]; then
  echo "Aborting. No changes made."; exit 1
fi

TMPFILE=$(mktemp)
echo "$SECRET_TO_REMOVE==> [REDACTED_SECRET]" > "$TMPFILE"

# Run git-filter-repo with replace-text
git filter-repo --replace-text "$TMPFILE"
rm -f "$TMPFILE"

echo "Rewrite complete. Review the repo, then force-push to remote:"
echo "  git push --force --all"
echo "  git push --force --tags"

echo "IMPORTANT: Rotate any credentials that were present in history immediately."