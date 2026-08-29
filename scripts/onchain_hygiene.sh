#!/usr/bin/env bash
# P0-6: local on-chain compile/test + Slither. Never sets EXECUTE_LIVE.
# GitHub Actions billing is locked (ci.yml / onchain-ci.yml stay workflow_dispatch).
# Run this on a machine with Foundry. Exits 0 if tools are missing (signal, not theater).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/onchain"

if ! command -v forge >/dev/null 2>&1; then
  echo "forge not installed — skip. Install Foundry: https://book.getfoundry.sh/"
  exit 0
fi

if [ ! -d lib/forge-std ] && [ ! -d lib/v4-core ]; then
  echo "onchain/lib not vendored — attempting pinned forge install"
  forge install foundry-rs/forge-std@v1.9.7 --no-commit || true
  forge install OpenZeppelin/openzeppelin-contracts@v5.5.0 --no-commit || true
fi

echo "== forge build =="
forge build --sizes

echo "== forge test (no IntegrationTest) =="
forge test -vvv --no-match-contract IntegrationTest

if command -v slither >/dev/null 2>&1; then
  echo "== slither =="
  slither . --config-file slither.config.json
else
  echo "slither not installed — skip (pip install slither-analyzer)"
fi

echo "onchain hygiene OK"
