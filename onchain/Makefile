# SINCOR onchain — single-command pipeline
# Usage: cd onchain && make <target>
# Full ship gate: make ship   (build + unit + fork + invariant, all green or nothing)

RPC              ?= https://base-rpc.publicnode.com
export BASE_RPC_URL ?= $(RPC)

.PHONY: setup build test test-unit test-fork test-invariant slither deploy-adapter ship clean

# Pinned deps — MUST match .github/workflows/onchain-ci.yml and DEPLOYING.md
setup:
	forge install foundry-rs/forge-std@v1.9.7
	forge install OpenZeppelin/openzeppelin-contracts@v5.5.0
	forge install Uniswap/v4-core@d153b048868a60c2403a3ef5b2301bb247884d46
	forge install Uniswap/v4-periphery@7ebd04b161745b75ed0c24ba2df3bc7c25f65606
	forge install Uniswap/permit2@cc56ad0f3439c502c246fc5cfcc3db92bb8b7219
	forge install OpenZeppelin/uniswap-hooks@26dc8e53f812a1ca390d470342adb6cd8c3286ad

build:
	forge build --sizes

test: test-unit test-fork

# Unit + fuzz (no RPC needed)
test-unit:
	forge test --no-match-contract "ForkTest|Fork|IntegrationTest" --no-match-path "test/invariant/*" -vvv

# Live Base fork tests (needs BASE_RPC_URL)
test-fork:
	forge test --match-contract "Fork" -vvv

# Stateful invariant suites (solvency etc.)
test-invariant:
	forge test --match-path "test/invariant/*" -vvv

slither:
	slither . --config-file slither.config.json

# Deploy SincFluidAdapter (broadcasts; requires DEPLOYER_KEY + BASESCAN_API_KEY in env)
deploy-adapter:
	forge script script/DeploySincFluidAdapter.s.sol \
		--rpc-url $(RPC) --broadcast --verify \
		--etherscan-api-key $(BASESCAN_API_KEY) -vvvv

# One command to rule the release: everything an auditor needs, green or it stops
ship: build test-unit test-fork test-invariant
	@echo "SHIP GATE PASSED — build + unit/fuzz + fork + invariant all green"

clean:
	forge clean
