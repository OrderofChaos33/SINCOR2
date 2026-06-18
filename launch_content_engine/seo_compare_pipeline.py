"""Draft SEO comparison snippets for agent-token discovery."""

from __future__ import annotations

import random

PRIMARY_SEO_KEYWORDS = [
    "automated lead generation for service businesses",
    "AI business intelligence software",
    "service business marketing automation",
    "lead generation system for contractors",
    "business intelligence for small business",
    "automated customer acquisition software",
    "service business CRM with AI",
    "AI agent platform on Base blockchain",
]

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


def _keyword_comparison() -> tuple[str, str]:
    kw = random.choice(PRIMARY_SEO_KEYWORDS)
    title = f"SINCOR vs legacy stacks — {kw.split(' for ')[0]}"
    body = (
        f"Search intent: **{kw}**\n\n"
        "Most tools bolt a chat widget onto Zapier and call it AI.\n"
        "SINCOR ships:\n"
        "• 42+ specialized agents (A2A-discoverable)\n"
        "• Verified Base contracts + $1.50 USDC floor gateway\n"
        "• Autonomous fulfillment — BI reports and content on payment\n\n"
        "Compare on-chain proof: https://getsincor.com/sinc\n"
        "Agent card: https://getsincor.com/.well-known/agent-card.json"
    )
    return title, body


def draft_comparison() -> tuple[str, str]:
    # Mix curated comparisons with keyword-targeted drafts
    pool = COMPARISONS + [{"title": "_kw", "body": ""}]
    pick = random.choice(pool)
    if pick.get("title") == "_kw":
        return _keyword_comparison()
    return pick["title"], pick["body"]