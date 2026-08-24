# Registry: A2A schema gate + canonical on-chain addresses

Additive. Does not change Contract-Net bidding, Cortex memory/settlement/merit,
`MemorySystem`, `ReputationEngine`, or `SettlementCoordinator`.

## Schema gate (`src/sincor2/schema_gate.py`)

`AgentSkill.input_schema` is compiled once at module load
(`compile_skill_schemas(SINCOR_SKILLS)` in `a2a_integration.py`). Vertical
skills appended by `platform_bootstrap` call `refresh_skill_schemas()`.

`message/send` and `message/stream` validate before the swarm runs:

1. Unknown skill → JSON-RPC `-32602`.
2. Payload extracted in order: `params.input` / `params.data` / `params.payload`
   → DataPart → JSON text → freeform promotion.
3. Freeform promotion maps a lone string onto a **single required string field**
   so existing `"Enrich Acme Corp"` callers keep working.
4. Skills with an empty schema stay freeform (healthcare-rcm and other
   registry-appended verticals).
5. Failures return HTTP 200 JSON-RPC `{ error: { code: -32602, data: { skillId, source, errors[] } } }`.
6. Prototype-pollution keys (`__proto__`, `constructor`, `prototype`) are
   rejected even when `jsonschema` is not installed.

Prefers the `jsonschema` Draft-07 package; otherwise uses the in-repo subset
(type / required / properties / items / enum / min / max / pattern).

## Canonical addresses (`src/sincor2/onchain/constants.py`)

Every runtime module imports from here. Human index:
[`CANONICAL_ADDRESSES.md`](../../CANONICAL_ADDRESSES.md).

| Role | Address | Decimals |
|---|---|---|
| AXM | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | 18 |
| SINC | `0xe1D836087F6573b665d25CE088793E916D7892f8` | 8 |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | — |

`resolve_address(env, canonical)` ignores stale and malformed env overrides so a
forgotten `.env` cannot silently settle against retired `0x9C8cd8…` or
`0xfF7aF6…`. Foundry fork tests that pin the historical SINC address are left
alone.

## Startup probe (`src/sincor2/onchain/probe.py`)

Boot always checks catalog integrity. When `BASE_RPC_URL` is set it `eth_call`s
`symbol()` (`0x95d89b41`) and `decimals()` (`0x313ce567`). A mismatch is logged;
RPC failure **never blocks boot**.

HTTP: `/api/registry/*`.
