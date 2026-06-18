"""Curated monetization path catalog — recovered from 27_MONETIZATION_PATHS.py, mapped to live status."""

from __future__ import annotations

from typing import Any

# status: live | partial | planned | deprecated
CATALOG: list[dict[str, Any]] = [
    {"id": 1, "name": "Instant BI Reports", "status": "live", "url": "https://getsincor.com/buy", "rail": "AXM/SINC"},
    {"id": 2, "name": "Predictive Analytics", "status": "partial", "url": "https://getsincor.com/buy", "rail": "AXM"},
    {"id": 3, "name": "Custom AI Agents", "status": "partial", "url": "https://getsincor.com/products/professional", "rail": "SINC subscription"},
    {"id": 4, "name": "Data Integration Services", "status": "planned", "rail": "AXM"},
    {"id": 5, "name": "Process Automation", "status": "partial", "url": "https://getsincor.com/webbuilder", "rail": "SINC"},
    {"id": 6, "name": "Agent-as-a-Service (Monthly)", "status": "live", "url": "https://getsincor.com/pricing", "rail": "SINC"},
    {"id": 7, "name": "BI Dashboard Subscription", "status": "partial", "url": "https://getsincor.com/dashboard", "rail": "SINC"},
    {"id": 8, "name": "API Access Plans", "status": "planned", "rail": "AXM"},
    {"id": 9, "name": "Content Syndication Service", "status": "live", "module": "content_agent", "rail": "AXM"},
    {"id": 10, "name": "Compliance Monitoring", "status": "partial", "module": "compliance_monitor", "rail": "SINC"},
    {"id": 11, "name": "Enterprise Partnerships", "status": "live", "url": "https://getsincor.com/launch/partners", "rail": "manual"},
    {"id": 12, "name": "White-Label Licensing", "status": "planned", "rail": "manual"},
    {"id": 13, "name": "Consulting Retainers", "status": "partial", "rail": "manual"},
    {"id": 14, "name": "Training & Certification", "status": "planned", "rail": "AXM"},
    {"id": 15, "name": "Implementation Services", "status": "planned", "rail": "manual"},
    {"id": 16, "name": "SEO Content Sales", "status": "live", "module": "launch_content_engine", "rail": "organic"},
    {"id": 17, "name": "Report Templates", "status": "partial", "module": "fulfillment_engine", "rail": "AXM"},
    {"id": 18, "name": "Agent Templates", "status": "partial", "module": "agents/", "rail": "marketplace"},
    {"id": 19, "name": "Workflow Automation Templates", "status": "planned", "rail": "marketplace"},
    {"id": 20, "name": "Industry Reports", "status": "live", "module": "fulfillment_engine", "rail": "AXM"},
    {"id": 21, "name": "Affiliate Commissions", "status": "live", "url": "https://getsincor.com/refer", "rail": "3% ETH curve"},
    {"id": 22, "name": "Marketplace Fees", "status": "planned", "source": "sincor2-clone/marketplace", "rail": "AXM"},
    {"id": 23, "name": "Data Licensing", "status": "planned", "rail": "manual"},
    {"id": 24, "name": "Advertising Revenue", "status": "deprecated", "note": "Not aligned with token-first GTM"},
    {"id": 25, "name": "Premium Support", "status": "partial", "url": "https://getsincor.com/contact", "rail": "SINC"},
    {"id": 26, "name": "Crypto Consulting", "status": "partial", "rail": "manual"},
    {"id": 27, "name": "Smart Contract Services", "status": "live", "module": "onchain/", "rail": "SINC treasury"},
    {"id": 28, "name": "NFT Collection Management", "status": "deprecated", "note": "Liquidate treasury NFTs script exists; not GTM focus"},
]


def catalog_summary() -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for row in CATALOG:
        st = row["status"]
        by_status[st] = by_status.get(st, 0) + 1
    live = [r for r in CATALOG if r["status"] == "live"]
    partial = [r for r in CATALOG if r["status"] == "partial"]
    planned = [r for r in CATALOG if r["status"] == "planned"]
    return {
        "total_paths": len(CATALOG),
        "by_status": by_status,
        "live_count": len(live),
        "partial_count": len(partial),
        "planned_count": len(planned),
        "live_paths": [{"id": r["id"], "name": r["name"], "url": r.get("url"), "rail": r.get("rail")} for r in live],
        "recoverable_from_clone": [
            "A2A marketplace (a2a_integration.py + marketplace/)",
            "Vertical agents (trading, lead_gen, healthcare, dental, compliance)",
            "Platform bootstrap + agent card registry",
        ],
    }