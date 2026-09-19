# SINCOR Trust Stack — one canonical surface

Last reviewed: 2026-09-19

Three identity/reputation systems shipped in parallel. This page is the
decision record. The Agent Card discovery page points at this stack only.

| Concern | Canonical system | Where | Do not use as primary |
|---|---|---|---|
| Agent registration | **KYA registry** | `src/sincor2/kya/registry.py`, `/v1` inbound fabric | Agent Passport NFT as the only registrar |
| Reputation scoring | **Agent underwriting score engine** | `docs/UNDERWRITING.md`, underwriting runtime `#234` | Ad-hoc homepage metrics, raw NFT traits |
| Credential verification | **KYA + ERC-8004 operator kit** | `docs/KYA.md`, `docs/ERC8004_REGISTER.md` | Self-attested Agent Card fields with no KYA bind |
| Payment settlement | **AXM on Base + x402 escrow** | `TOKEN_CANON.json` `axiom.address`, `SincorEscrow` when deployed | Live SINC as A2A gas; Stripe is USDC fallback for humans only |
| Human-readable identity card | Agent Passport / Genesis NFT | `#229` | Must *display* KYA id + underwriting score, not replace them |

## Routing rule

1. An agent registers in KYA (wallet + card + heartbeat).
2. Underwriting scores the mandate and emits a spend envelope.
3. Settlement moves AXM (or USDC fallback) through the escrow/x402 rail.
4. Agent Passport is the public badge that *points at* (1) and (2).

If a UI surface shows a second “trust score”, mark it `legacy` or delete it.
