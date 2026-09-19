#!/usr/bin/env python3
"""Generate TOKEN_CANON.generated.md from TOKEN_CANON.json.

Run from repo root:  python scripts/generate_canon_md.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = json.loads((ROOT / "TOKEN_CANON.json").read_text(encoding="utf-8"))


def main() -> None:
    sinc = CANON["sinc"]
    axm = CANON["axiom"]
    live = sinc.get("onchain_as_of_lock") or {}
    axm_live = axm.get("onchain_live") or {}
    md = f"""# TOKEN CANON — generated from TOKEN_CANON.json

**Machine source of truth is `TOKEN_CANON.json`.** This markdown is generated.
If a page, tweet, whitepaper, or brief disagrees with the JSON, the JSON wins.

Lock version: **{CANON.get('lock_version')}**
Policy addresses locked: **2026-09-01**
On-chain snapshot `verified_at`: **{CANON.get('verified_at')}**
Chain: **{CANON.get('chain')} ({CANON.get('chain_id')})**
Official SINC floor: **${CANON['decision']['official_floor_usd']}**
Price API: {CANON.get('price_api')}
Official buy: {CANON.get('official_buy_url')}

## Live contracts

| Role | Address | Decimals | Supply | Live snapshot |
|---|---|---|---|---|
| SINC | `{sinc['address']}` | {sinc['decimals']} | {sinc['total_supply']:,} | holders={live.get('holders')} transfers={live.get('transfers')} |
| AXM | `{axm['address']}` | {axm['decimals']} | {axm['total_supply']:,} | holders={axm_live.get('holders')} transfers={axm_live.get('transfers')} verified={axm.get('source_verified_basescan')} |
| Treasury | `{CANON['treasury']}` | — | — | — |
| USDC | `{CANON['usdc']}` | 6 | — | — |

## Plans (USD reference)

{CANON.get('plans_usd')}

## Do not buy

"""
    for row in CANON.get("do_not_buy") or []:
        md += f"- {row.get('label')}: `{row.get('address')}`\n"
    md += "\n## Forbidden claims until proven\n\n"
    for claim in CANON.get("forbidden_claims_until_proven") or []:
        md += f"- {claim}\n"
    (ROOT / "TOKEN_CANON.generated.md").write_text(md)
    print("wrote TOKEN_CANON.generated.md")


if __name__ == "__main__":
    main()
