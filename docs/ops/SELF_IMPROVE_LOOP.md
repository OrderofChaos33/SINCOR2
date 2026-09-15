# Self-improve loop

1. Heartbeat every 60s (`POST /api/a2a/heartbeat`). Stale after 180s → Suspended.
2. Four-tier memory JSONL under `agents/memory/<id>/`.
3. TOA opens an internal contract-net job at least every cycle via `sincor2.wardrobe.self_improve.open_improve_job`.
4. First live assignment 2026-09-14: Critic won `IMP-20260914174854` (wardrobe lift + storefront).
5. Do not spawn 43 dynos. One runner plus the task market.
