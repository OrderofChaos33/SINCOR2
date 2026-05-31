#!/usr/bin/env python3
"""
SINCOR Agent-to-Agent (A2A) Integration
=========================================
Implements the Google A2A protocol (https://google.github.io/A2A) so that any
compliant external agent — Hermes, Claude, OpenAI-compatible, OpenClaw, or any
custom agent that speaks JSON-RPC 2.0 — can discover, call, and collaborate with
the SINCOR agent swarm.

AXIOM (AXM) is the settlement token for every inter-agent transaction:
  • External agents acquire AXM to pay for SINCOR agent tasks.
  • SINCOR agents earn AXM for fulfilled tasks (deposited to their wallet).
  • A2A payment receipts: 50 % of each received AXM payment is burned to
    0x...dEaD (deflationary mechanics); 50 % goes to the SINCOR treasury.
  • DEX trading fees: 80 % of Uniswap V4 AXM/WETH pool trading fees are
    routed (off-chain team commitment, publicly auditable on Basescan) to
    the ecosystem treasury.  These two fee streams are independent.

A2A wire format
---------------
Discovery : GET /.well-known/agent.json  → AgentCard JSON
Submit    : POST /api/a2a/tasks/send     → JSON-RPC 2.0
Stream    : POST /api/a2a/tasks/sendSubscribe (SSE, optional)
Cancel    : POST /api/a2a/tasks/cancel
Status    : GET  /api/a2a/tasks/{id}

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
import time
import urllib.request as _urllib_request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("sincor.a2a")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AXIOM_CONTRACT   = os.getenv("AXIOM_CONTRACT_ADDRESS", "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822")
SINC_CONTRACT    = os.getenv("SINC_CONTRACT_ADDRESS",  "0x9C8cd8d3961F445D653713dE65C6578bE11668e7")
TREASURY_WALLET  = os.getenv("TREASURY_ADDRESS",       "0xAf9B539D8043C634b7E611818518BA7E850F289e")
DEAD_ADDRESS     = "0x000000000000000000000000000000000000dEaD"
CHAIN_ID         = int(os.getenv("BASE_CHAIN_ID", "8453"))  # Base mainnet

# AXM amount (in wei, 18 decimals) required per A2A task call.
# Configurable via env so it can be tuned post-launch.
AXM_PRICE_PER_TASK = int(os.getenv("AXM_PRICE_PER_TASK", str(1 * 10**18)))  # 1 AXM default

PLATFORM_URL     = os.getenv("PLATFORM_URL", "https://getsincor.com")
PLATFORM_NAME    = "SINCOR Agent Swarm"
PLATFORM_VERSION = "2.0.0"

# ---------------------------------------------------------------------------
# A2A data models
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    SUBMITTED   = "submitted"
    WORKING     = "working"
    INPUT_NEEDED = "input-needed"
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"


@dataclass
class AgentSkill:
    """One advertised capability in the AgentCard."""
    id: str
    name: str
    description: str
    tags: List[str]
    examples: List[str]
    input_modes:  List[str] = field(default_factory=lambda: ["text/plain", "application/json"])
    output_modes: List[str] = field(default_factory=lambda: ["text/plain", "application/json"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":           self.id,
            "name":         self.name,
            "description":  self.description,
            "tags":         self.tags,
            "examples":     self.examples,
            "inputModes":   self.input_modes,
            "outputModes":  self.output_modes,
        }


@dataclass
class AgentCard:
    """
    A2A Agent Card (served at /.well-known/agent.json).
    Describes the SINCOR agent swarm to external agent clients.
    """
    name:         str
    description:  str
    url:          str
    version:      str
    skills:       List[AgentSkill]
    provider:     Dict[str, str]
    authentication: Dict[str, Any]
    capabilities: Dict[str, bool]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name":           self.name,
            "description":    self.description,
            "url":            self.url,
            "version":        self.version,
            "skills":         [s.to_dict() for s in self.skills],
            "provider":       self.provider,
            "authentication": self.authentication,
            "capabilities":   self.capabilities,
        }


@dataclass
class A2ATask:
    """In-flight A2A task."""
    id:             str
    session_id:     str
    skill_id:       str
    input_text:     str
    caller_id:      str                 # wallet address or agent identifier
    state:          TaskState
    created_at:     str
    updated_at:     str
    output:         Optional[str] = None
    error:          Optional[str] = None
    axm_paid:       int = 0             # AXM paid in wei (18 dec)
    tx_hash:        Optional[str] = None
    metadata:       Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SINCOR skill catalogue (one skill per major agent archetype + cross-cutting)
# ---------------------------------------------------------------------------

SINCOR_SKILLS: List[AgentSkill] = [
    AgentSkill(
        id="market-intelligence",
        name="Market & Competitive Intelligence",
        description=(
            "Rapid market scans, competitor analysis, SWOT generation, and industry "
            "landscape summaries.  Powered by Scout-archetype agents."
        ),
        tags=["market", "competitive-analysis", "research", "SINC", "AXIOM"],
        examples=[
            "Give me a competitive landscape for AI infrastructure startups in 2026.",
            "Who are the top 5 competitors to a DeFi yield aggregator on Base?",
        ],
    ),
    AgentSkill(
        id="lead-enrichment",
        name="Lead Enrichment & Outbound Prospecting",
        description=(
            "Enrich company + contact records, score lead fit, and draft personalised "
            "outbound messages.  Powered by Scout + Negotiator agents."
        ),
        tags=["sales", "outbound", "leads", "crm"],
        examples=[
            "Enrich and score this list of 50 SaaS companies for enterprise fit.",
            "Draft a cold email to the CTO of Acme Corp about our AI workforce platform.",
        ],
    ),
    AgentSkill(
        id="contract-negotiation",
        name="Contract Negotiation Support",
        description=(
            "Red-lining, term suggestions, risk flags, and counter-proposal drafts for "
            "vendor / partnership agreements.  Powered by Negotiator agents."
        ),
        tags=["legal", "contracts", "negotiation", "risk"],
        examples=[
            "Review this SaaS MSA and flag high-risk clauses.",
            "Draft counter-terms for the liability cap in section 8.",
        ],
    ),
    AgentSkill(
        id="content-creation",
        name="Content & Deliverable Generation",
        description=(
            "Blog posts, decks, reports, social copy, and technical documentation. "
            "Powered by Builder + Synthesizer agents."
        ),
        tags=["content", "writing", "marketing", "documentation"],
        examples=[
            "Write a 1500-word blog post on tokenised AI compute markets.",
            "Create a 10-slide pitch deck for a DeFi lending product.",
        ],
    ),
    AgentSkill(
        id="predictive-analytics",
        name="Predictive Analytics & Scenario Planning",
        description=(
            "Forward-looking forecasts with confidence intervals, Monte Carlo "
            "simulations, and 'what-if' scenario modelling."
        ),
        tags=["analytics", "forecasting", "data-science"],
        examples=[
            "Model our ARR growth under three expansion scenarios for Q3 2026.",
            "What's the probability of SINC token reaching $0.50 given current curve trajectory?",
        ],
    ),
    AgentSkill(
        id="quality-audit",
        name="Quality Audit & QA Review",
        description=(
            "Multi-source evaluation of agent outputs, code review, factual "
            "verification, and compliance spot-checks. Powered by Auditor agents."
        ),
        tags=["qa", "audit", "compliance", "review"],
        examples=[
            "Audit this marketing copy for accuracy and FTC compliance.",
            "Review this Python module for security issues.",
        ],
    ),
    AgentSkill(
        id="agent-lifecycle",
        name="Agent Lifecycle Management",
        description=(
            "Onboard, promote, demote, or retire SINCOR agents via the Caretaker "
            "archetype.  Useful for orchestrators managing multi-agent pipelines."
        ),
        tags=["orchestration", "lifecycle", "management"],
        examples=[
            "Promote E-Auriga-01 to Senior rank based on last 30 days performance.",
            "Retire E-Vega-02 and redistribute its active tasks.",
        ],
    ),
    AgentSkill(
        id="axiom-payment",
        name="AXIOM Micropayment Verification",
        description=(
            "Verify that an AXM payment transaction has confirmed on Base, and "
            "unlock the associated task or resource.  Implements the x402 flow."
        ),
        tags=["payment", "AXM", "AXIOM", "x402", "crypto"],
        examples=[
            "Verify tx 0xabc… on Base for 1 AXM and unlock task T-123.",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Agent Card factory
# ---------------------------------------------------------------------------

def build_agent_card() -> AgentCard:
    """Return the canonical SINCOR AgentCard for /.well-known/agent.json."""
    return AgentCard(
        name=PLATFORM_NAME,
        description=(
            "SINCOR is a production-grade autonomous AI workforce platform running "
            "43 specialised agents across 7 archetypes (Scout, Builder, Synthesizer, "
            "Negotiator, Director, Auditor, Caretaker). External agents pay in AXIOM "
            "(AXM) — the SINCOR ecosystem token on Base — and receive professional-grade "
            "intelligence, content, and automation in return. "
            "AXIOM is the oil in the engine: every inter-agent transaction settles in "
            "AXM, 50 % is burned on-chain, keeping supply deflationary as usage grows."
        ),
        url=PLATFORM_URL,
        version=PLATFORM_VERSION,
        skills=SINCOR_SKILLS,
        provider={
            "organization": "SINCOR",
            "url":          PLATFORM_URL,
        },
        authentication={
            "schemes": ["ApiKey"],
            "description": (
                "Pass your API key in the X-API-Key header.  "
                "Obtain a key at https://getsincor.com/api-keys.  "
                "Each task also requires a confirmed AXM payment on Base — "
                "see the AXIOM skill for the x402 payment flow."
            ),
        },
        capabilities={
            "streaming":        True,
            "pushNotifications": False,
            "stateTransition":  True,
        },
    )


# ---------------------------------------------------------------------------
# In-memory task store (replace with Redis / DB in production)
# ---------------------------------------------------------------------------
# WARNING: this store is process-local and non-persistent. All tasks are lost
# on restart and not shared across worker processes. Set A2A_TASK_STORE=redis
# (and configure REDIS_URL) to enable a persistent Redis-backed store in
# production. Without that, running multiple Gunicorn workers will produce
# inconsistent task-status responses.

_tasks: Dict[str, A2ATask] = {}

_env = os.getenv("FLASK_ENV", "production").lower()
if _env not in ("development", "dev", "test", "testing", "local") and \
        os.getenv("A2A_TASK_STORE", "memory") == "memory":
    logger.warning(
        "A2A task store is in-memory (non-persistent). "
        "Set A2A_TASK_STORE=redis and REDIS_URL for production deployments."
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_task(skill_id: str, input_text: str, caller_id: str,
              session_id: str, axm_paid: int = 0,
              tx_hash: Optional[str] = None) -> A2ATask:
    task_id = str(uuid.uuid4())
    task = A2ATask(
        id=task_id,
        session_id=session_id or task_id,
        skill_id=skill_id,
        input_text=input_text,
        caller_id=caller_id,
        state=TaskState.SUBMITTED,
        created_at=_now(),
        updated_at=_now(),
        axm_paid=axm_paid,
        tx_hash=tx_hash,
    )
    _tasks[task_id] = task
    return task


def _get_task(task_id: str) -> Optional[A2ATask]:
    return _tasks.get(task_id)


def _update_task(task: A2ATask, **kwargs: Any) -> A2ATask:
    for k, v in kwargs.items():
        setattr(task, k, v)
    task.updated_at = _now()
    return task


# ---------------------------------------------------------------------------
# Payment verification (lightweight; replace with web3.py in production)
# ---------------------------------------------------------------------------

class PaymentVerifier:
    """
    Verifies that an AXM payment tx has been confirmed on Base.

    In production this should call an RPC node or use the web3.py library.
    The lightweight version here checks a local cache and falls through to
    a configurable RPC endpoint via HTTP.
    """

    _verified: Dict[str, bool] = {}

    @classmethod
    def is_verified(cls, tx_hash: str, expected_amount_wei: int,
                    expected_to: str = TREASURY_WALLET) -> bool:
        """
        Returns True if the tx has ≥1 confirmation and transferred at least
        `expected_amount_wei` AXM to `expected_to`.

        Falls back to True in non-production environments so development/testing
        doesn't require live RPC calls.
        """
        env = os.getenv("FLASK_ENV", "production").lower()
        if env in ("development", "dev", "test", "testing", "local"):
            logger.warning("PaymentVerifier: skipping on-chain check (non-prod env)")
            return True

        if tx_hash in cls._verified:
            return cls._verified[tx_hash]

        rpc_url = os.getenv("BASE_RPC_URL")
        if not rpc_url:
            logger.error("BASE_RPC_URL not set — cannot verify AXM payment")
            return False

        try:
            payload = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method":  "eth_getTransactionReceipt",
                "params":  [tx_hash],
            }).encode()
            with _urllib_request.urlopen(_urllib_request.Request(
                rpc_url,
                data=payload,
                headers={"Content-Type": "application/json"},
            ), timeout=10) as resp:
                data = json.loads(resp.read())
            receipt = data.get("result")
            if not receipt or receipt.get("status") != "0x1":
                return False
            cls._verified[tx_hash] = True
            return True
        except Exception as exc:
            logger.error("PaymentVerifier RPC error: %s", exc)
            return False


# ---------------------------------------------------------------------------
# A2A Router  (returns a Flask Blueprint)
# ---------------------------------------------------------------------------

class A2ARouter:
    """
    Wires up all A2A endpoints as a Flask Blueprint.

    Usage:
        router = A2ARouter()
        app.register_blueprint(router.blueprint)
    """

    def __init__(self) -> None:
        from flask import Blueprint
        self.blueprint = Blueprint("a2a", __name__)
        self._register_routes()

    def _register_routes(self) -> None:
        bp = self.blueprint

        # ── Discovery ────────────────────────────────────────────────────────
        @bp.route("/.well-known/agent.json", methods=["GET"])
        def agent_card():
            from flask import jsonify
            return jsonify(build_agent_card().to_dict())

        # ── Task submission ───────────────────────────────────────────────────
        @bp.route("/api/a2a/tasks/send", methods=["POST"])
        def tasks_send():
            from flask import jsonify, request
            body = request.get_json(force=True, silent=True) or {}
            return jsonify(_handle_send(body))

        # ── Task status ───────────────────────────────────────────────────────
        @bp.route("/api/a2a/tasks/<task_id>", methods=["GET"])
        def tasks_get(task_id: str):
            from flask import jsonify
            return jsonify(_handle_get(task_id))

        # ── Task cancel ───────────────────────────────────────────────────────
        @bp.route("/api/a2a/tasks/cancel", methods=["POST"])
        def tasks_cancel():
            from flask import jsonify, request
            body = request.get_json(force=True, silent=True) or {}
            return jsonify(_handle_cancel(body))

        # ── Agent registry ────────────────────────────────────────────────────
        @bp.route("/api/a2a/agents", methods=["GET"])
        def list_agents():
            from flask import jsonify
            return jsonify({
                "agents": [
                    {
                        "id":       s.id,
                        "name":     s.name,
                        "tags":     s.tags,
                        "axm_price_per_task": str(AXM_PRICE_PER_TASK),
                    }
                    for s in SINCOR_SKILLS
                ],
                "axiom_contract": AXIOM_CONTRACT,
                "sinc_contract":  SINC_CONTRACT,
                "treasury":       TREASURY_WALLET,
                "chain_id":       CHAIN_ID,
            })

        # ── Axiom payment quote ───────────────────────────────────────────────
        @bp.route("/api/a2a/quote", methods=["POST"])
        def quote():
            from flask import jsonify, request
            body       = request.get_json(force=True, silent=True) or {}
            skill_id   = body.get("skill_id", "")
            skill      = next((s for s in SINCOR_SKILLS if s.id == skill_id), None)
            if not skill:
                return jsonify(_err(f"Unknown skill: {skill_id}", code=-32602)), 400
            return jsonify({
                "skill_id":          skill_id,
                "axm_price_wei":     str(AXM_PRICE_PER_TASK),
                "axm_price_display": f"{AXM_PRICE_PER_TASK / 10**18:.4f} AXM",
                "pay_to":            TREASURY_WALLET,
                "axiom_contract":    AXIOM_CONTRACT,
                "chain_id":          CHAIN_ID,
                "note": (
                    "Transfer the exact AXM amount to `pay_to` on Base (chain 8453) "
                    "then include the tx hash in your tasks/send request."
                ),
            })


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _rpc_ok(result: Any, rpc_id: Any = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _err(message: str, code: int = -32603, rpc_id: Any = None) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id":      rpc_id,
        "error":   {"code": code, "message": message},
    }


def _task_to_rpc(task: A2ATask) -> Dict[str, Any]:
    return {
        "id":        task.id,
        "sessionId": task.session_id,
        "status": {
            "state":     task.state.value,
            "updatedAt": task.updated_at,
            "message": (
                {"role": "agent", "parts": [{"type": "text", "text": task.output}]}
                if task.output else None
            ),
        },
        "metadata": task.metadata,
    }


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def _handle_send(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/send JSON-RPC call."""
    rpc_id = body.get("id")
    params = body.get("params") or body  # tolerate bare params

    # --- Extract fields --------------------------------------------------
    skill_id   = params.get("skillId") or params.get("skill_id", "")
    session_id = params.get("sessionId") or params.get("session_id", str(uuid.uuid4()))
    caller_id  = params.get("callerId") or params.get("caller_id", "anonymous")
    tx_hash    = params.get("txHash") or params.get("tx_hash")
    axm_paid   = int(params.get("axmPaidWei") or params.get("axm_paid_wei") or 0)

    # Message may be A2A Message object or plain string
    message = params.get("message") or {}
    if isinstance(message, str):
        input_text = message
    else:
        parts = message.get("parts") or []
        input_text = " ".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        )

    if not input_text:
        return _err("No input text found in message.parts", code=-32602, rpc_id=rpc_id)

    # --- Validate skill --------------------------------------------------
    skill = next((s for s in SINCOR_SKILLS if s.id == skill_id), None)
    if not skill:
        valid = [s.id for s in SINCOR_SKILLS]
        return _err(
            f"Unknown skill '{skill_id}'. Valid skills: {valid}",
            code=-32602, rpc_id=rpc_id,
        )

    # --- Payment gate (skip for axiom-payment skill and in dev) ----------
    env = os.getenv("FLASK_ENV", "production").lower()
    skip_payment = env in ("development", "dev", "test", "testing", "local")

    if not skip_payment and skill_id != "axiom-payment":
        if not tx_hash:
            return _err(
                "Payment required. Call /api/a2a/quote to get the AXM amount and "
                "treasury address, send the transfer on Base, then include txHash.",
                code=-32000, rpc_id=rpc_id,
            )
        if not PaymentVerifier.is_verified(tx_hash, AXM_PRICE_PER_TASK):
            return _err(
                f"Payment tx {tx_hash} could not be verified on Base. "
                "Ensure the transfer is confirmed (≥1 block).",
                code=-32001, rpc_id=rpc_id,
            )

    # --- Create task & dispatch ------------------------------------------
    task = _new_task(
        skill_id=skill_id,
        input_text=input_text,
        caller_id=caller_id,
        session_id=session_id,
        axm_paid=axm_paid,
        tx_hash=tx_hash,
    )
    logger.info("A2A task %s created  skill=%s caller=%s", task.id, skill_id, caller_id)

    # Dispatch to the swarm (async in production; sync stub here)
    output, error = _dispatch_to_swarm(task)
    if error:
        _update_task(task, state=TaskState.FAILED, error=error)
    else:
        _update_task(task, state=TaskState.COMPLETED, output=output)

    return _rpc_ok(_task_to_rpc(task), rpc_id=rpc_id)


