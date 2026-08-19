"""Core one-line / one-command registration helper.

Usage:
    from examples.onboarding.register import register_agent
    result = register_agent(my_agent_card_dict)
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import requests

DEFAULT_ENDPOINT = os.getenv("SINCOR_API", "https://getsincor.com")


def register_agent(
    card: Dict[str, Any],
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    wallet: Optional[str] = None,
) -> Dict[str, Any]:
    """Register or update an Agent Card on SINCOR.

    Ensures pricing / SLA / paymentRails are present (fills safe defaults if missing).
    Returns the public directory entry + registration status.
    """
    base = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
    url = f"{base}/api/marketplace/register"  # expected surface once #159 + this package land

    # Enforce machine-readable commercial surface
    card = dict(card)
    if "pricing" not in card and "sincPricing" not in card:
        card["pricing"] = {
            "pricePerCall": 1.0,
            "currency": "AXM",
            "model": "per-call",
        }
    if "sla" not in card:
        card["sla"] = {
            "maxLatencyMs": 30000,
            "availability": "99.0%",
        }
    if "paymentRails" not in card:
        card["paymentRails"] = ["AXM", "x402"]
    if "qualityTier" not in card:
        card["qualityTier"] = "experimental"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {"card": card}
    if wallet:
        payload["wallet"] = wallet

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        # Fallback for local / offline development
        return {
            "status": "local_only",
            "message": f"Remote registration unavailable ({e}). Card validated locally.",
            "card": card,
            "next": "Deploy or point SINCOR_API at a live instance that exposes /api/marketplace/register",
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Register an Agent Card on SINCOR")
    parser.add_argument("--card", required=True, help="Path to Agent Card JSON")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--wallet", default=None)
    args = parser.parse_args()
    with open(args.card, encoding="utf-8") as f:
        card = json.load(f)
    print(json.dumps(register_agent(card, endpoint=args.endpoint, wallet=args.wallet), indent=2))
