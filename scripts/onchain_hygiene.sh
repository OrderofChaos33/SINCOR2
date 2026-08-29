#!/usr/bin/env bash
# P0-6: local on-chain compile/test + Slither. Never sets EXECUTE_LIVE.
# GitHub Actions billing is locked (ci.yml / onchain-ci.yml stay workflow_dispatch).
# Missing tools → skip that step with a signal, not a fake green.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/onchain"

if ! command -v forge >/dev/null 2>&1; then
  echo "forge not installed — skip. Install Foundry: https://book.getfoundry.sh/"
  exit 0
fi

install_pinned() {
  # Exact pins from DEPLOYING.md / .github/workflows/onchain-ci.yml
  forge install foundry-rs/forge-std@v1.9.7 --no-commit
  forge install OpenZeppelin/openzeppelin-contracts@v5.5.0 --no-commit
  forge install Uniswap/v4-core@d153b048868a60c2403a3ef5b2301bb247884d46 --no-commit
  forge install Uniswap/v4-periphery@7ebd04b161745b75ed0c24ba2df3bc7c25f65606 --no-commit
  forge install Uniswap/permit2@cc56ad0f3439c502c246fc5cfcc3db92bb8b7219 --no-commit
  forge install OpenZeppelin/uniswap-hooks@26dc8e53f812a1ca390d470342adb6cd8c3286ad --no-commit
}

if [ ! -d lib/forge-std ] || [ ! -d lib/v4-core ] || [ ! -d lib/openzeppelin-contracts ]; then
  echo "onchain/lib incomplete — installing pinned Foundry deps"
  install_pinned
fi

export BASE_RPC_URL="${BASE_RPC_URL:-https://base-rpc.publicnode.com}"

echo "== forge build =="
forge build --sizes

echo "== forge test unit =="
# Graduation + LimitOrderHook suites fork Base in setUp even without "Fork" in the name.
# Invariant + IntegrationTest + *Fork* are opt-in (need RPC / longer runtime).
forge test -vvv \
  --no-match-contract "ForkTest|Fork|IntegrationTest|GraduationTest|SincLimitOrderHookTest|SincLimitOrderHookAntiSandwichTest" \
  --no-match-path "test/invariant/*"

if ! command -v slither >/dev/null 2>&1; then
  echo "slither not installed — skip (pip install slither-analyzer)"
  echo "onchain hygiene OK (forge only)"
  exit 0
fi

echo "== slither (Foundry 1.8 build-info shim) =="
# crytic-compile 0.4.2 expects Hardhat-style *.output.json with an "output" key.
# Foundry 1.8 also writes a small index JSON without "output" — drop it.
# dynamic_test_linking must stay false in foundry.toml or foundry-pp/* virtual
# sources appear and Slither 0.11.6 cannot parse them.
forge build --build-info --deny never --skip ./test/** ./script/** --force
python3 - <<'PY'
import json
from pathlib import Path
bi = Path("out/build-info")
if not bi.is_dir():
    raise SystemExit("no out/build-info after forge build --build-info")
for p in list(bi.iterdir()):
    if not p.name.endswith(".json"):
        continue
    if p.name.endswith(".output.json"):
        p.unlink()
        continue
    data = json.loads(p.read_text())
    if "output" not in data:
        p.unlink()
        continue
    dest = p.with_name(p.stem + ".output.json")
    dest.write_bytes(p.read_bytes())
print("slither build-info shim ready")
PY
slither . --config-file slither.config.json --ignore-compile

echo "onchain hygiene OK"