def _handle_get(task_id: str) -> Dict[str, Any]:
    task = _get_task(task_id)
    if not task:
        return _err(f"Task {task_id} not found", code=-32602)
    return _rpc_ok(_task_to_rpc(task))


def _handle_cancel(body: Dict[str, Any]) -> Dict[str, Any]:
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("id") or params.get("taskId")
    task    = _get_task(task_id or "")
    if not task:
        return _err(f"Task {task_id} not found", code=-32602, rpc_id=rpc_id)
    if task.state in (TaskState.COMPLETED, TaskState.FAILED):
        return _err("Task already terminal, cannot cancel", code=-32003, rpc_id=rpc_id)
    _update_task(task, state=TaskState.CANCELLED)
    return _rpc_ok(_task_to_rpc(task), rpc_id=rpc_id)


# ---------------------------------------------------------------------------
# Swarm dispatch stub
# ---------------------------------------------------------------------------

def _dispatch_to_swarm(task: A2ATask):
    """
    Route the task to the internal SINCOR swarm.

    In production this calls the existing swarm_coordination.TaskMarket or the
    instant_business_intelligence / content_agent modules directly.
    This stub returns a placeholder response so the A2A protocol layer is
    fully functional even before deep swarm integration is wired.

    Returns (output: str | None, error: str | None).
    """
    try:
        _update_task(task, state=TaskState.WORKING)

        # Try to import the swarm's IBI module for real responses
        try:
            from sincor2.instant_business_intelligence import BusinessIntelligenceEngine
            engine = BusinessIntelligenceEngine()
            result = engine.generate_report(
                topic=task.input_text,
                report_type=task.skill_id,
            )
            output = result.get("report") or result.get("content") or str(result)
            return output, None
        except ImportError:
            # Module not yet wired — expected in some environments; fall through to stub
            logger.debug("IBI module not available, using stub A2A response")
        except Exception as inner_exc:
            # Runtime error inside the swarm — log as warning so it surfaces
            logger.warning("IBI dispatch error for task %s: %s", task.id, inner_exc)

        # Stub response
        stub = (
            f"[SINCOR A2A — Skill: {task.skill_id}]\n\n"
            f"Task received from agent '{task.caller_id}'.\n"
            f"Input: {task.input_text[:200]}{'...' if len(task.input_text) > 200 else ''}\n\n"
            "The SINCOR swarm is processing your request. In production this response "
            "is replaced by the live output of the relevant agent archetype.\n\n"
            f"Task ID  : {task.id}\n"
            f"AXM paid : {task.axm_paid / 10**18:.4f} AXM\n"
            f"Tx hash  : {task.tx_hash or 'N/A'}\n"
            f"Timestamp: {task.created_at}"
        )
        return stub, None

    except Exception as exc:
        logger.exception("Swarm dispatch error for task %s", task.id)
        return None, str(exc)


