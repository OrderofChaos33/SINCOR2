"""
SINCOR off-chain solver quoting engine — PAPER / SHADOW ONLY.

Builds SINCOR's own DEX quotes from public on-chain pool state on Base
(Uniswap V2/V3-style pools, Aerodrome volatile pools) using the public,
open-source pool math. Nothing in this package signs, submits, swaps, or
touches a key: it reads public RPC state and produces *unsigned* quote
objects in the shape a solver would respond with.

Hard safety rules:
- Read-only RPC access (eth_call / block / gas price). No transactions.
- No private keys are read, accepted, or used anywhere in this package.
- Quotes are measurements, not commitments: ``signed`` is always False
  and ``signature`` is always None in this build.
"""
