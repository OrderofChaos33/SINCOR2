"""A2A production bootstrap — register discovery + JSON-RPC surfaces on any Flask app.

Usage in mvp_app.py (or any entrypoint):

    from sincor2.a2a_bootstrap import register_a2a
    register_a2a(app)

This is the single critical path that unblocks:
  GET  /.well-known/agent-card.json
  GET  /.well-known/agent.json
  POST /api/a2a
  GET  /api/a2a/agents
  GET|POST /api/a2a/quote
  POST /api/a2a/settle
  GET  /api/a2a/leaderboard
  GET  /api/a2a/pricing
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("sincor.a2a.bootstrap")


def register_a2a(app: Any) -> bool:
    """Register A2ARouter blueprint on the given Flask app. Idempotent.

    Returns True if routes were registered, False if already present or failed.
    """
    if getattr(app, "_sincor_a2a_registered", False):
        logger.info("A2A already registered — skipping")
        return True

    try:
        from sincor2.a2a_integration import A2ARouter

        router = A2ARouter()
        # url_prefix="" so /.well-known and /api/a2a land at domain root
        app.register_blueprint(router.blueprint, url_prefix="")
        app._sincor_a2a_registered = True
        logger.info(
            "A2A v1.0.1 surfaces live: agent-card.json, agent.json, /api/a2a "
            "(+ agents, quote, settle, leaderboard, pricing)"
        )
        return True
    except Exception as exc:
        logger.exception("A2A registration failed: %s", exc)
        return False


def required_env_report() -> dict:
    """Return production env readiness for A2A payment verification + task store."""
    return {
        "PLATFORM_URL": os.getenv("PLATFORM_URL", "https://getsincor.com"),
        "A2A_PRIMARY_TOKEN": os.getenv("A2A_PRIMARY_TOKEN", "AXIOM"),
        "AXIOM_CONTRACT_ADDRESS": os.getenv(
            "AXIOM_CONTRACT_ADDRESS", "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822"
        ),
        "TREASURY_ADDRESS": os.getenv(
            "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
        ),
        "BASE_RPC_URL": os.getenv("BASE_RPC_URL") or "NOT_SET",
        "A2A_TASK_STORE": os.getenv("A2A_TASK_STORE", "memory"),
        "REDIS_URL": "set" if os.getenv("REDIS_URL") else "NOT_SET",
        "BASE_CHAIN_ID": os.getenv("BASE_CHAIN_ID", "8453"),
    }
