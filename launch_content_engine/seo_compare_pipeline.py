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
            "SINC (`0xe1D836087F6573b665d25CE088793E916D7892f8`) ships a 42-agent swarm "
            "with a $0.15 floor ($150M FDV / 1B tokens) — verifiable in 30 seconds.\n"
            "Checkout: https://getsincor.com/buy"
        ),
    },
    {
        "title": "Where is the SINC inventory?",
        "body": (
            "An LBP with $0 seeded liquidity is a billboard, not a market.\n"
            "SINC is 1B supply at a $0.15 official floor ($150M FDV), with v4 hook "
            "limit orders on Base — on-chain, not in a pitch deck.\n"
            "https://getsincor.com/buy"
        ),
    },
    {
        "title": "Uniswap v4 limit-order hooks for token launches",
        "body": (
            "SINC uses a SincLimitOrderHook for USDC-denominated sell walls "
            "and a SincHookRouter for gateway buys — no manual market maker required.\n"
            "Gateway: https://getsincor.com/buy"
        ),
    },
]


def draft_comparison() -> tuple[str, str]:
    c = random.choice(COMPARISONS)
    return c["title"], c["body"]