# Agent Underwriting

Spend envelopes for machine treasuries. Not a branded stablecoin.

```bash
python -m pytest tests/underwriting -q
bash scripts/underwrite_demo.sh
python -m sincor2.underwriting.api   # http://127.0.0.1:8787/underwrite/demo
```

Default tap: `ledger_sim`. `bridge_mint` is a stub that refuses transfers.

See `PARTNER_ONE_PAGER.md` for the handshake copy.
