# Post-deploy wiring — UNAPPLIED patches

These patches wire the deployed `SincorSettlementHub` into the live
surfaces. **They are prep only. Nothing in this directory is applied, and
nothing here may be applied until the mainnet deploy is confirmed on
Basescan.** Live code still states the honest pre-deploy state
(`hub_deployed_on_mainnet: False`, no hub address anywhere).

## APPLY ONLY AFTER THE MAINNET DEPLOY IS CONFIRMED ON BASESCAN

Order and placeholders — replace before applying:

| Placeholder | Replace with |
|---|---|
| `<MAINNET_HUB_ADDRESS>` | The deployed Hub address on Base mainnet (8453), checksummed, as confirmed on Basescan |
| `<SEPOLIA_HUB_ADDRESS>` | The deployed Hub address on Base Sepolia (84532) from the rehearsal deploy |

## The patches

### 01-sincor-x402-hub-address.patch
Target: `src/sincor_x402/hub_engine.py` in the **sincor-x402 repo**
(not this repo). Adds a `HUB_DEPLOYMENTS` registry
(`{8453: "<MAINNET_HUB_ADDRESS>", 84532: "<SEPOLIA_HUB_ADDRESS>"}`) and
makes `HubSettlementEngine`'s `hub_address` argument optional: when it is
not passed, the engine resolves the address from the `SINCOR_HUB_ADDRESS`
environment override first, then from the registry by chain id, and
raises if the chain is unknown or the registry entry is still a
placeholder. Apply in the sincor-x402 tree, run its hub tests
(`tests/test_hub_engine.py`), and ship through that package's normal
release flow.

### 02-ap2-hub-deployed-flag.patch
Target: `src/sincor2/a2a_integration.py` in **this repo**
(`build_ap2_discovery_document`, ~line 2295). Flips
`settlement.hub_deployed_on_mainnet` from `False` to `True`, updates the
`hub_note`, and updates the factory docstring to match.
**Apply-time companions (not in this patch):** the same flip must be
mirrored in `README.md` (the AP2 paragraph, ~line 761) and in
`tests/pytest/test_well_known_ap2.py` (line 36 asserts the flag is
`False` — update the assertion in the same change, or the suite goes red
for the right reason). Redeploy the site after applying so
`/.well-known/ap2.json` serves the new document.

### 03-agent-card-hub-reference.patch
Target: `src/sincor2/a2a_integration.py` in **this repo**. Adds an
optional `settlement_hub` field to the `AgentCard` dataclass, emits it
as `settlementHub` from `to_dict()` when set, and populates it in
`build_agent_card()` (~line 2163) with
`{network: "Base", chain_id: 8453, address: "<MAINNET_HUB_ADDRESS>",
contract: "SincorSettlementHub", protocol_fee_bps: 500, scheme:
"sincor-seal-v1"}`. Patches 02 and 03 touch different regions of the
same file and apply sequentially (verified at prep time).

## Verification at prep time (2026-11-13)

Each patch was generated from a scratch copy of its real target file and
verified with `git apply` against a pristine copy: all three apply
cleanly, 02+03 apply sequentially, and every edited copy parses as valid
Python. They have **not** been applied to any live tree.
