"""Draft SEO comparison snippets for agent-token discovery."""

from __future__ import annotations

import random

COMPARISONS = [
    {
        "title": "Verified AI agent tokens vs vaporware launches",
        "body": (
            "Most 'AI agent tokens' ship a landing page and disappear. "
            "Checklist for real projects on Base:\n"
            "1. Sourcify full-match source\n"
            "2. Ownership renounced (no hidden mint)\n"
            "3. Live contracts you can call today\n\n"
            "SINC (`0x9C8cd8d3961F445D653713dE65C6578bE11668e7`) ships a 42-agent swarm "
            "plus bonding curve + audited v4 hook — verifiable in 30 seconds.\n"
            "Self-serve buy: https://getsincor.com/sinc"
        ),
    },
    {
        "title": "Bonding curve vs empty LBP — where is the inventory?",
        "body": (
            "An LBP with $0 seeded liquidity is a billboard, not a market.\n"
            "SINC's live sale inventory sits in the bonding curve (~65M SINC) "
            "and v4 hook limit orders (~20M SINC) — on-chain, not in a pitch deck.\n"
            "https://getsincor.com/why-no-dex"
        ),
    },
    {
        "title": "Uniswap v4 limit-order hooks for token launches",
        "body": (
            "SINC uses a SincLimitOrderHook for USDC-denominated sell walls "
            "and a SincHookRouter for gateway buys — no manual market maker required.\n"
            "Gateway: https://getsincor.com/sinc"
        ),
    },
]


def draft_comparison() -> tuple[str, str]:
    c = random.choice(COMPARISONS)
    return c["title"], c["body"]