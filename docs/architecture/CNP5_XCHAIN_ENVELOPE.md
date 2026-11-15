# CNP-5 — Cross-chain / L2 execution envelopes: bridge/relayer interface

Founder-authorized 2026-10-01. **No live bridging is implemented or performed
by this work.** This document specifies the offchain envelope format and the
contract a future relayer must satisfy. Any bridge deployment, RPC wiring, or
fund movement is founder-gated and out of scope.

## Envelope format

Defined in `src/sincor2/xchain_envelope.py`:

| Field | Type | Bound by signature |
|---|---|---|
| `payload` | JSON object (the CNP work order) | via `payload_digest = keccak256(canonical JSON)` |
| `payload_digest` | bytes32 (hex) | yes |
| `origin_chain_id` | uint256 | yes |
| `dest_chain_id` | uint256 | yes |
| `signer` | address (authorizing agent) | yes |
| `nonce` | uint256, unique per (signer, dest chain) | yes |
| `deadline` | uint256 unix timestamp | yes |

The EIP-712 domain is `("SINCOR XChainEnvelope", "1", chainId=dest_chain_id,
verifyingContract=<registry>)`. Because the domain chainId is the
**destination** chain, a signature is only meaningful on that chain: replaying
it elsewhere fails both the explicit `dest_chain_id == local_chain` check and
the implicit domain-separator binding (recovery yields a different address).

## Relayer contract (`RelayerAdapter`)

A concrete relayer must implement three methods (base class raises
`NotImplementedError` so a half-wired relayer fails loudly):

1. **`submit(envelope_dict, signature) -> envelope_id`**
   Origin side. Validate schema with `validate_relayer_payload()`. Transport
   the (envelope, signature) bytes to the destination chain's validator
   endpoint. The relayer must NOT mutate the envelope (any mutation breaks the
   signature — this is a feature, not a limitation).

2. **`deliver(envelope_dict, signature) -> receipt`**
   Destination side. MUST run `EnvelopeValidator(local_chain_id).validate()`
   — chain binding + signature + expiry + nonce freshness — BEFORE executing
   `payload`. `validate()` atomically consumes the nonce, so the executing
   path is the replay-protection path.

3. **`status(envelope_id) -> str`**
   Observability: `pending` / `delivered` / `rejected` / `expired`.

## Validation checklist for the future relayer

- Never accept an envelope whose `dest_chain_id` differs from the chain the
  validator runs on (`ChainIdMismatchError` — fail closed).
- Never execute without `validate()` consuming the nonce first.
- Back the nonce ledger with durable storage (the in-module validator is
  in-memory and intended for tests/single-process use).
- Relayer is untrusted transport: it cannot forge, re-stamp origin, or
  re-target the destination — all of those are inside the signed struct.
- Only secp256k1/EIP-712 signatures are accepted on this path; there is no
  HMAC fallback (unlike the auction demo path).
- Signatures must be canonical low-S; malleated `(r, n-s, v^1)` siblings are
  rejected before recovery.

## Example (Python)

```python
from sincor2.xchain_envelope import (
    XChainEnvelope, sign_envelope, EnvelopeValidator,
)

env = XChainEnvelope(
    payload={"task": "settle", "auction_id": "0x...", "amount": 150},
    origin_chain_id=11155111,   # Sepolia
    dest_chain_id=8453,          # Base
    signer="0xAgent...",
    nonce=1,
    deadline=4102444800,
)
sig = sign_envelope(env, private_key="0x...")

# destination side (relayer -> validator):
validator = EnvelopeValidator(8453)
signer = validator.validate(env, sig)  # raises on any attack; consumes nonce
# ... execute env.payload ...
```
