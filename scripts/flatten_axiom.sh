#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../onchain"
mkdir -p /tmp
if command -v forge >/dev/null 2>&1; then
  forge flatten src/Axiom.sol > /tmp/Axiom.flattened.sol
  echo "wrote /tmp/Axiom.flattened.sol"
  echo "Next: forge verify-contract --chain-id 8453 0x4c3fb66f14fbaa2088c9ae91017ba770da53715a src/Axiom.sol:Axiom"
else
  echo "forge not installed — cannot flatten"
  exit 2
fi