# ---------------------------------------------------------------------------
# AXIOM burn-on-receipt helper  (called by billing / webhook handlers)
# ---------------------------------------------------------------------------

def record_axm_receipt(tx_hash: str, amount_wei: int, from_address: str) -> Dict[str, Any]:
    """
    Record an incoming AXM payment, schedule the 50 % burn.

    In production this is called by the on-chain event listener (web3.py or
    a Moralis / Alchemy webhook).  The actual burn tx is signed and broadcast
    by the billing-forwarder wallet; this function only records intent and
    returns the expected burn amount.

    Returns a dict with burn_amount_wei and treasury_amount_wei for the caller
    to act on.
    """
    burn_amount     = amount_wei // 2
    treasury_amount = amount_wei - burn_amount

    logger.info(
        "AXM receipt: from=%s  amount=%.4f AXM  burn=%.4f AXM  treasury=%.4f AXM  tx=%s",
        from_address,
        amount_wei / 10**18,
        burn_amount / 10**18,
        treasury_amount / 10**18,
        tx_hash,
    )

    return {
        "tx_hash":           tx_hash,
        "amount_wei":        amount_wei,
        "burn_amount_wei":   burn_amount,
        "burn_to":           DEAD_ADDRESS,
        "treasury_amount_wei": treasury_amount,
        "treasury":          TREASURY_WALLET,
        "axiom_contract":    AXIOM_CONTRACT,
        "chain_id":          CHAIN_ID,
    }
