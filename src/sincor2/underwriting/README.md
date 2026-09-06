# Agent Underwriting (v1)

Treasury Mandate Runtime. Not a stablecoin.

Default tap is `ledger_sim`. `bridge_mint` is a stub that cannot move funds.

See `docs/underwriting/BUILD_GUIDE.md`.

```
python -m pytest tests/underwriting -q
bash scripts/underwrite_demo.sh
python -m sincor2.underwriting.api
```
