"""AXM-first price book. Card rails are a human fallback only."""

from __future__ import annotations

PRICE_BOOK = {
    "kya_list": {"axm": 0, "usdc": 0, "usd_card": 0, "note": "free listing"},
    "kya_verify": {"axm": 25, "usdc": 5, "usd_card": 9, "stake_axm_min": 50},
    "sla_epoch": {"axm": 8, "usdc": 2, "usd_card": 20, "interval_s": 300},
    "sadas_month": {"axm": 40, "usdc": 25, "usd_card": 49, "axm_discount_if_verified": 10},
    "escrow_take_bps": 150,
    "quest_reward_axm": 15,
    "chain_id": 8453,
    "settlement": "AXM first, USDC second, card last",
}
