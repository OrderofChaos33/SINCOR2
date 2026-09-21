"""Unified execution adapter — the ONLY place orders reach an exchange.

Replaces the three disconnected Polyclaw implementations
(``polyclaw_scheduler``, ``polyclaw_mega_aggressive_live``,
``verticals/trading/polyclaw``) behind a single adapter with hard guarantees:

- **Dry-run by default.** Live orders require ``POLYCLAW_LIVE=true`` AND valid
  Polymarket credentials in the environment. Anything else simulates and
  says so loudly.
- **Capital caps enforced upstream** by ``bankroll.py``.
- **Kill switch.** A tripped switch (DB flag or ``/data/POLYCLAW_HALT`` file)
  blocks every order until manually cleared.
- **EOA allowances.** On first live client init, approve USDC.e + CTF for the
  Polymarket exchange contracts and refresh the CLOB balance/allowance cache.
  Without this, funded wallets still cannot trade.
- **Fill reconciliation** against the CLOB REST API — no phantom PnL.

Nothing here signs anything without an explicit private key from the
environment. Keys are never logged.

Environment
-----------
POLYCLAW_LIVE                 "true" to allow real orders (default: false)
POLYMARKET_PRIVATE_KEY        Polygon EOA key for the CLOB (hex, 0x-prefixed)
POLYCLAW_PRIVATE_KEY          fallback alias for the same key
POLYMARKET_PK                 second fallback alias
POLYMARKET_FUNDER             Address funding the orders (defaults to key addr)
POLYMARKET_SIGNATURE_TYPE     0=EOA (default), 1=Magic/email, 2=browser proxy
POLYMARKET_API_KEY / _SECRET / _PASSPHRASE   CLOB API creds (derived if absent)
POLYMARKET_HOST               default https://clob.polymarket.com
POLYGON_RPC_URL               default https://polygon-bor.publicnode.com
"""
