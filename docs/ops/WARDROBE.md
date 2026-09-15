# Agent wardrobe

Every `agents/E-*.yaml` file is a production wardrobe, not a catalog stub.

- Public roster: 43 star-named agents (`E-auriga-01` … `E-mesarthim-43`)
- Command layer (internal): TOA 44, Strategist 45, Critic 46, Treasury 47
- Status starts at `WardrobeDraft`. `Active` requires a public address and sandbox pass.
- Private keys never belong in git. Officer assigns addresses (`wallet.key_status`).
- Metrics are ledger-backed. New agents show trust 40 provisional.
- Schema and tests: `src/sincor2/wardrobe/`, `tests/wardrobe/test_wardrobe.py`
- Lift: `PYTHONPATH=src python3 scripts/lift_wardrobes.py`
