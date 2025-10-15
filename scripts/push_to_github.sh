#!/usr/bin/env bash
set -euo pipefail

REPO_NAME=${1:-clinton-monetization-engine}
GH_USER=${2:-OrderofChaos33}

echo "Creating repo $GH_USER/$REPO_NAME (private by default)"
gh repo create "$GH_USER/$REPO_NAME" --private --confirm || true

git init
git branch -M main
git add .
git commit -m "chore: initial professionalized repo"
git remote add origin "https://github.com/$GH_USER/$REPO_NAME.git"
git push -u origin main

echo "Done. Visit: https://github.com/$GH_USER/$REPO_NAME"
