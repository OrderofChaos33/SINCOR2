# G1 AXM verification — STOP

Date: 2026-09-19

## Compiler (onchain/foundry.toml — Axiom.sol lives here)

- solc: 0.8.26
- optimizer: true
- optimizer_runs: 200
- via_ir: true
- evm_version: cancun

Root foundry.toml is 0.8.27 and does not own Axiom.sol.

## Bytecode

- `cast code 0x4c3fb66f14fbaa2088c9ae91017ba770da53715a --rpc-url https://mainnet.base.org`
- deployed: `9013 /tmp/axm_deployed.txt`
- local `forge build` of `onchain/src/Axiom.sol` deployedBytecode: `2871 /tmp/axm_local.txt`
- `diff` → **MISMATCH**

Live runtime contains Ownable + `mint(address,uint256)` (`40c10f19`, `f2fde38b`, `715018a6`, `8da5cb5b`). Repo `Axiom.sol` is fixed-supply, no owner, no mint. Publishing the repo file would be a false verification.

Flatten: 76 lines. Not submitted.

https://basescan.org/address/0x4c3fb66f14fbaa2088c9ae91017ba770da53715a still unverified.
