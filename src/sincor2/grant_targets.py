"""Grant and non-dilutive funding targets for SINCOR (Base + AI agent narrative)."""

from __future__ import annotations

from typing import Any


def list_grant_targets() -> list[dict[str, Any]]:
    """Curated apply-now list — no token borrow (SINC is not Aave/Morpho collateral)."""
    return [
        {
            "name": "Base Builder Grants (Gitcoin)",
            "url": "https://grants.base.org/",
            "fit": "Live Base mainnet product, verified contracts, agent platform",
            "ask": "$5k–$50k milestone grants",
            "effort": "2–4 hours application",
        },
        {
            "name": "Base Batches",
            "url": "https://www.basebatches.xyz/",
            "fit": "Early-stage Base builders; cohort + intros",
            "ask": "Cohort, mentorship, demo day",
            "effort": "Apply when batch opens",
        },
        {
            "name": "Coinbase Developer Platform — AI Builder Grants",
            "url": "https://www.coinbase.com/developer-platform/discover/launches/ai-builder-grants",
            "fit": "Autonomous AI agents on Base",
            "ask": "Credits / grant pool for AI infra",
            "effort": "1–2 hours",
        },
        {
            "name": "Base Get Funded hub",
            "url": "https://docs.base.org/get-started/get-funded",
            "fit": "Index of ecosystem funds",
            "ask": "Directory → apply to matching programs",
            "effort": "Research pass",
        },
        {
            "name": "Balancer Grants (ecosystem partner)",
            "url": "https://grants.balancer.fi/",
            "fit": "Liquidity infrastructure story; already in partner CRM",
            "ask": "Liquidity / integration grant",
            "effort": "Discord + grant form",
        },
        {
            "name": "Optimism Retro Funding (if Superchain PR lands)",
            "url": "https://community.optimism.io/",
            "fit": "Public goods on OP Stack / Base alignment",
            "ask": "Retroactive public-goods funding",
            "effort": "Post-Superchain listing",
        },
    ]


def borrow_reality_check() -> dict[str, Any]:
    """Honest: SINC cannot be borrowed against on Base DeFi today."""
    return {
        "aave_base": "SINC not listed as collateral",
        "morpho": "No SINC market",
        "cow_swap_sinc": "No liquidity — quotes ~$0",
        "realistic_paths": [
            "Grants (above)",
            "Revenue: platform plans paid in SINC/AXM at /buy",
            "Hook USDC fills when buyers appear",
            "OTC: sell SINC at $1.50 floor to strategic buyer",
            "Demo-first: swarm generates BI reports → first paying customer",
        ],
    }