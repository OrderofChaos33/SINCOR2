#!/usr/bin/env python3
"""
SINCOR Agent-to-Agent (A2A) Integration — A2A v1.0.1 Compliant
================================================================
Implements the A2A protocol v1.0.1 (https://a2aproject.github.io/A2A) so that
any compliant external agent — Hermes, Claude, OpenAI-compatible, OpenClaw, or
any custom agent that speaks JSON-RPC 2.0 — can discover, call, and collaborate
with the SINCOR agent swarm.

AXIOM (AXM) is the settlement token for every inter-agent transaction:
  • External agents acquire AXM to pay for SINCOR agent tasks.
  • SINCOR agents earn AXM for fulfilled tasks (deposited to their wallet).
  • A2A payment receipts: 50 % of each received AXM payment is burned to
    0x...dEaD (deflationary mechanics); 50 % goes to the SINCOR treasury.
  • DEX trading fees: 80 % of Uniswap V4 AXM/WETH pool trading fees are
    routed (off-chain team commitment, publicly auditable on Basescan) to
    the ecosystem treasury.  These two fee streams are independent.

A2A wire format (v1.0.1)
-------------------------
Discovery : GET  /.well-known/agent-card.json  → AgentCard JSON (v1.0.1)
           GET  /.well-known/agent.json         → AgentCard JSON (legacy alias)
JSON-RPC  : POST /api/a2a                       → JSON-RPC 2.0 dispatcher
  Methods : message/send, message/stream (SSE), tasks/get, tasks/cancel,
            tasks/list, tasks/pushNotificationConfig/set,
            tasks/pushNotificationConfig/get, tasks/resubscribe (SSE)
Legacy    : POST /api/a2a/tasks/send   GET /api/a2a/tasks/<id>
            POST /api/a2a/tasks/cancel GET /api/a2a/agents  POST /api/a2a/quote

The AgentCard advertises all 43 SINCOR agents as individual skills.
External agents select the skill they need and submit a task with their
AXIOM payment commitment.  SINCOR validates the on-chain payment (or an
off-chain signed intent), routes the task through the swarm, and returns
the result.

Quick start
-----------
    from sincor2.a2a_integration import A2ARouter

    router = A2ARouter()
    # in Flask: register blueprint
    app.register_blueprint(router.blueprint, url_prefix="")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.request as _urllib_request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Generator, List, Optional, Tuple

logger = logging.getLogger("sincor.a2a")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CEO 2026-08-18 CORRECTION: live AXM is 0x4c3fb66f14fbaa2088c9ae91017ba770da53715a — previous 0xfF7aF6... is dead
AXIOM_CONTRACT   = os.getenv("AXIOM_CONTRACT_ADDRESS", "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a")
SINC_CONTRACT    = os.getenv("SINC_CONTRACT_ADDRESS",  "0x9C8cd8d3961F445D653713dE65C6578bE11668e7")
TREASURY_WALLET  = os.getenv("TREASURY_ADDRESS",       "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac")
DEAD_ADDRESS     = "0x000000000000000000000000000000000000dEaD"
CHAIN_ID         = int(os.getenv("BASE_CHAIN_ID", "8453"))  # Base mainnet

# Primary token for A2A task payments. Default is SINC; set A2A_PRIMARY_TOKEN=AXIOM
# for legacy AXIOM-based settlements.
A2A_PRIMARY_TOKEN = os.getenv("A2A_PRIMARY_TOKEN", "SINC").upper()

# SINC price per A2A task call (whole tokens, decimals=0).
SINC_PRICE_PER_TASK = int(os.getenv("SINC_PRICE_PER_TASK", "1"))  # 1 SINC default

# Legacy AXIOM price per task (wei, 18 decimals) — kept for backward compatibility.
AXM_PRICE_PER_TASK = int(os.getenv("AXM_PRICE_PER_TASK", str(1 * 10**18)))  # 1 AXM default

PLATFORM_URL     = os.getenv("PLATFORM_URL", "https://getsincor.com")
PLATFORM_NAME    = "SINCOR Agent Swarm"
PLATFORM_VERSION = "2.0.0"
A2A_PROTOCOL_VERSION = "1.0.1"        # A2A spec version advertised in AgentCard

# Tunable limits
BASE_RPC_TIMEOUT     = int(os.getenv("BASE_RPC_TIMEOUT", "10"))   # seconds
TASK_LIST_MAX_PAGE   = int(os.getenv("TASK_LIST_MAX_PAGE", "1000"))

# Pricing engine: target fills per 24h window before price adjustment is triggered
PRICE_ADJUST_TARGET_FILLS = int(os.getenv("PRICE_ADJUST_TARGET_FILLS", "10"))
# Price adjustment step: ±10% of current price per 24h cycle
PRICE_ADJUST_STEP = float(os.getenv("PRICE_ADJUST_STEP", "0.10"))
# Free-quota calls granted to verified external A2A callers per skill (top 5 skills)
FREE_QUOTA_PER_CALLER = int(os.getenv("A2A_FREE_QUOTA_PER_CALLER", "5"))
# Top 5 skills eligible for free quota (subsidised for external agent discovery)
FREE_QUOTA_SKILLS = frozenset({
    "lead-enrichment",
    "competitor-intel",
    "outreach-sequence",
    "market-forecast",
    "content-blog",
})
# High-reputation threshold: callers with >= this many settled calls get priority
REPUTATION_HIGH_THRESHOLD = int(os.getenv("A2A_REPUTATION_HIGH_THRESHOLD", "10"))

# Non-production environments where on-chain / payment checks are skipped
_DEV_ENVS: frozenset = frozenset({"development", "dev", "test", "testing", "local"})
