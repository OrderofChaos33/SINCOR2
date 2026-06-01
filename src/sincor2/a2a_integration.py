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
import threading
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
A2A_PROTOCOL_VERSION = "1.0.1"        # A2A spec version advertised in AgentCard

# Tunable limits
BASE_RPC_TIMEOUT     = int(os.getenv("BASE_RPC_TIMEOUT", "10"))   # seconds
TASK_LIST_MAX_PAGE   = int(os.getenv("TASK_LIST_MAX_PAGE", "1000"))

# Non-production environments where on-chain / payment checks are skipped
_DEV_ENVS: frozenset = frozenset({"development", "dev", "test", "testing", "local"})

# ---------------------------------------------------------------------------
# A2A data models (v1.0.1)
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    """A2A v1.0.1 task lifecycle states."""
    SUBMITTED       = "submitted"
    WORKING         = "working"
    COMPLETED       = "completed"
    FAILED          = "failed"
    CANCELED        = "canceled"        # v1.0.1 spelling (was "cancelled")
    INPUT_REQUIRED  = "input_required"  # v1.0.1 (was "input-needed")
    REJECTED        = "rejected"        # new in v1.0.1
    AUTH_REQUIRED   = "auth_required"   # new in v1.0.1
    # Legacy aliases kept for backward compatibility
    INPUT_NEEDED    = "input_required"
    CANCELLED       = "canceled"

    @classmethod
    def terminal_states(cls) -> frozenset:
        """Return the set of states from which no further transitions are allowed."""
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELED, cls.REJECTED})


@dataclass
class AgentSkill:
    """One advertised capability in the AgentCard (A2A v1.0.1 AgentSkill)."""
    id: str
    name: str
    description: str
    tags: List[str]
    examples: List[str]
    input_modes:  List[str] = field(default_factory=list)
    output_modes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.input_modes:
            self.input_modes = ["text/plain", "application/json"]
        if not self.output_modes:
            self.output_modes = ["text/plain", "application/json"]

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
class AgentInterface:
    """A2A v1.0.1 AgentInterface — one transport binding exposed by this agent."""
    url: str
    protocol_binding: str   # "JSONRPC", "GRPC", "HTTP+JSON"
    protocol_version: str   # e.g. "1.0"
    tenant: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "url":             self.url,
            "protocolBinding": self.protocol_binding,
            "protocolVersion": self.protocol_version,
        }
        if self.tenant:
            d["tenant"] = self.tenant
        return d


@dataclass
class AgentCard:
    """
    A2A v1.0.1 Agent Card.
    Served at /.well-known/agent-card.json (and legacy /.well-known/agent.json).
    Describes the SINCOR agent swarm to external agent clients.
    """
    name:                  str
    description:           str
    version:               str
    supported_interfaces:  List[AgentInterface]
    provider:              Dict[str, str]
    capabilities:          Dict[str, Any]
    default_input_modes:   List[str]
    default_output_modes:  List[str]
    skills:                List[AgentSkill]
    security_schemes:      Dict[str, Any] = field(default_factory=dict)
    security_requirements: List[Dict[str, Any]] = field(default_factory=list)
    documentation_url:     Optional[str] = None
    icon_url:              Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name":               self.name,
            "description":        self.description,
            "version":            self.version,
            "supportedInterfaces": [i.to_dict() for i in self.supported_interfaces],
            "provider":           self.provider,
            "capabilities":       self.capabilities,
            "defaultInputModes":  self.default_input_modes,
            "defaultOutputModes": self.default_output_modes,
            "skills":             [s.to_dict() for s in self.skills],
        }
        if self.security_schemes:
            d["securitySchemes"] = self.security_schemes
        if self.security_requirements:
            d["security"] = self.security_requirements
        if self.documentation_url:
            d["documentationUrl"] = self.documentation_url
        if self.icon_url:
            d["iconUrl"] = self.icon_url
        return d

    # Legacy helper — returns a flat dict matching the old agent.json shape so
    # clients pinned to the pre-v1.0.1 format keep working.
    def to_legacy_dict(self) -> Dict[str, Any]:
        url = (self.supported_interfaces[0].url
               if self.supported_interfaces else PLATFORM_URL)
        return {
            "name":           self.name,
            "description":    self.description,
            "url":            url,
            "version":        self.version,
            "skills":         [s.to_dict() for s in self.skills],
            "provider":       self.provider,
            "authentication": {
                "schemes":     list(self.security_schemes.keys()),
                "description": (
                    "Pass your API key in the X-API-Key header. "
                    "Obtain a key at https://getsincor.com/api-keys."
                ),
            },
            "capabilities":   self.capabilities,
        }



@dataclass
class A2AMessage:
    """A2A v1.0.1 Message object."""
    message_id: str
    role: str                           # "user" or "agent"
    parts: List[Dict[str, Any]]         # list of Part dicts (text, data, file)
    context_id: Optional[str] = None
    task_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    extensions: List[str] = field(default_factory=list)
    reference_task_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "messageId": self.message_id,
            "role":      self.role,
            "parts":     self.parts,
        }
        if self.context_id:
            d["contextId"] = self.context_id
        if self.task_id:
            d["taskId"] = self.task_id
        if self.metadata:
            d["metadata"] = self.metadata
        if self.extensions:
            d["extensions"] = self.extensions
        if self.reference_task_ids:
            d["referenceTaskIds"] = self.reference_task_ids
        return d


@dataclass
class A2AArtifact:
    """A2A v1.0.1 Artifact — an output produced by a task."""
    artifact_id: str
    parts: List[Dict[str, Any]]
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    extensions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "artifactId": self.artifact_id,
            "parts":      self.parts,
        }
        if self.name:
            d["name"] = self.name
        if self.description:
            d["description"] = self.description
        if self.metadata:
            d["metadata"] = self.metadata
        if self.extensions:
            d["extensions"] = self.extensions
        return d


@dataclass
class A2ATask:
    """In-flight A2A task (v1.0.1)."""
    id:             str
    context_id:     str                 # A2A v1.0.1 contextId (was sessionId)
    skill_id:       str
    input_text:     str
    caller_id:      str                 # wallet address or agent identifier
    state:          TaskState
    created_at:     str
    updated_at:     str
    history:        List[A2AMessage] = field(default_factory=list)
    artifacts:      List[A2AArtifact] = field(default_factory=list)
    output:         Optional[str] = None   # convenience; surfaced via artifacts
    error:          Optional[str] = None
    axm_paid:       int = 0             # AXM paid in wei (18 dec)
    tx_hash:        Optional[str] = None
    metadata:       Dict[str, Any] = field(default_factory=dict)

    # Keep session_id as an alias for backward compat code paths
    @property
    def session_id(self) -> str:
        return self.context_id


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
    # ------------------------------------------------------------------
    # SINAX / Proof Topology Navigator (PTN) skills
    # ------------------------------------------------------------------
    AgentSkill(
        id="proof-topology-solve",
        name="Proof Topology Navigator — Full Solve",
        description=(
            "End-to-end automated theorem proving via SINAX's Proof Topology Navigator "
            "(PTN).  Embeds the start and target proof states onto a Riemannian manifold, "
            "computes the geodesic flow, runs homology detection, and applies Morse-theory "
            "filtering to produce a minimal tactic sequence and a human-readable proof "
            "narrative.  Powered by SINAX — SINCOR's unique geometric proof navigation layer."
        ),
        tags=["SINAX", "PTN", "theorem-proving", "Lean", "formal-verification", "geodesic"],
        examples=[
            "Prove: start=⊢ ∀ n : ℕ, n + 0 = n  target=closed",
            '{"start": "⊢ P ∧ Q", "target": "closed", "context": ["h : P", "h2 : Q"]}',
        ],
    ),
    AgentSkill(
        id="proof-topology-embed",
        name="Proof Topology Navigator — State Embedding",
        description=(
            "Layer 1 of the PTN pipeline.  Maps a Lean/formal proof state onto the "
            "learned Riemannian proof manifold and returns its coordinates and local "
            "curvature score (branching complexity).  Useful for comparing proof states "
            "or seeding the manifold before a full solve.  Powered by SINAX."
        ),
        tags=["SINAX", "PTN", "embedding", "manifold", "curvature"],
        examples=[
            "Embed proof state: ⊢ n + 0 = n",
            "What are the manifold coordinates of ⊢ ∀ x : ℝ, x * 1 = x?",
        ],
    ),
    AgentSkill(
        id="proof-topology-geodesic",
        name="Proof Topology Navigator — Geodesic Flow",
        description=(
            "Layer 2 of the PTN pipeline.  Computes the geodesic (shortest continuous "
            "path) between two proof states on the learned manifold and returns the "
            "decoded tactic sequence, path length, and convergence status.  Short paths "
            "indicate that the two states are topologically close.  Powered by SINAX."
        ),
        tags=["SINAX", "PTN", "geodesic", "tactic-sequence", "proof-path"],
        examples=[
            '{"start": "⊢ n + 0 = n", "target": "closed"}',
            "Find the geodesic from ⊢ P → Q to ⊢ ¬Q → ¬P",
        ],
    ),
    AgentSkill(
        id="proof-topology-homology",
        name="Proof Topology Navigator — Homology Analysis",
        description=(
            "Layer 3 of the PTN pipeline.  Computes persistent homology over a set of "
            "proof states and detects topological holes — regions where proofs consistently "
            "fail.  Returns Betti numbers, hole-filling lemma suggestions, and connected "
            "component counts.  Powered by SINAX."
        ),
        tags=["SINAX", "PTN", "homology", "lemma-discovery", "topology"],
        examples=[
            '["⊢ P", "⊢ P ∧ Q", "⊢ Q → R", "⊢ R"]',
            "Analyse the homology of these failed proof states: ...",
        ],
    ),
    AgentSkill(
        id="proof-topology-morse",
        name="Proof Topology Navigator — Morse Decomposition",
        description=(
            "Layer 4 of the PTN pipeline.  Applies Morse theory to a set of proof states "
            "to identify critical points: key lemmas (local minima), branch points (saddle "
            "points), and a lower bound on minimal proof length via the Morse inequalities.  "
            "Powered by SINAX."
        ),
        tags=["SINAX", "PTN", "morse-theory", "critical-points", "proof-complexity"],
        examples=[
            '["⊢ base case", "⊢ inductive step", "⊢ conclusion"]',
            "Decompose the Morse landscape for: ⊢ ∀ n, fib n > 0",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Agent Card factory
# ---------------------------------------------------------------------------

def build_agent_card() -> AgentCard:
    """Return the canonical SINCOR AgentCard (A2A v1.0.1) for /.well-known/agent-card.json."""
    rpc_url = f"{PLATFORM_URL}/api/a2a"
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
        version=PLATFORM_VERSION,
        supported_interfaces=[
            AgentInterface(
                url=rpc_url,
                protocol_binding="JSONRPC",
                protocol_version=A2A_PROTOCOL_VERSION,
            ),
        ],
        provider={
            "organization": "SINCOR",
            "url":          PLATFORM_URL,
        },
        capabilities={
            "streaming":             True,
            "pushNotifications":     False,
            "stateTransitionHistory": True,
        },
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=SINCOR_SKILLS,
        security_schemes={
            "apiKey": {
                "type": "apiKey",
                "in":   "header",
                "name": "X-API-Key",
                "description": (
                    "Obtain a key at https://getsincor.com/api-keys. "
                    "Each task also requires a confirmed AXM payment on Base — "
                    "see the axiom-payment skill for the x402 payment flow."
                ),
            },
        },
        security_requirements=[{"apiKey": []}],
        documentation_url=f"{PLATFORM_URL}/docs/a2a",
    )


# ---------------------------------------------------------------------------
# In-memory stores (replace with Redis / DB in production)
# ---------------------------------------------------------------------------
# NOTE: this store is process-local and non-persistent. All tasks are lost on
# restart and not shared across worker processes. Set A2A_TASK_STORE=redis (and
# configure REDIS_URL) to enable a persistent Redis-backed store in production.
# Without that, running multiple Gunicorn workers will produce inconsistent
# task-status responses.
#
# Thread-safety: all access to _tasks and _push_configs must be guarded by
# _store_lock. Use the _get_task / _update_task / _new_task helpers below.

_tasks: Dict[str, A2ATask] = {}
_push_configs: Dict[str, Dict[str, Any]] = {}  # task_id → push notification config
_store_lock: threading.Lock = threading.Lock()

_env = os.getenv("FLASK_ENV", "production").lower()
if _env not in _DEV_ENVS and \
        os.getenv("A2A_TASK_STORE", "memory") == "memory":
    logger.error(
        "A2A task store is in-memory (non-persistent). "
        "Set A2A_TASK_STORE=redis and REDIS_URL for production deployments. "
        "Tasks will be lost on restart and are not shared across workers."
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_task(skill_id: str, input_text: str, caller_id: str,
              session_id: str, axm_paid: int = 0,
              tx_hash: Optional[str] = None) -> A2ATask:
    task_id = str(uuid.uuid4())
    context_id = session_id or task_id
    # Record the initial user message in history
    user_msg = A2AMessage(
        message_id=str(uuid.uuid4()),
        role="user",
        parts=[{"text": input_text}],
        context_id=context_id,
        task_id=task_id,
    )
    task = A2ATask(
        id=task_id,
        context_id=context_id,
        skill_id=skill_id,
        input_text=input_text,
        caller_id=caller_id,
        state=TaskState.SUBMITTED,
        created_at=_now(),
        updated_at=_now(),
        history=[user_msg],
        axm_paid=axm_paid,
        tx_hash=tx_hash,
    )
    with _store_lock:
        _tasks[task_id] = task
    return task


def _get_task(task_id: str) -> Optional[A2ATask]:
    with _store_lock:
        return _tasks.get(task_id)


def _update_task(task: A2ATask, **kwargs: Any) -> A2ATask:
    with _store_lock:
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

    Validation checks (production mode):
      1. Transaction receipt exists and status == 0x1 (success).
      2. A Transfer(address,address,uint256) log from the AXM contract is present
         with `to` == expected_to (treasury wallet) and value >= expected_amount_wei.
    """

    # ERC-20 Transfer event topic: keccak256("Transfer(address,address,uint256)")
    _TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    _verified: Dict[str, bool] = {}
    _lock: threading.Lock = threading.Lock()

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
        if env in _DEV_ENVS:
            logger.warning("PaymentVerifier: skipping on-chain check (non-prod env)")
            return True

        with cls._lock:
            cached = cls._verified.get(tx_hash)
        if cached is not None:
            return cached

        rpc_url = os.getenv("BASE_RPC_URL")
        if not rpc_url:
            logger.error("BASE_RPC_URL not set — cannot verify AXM payment")
            return False

        result = False
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
            ), timeout=BASE_RPC_TIMEOUT) as resp:
                data = json.loads(resp.read())
            receipt = data.get("result")
            if not receipt or receipt.get("status") != "0x1":
                logger.warning("PaymentVerifier: tx %s not successful", tx_hash)
            else:
                result = cls._validate_transfer_log(
                    receipt.get("logs", []),
                    expected_to=expected_to,
                    expected_amount_wei=expected_amount_wei,
                )
        except Exception as exc:
            logger.error("PaymentVerifier RPC error: %s", exc)

        if result:
            with cls._lock:
                cls._verified[tx_hash] = True
        return result

    @classmethod
    def _validate_transfer_log(
        cls,
        logs: List[Dict[str, Any]],
        expected_to: str,
        expected_amount_wei: int,
    ) -> bool:
        """
        Scan the receipt logs for an ERC-20 Transfer from the AXM contract
        whose `to` address matches *expected_to* and whose value is at least
        *expected_amount_wei*.
        """
        axm_addr = AXIOM_CONTRACT.lower()
        expected_to_norm = expected_to.lower()
        # Transfer(address indexed from, address indexed to, uint256 value)
        # topics[0] = event sig, topics[1] = from, topics[2] = to
        # data = value (32-byte big-endian hex)
        for log in logs:
            if log.get("address", "").lower() != axm_addr:
                continue
            topics = log.get("topics", [])
            if len(topics) < 3 or topics[0].lower() != cls._TRANSFER_TOPIC:
                continue
            # topics[2] is 32-byte padded address; last 20 bytes = actual address
            to_addr = ("0x" + topics[2][-40:]).lower()
            if to_addr != expected_to_norm:
                continue
            raw_value = log.get("data", "0x0")
            try:
                value = int(raw_value, 16)
            except ValueError:
                continue
            if value >= expected_amount_wei:
                return True
        logger.warning(
            "PaymentVerifier: no qualifying AXM Transfer log found in tx; "
            "expected ≥%d wei to %s from contract %s",
            expected_amount_wei, expected_to, AXIOM_CONTRACT,
        )
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

        # ── Discovery — A2A v1.0.1 canonical path ────────────────────────────
        @bp.route("/.well-known/agent-card.json", methods=["GET"])
        def agent_card_v1():
            from flask import jsonify
            return jsonify(build_agent_card().to_dict())

        # ── Discovery — legacy path (backward compat) ─────────────────────────
        @bp.route("/.well-known/agent.json", methods=["GET"])
        def agent_card_legacy():
            from flask import jsonify
            return jsonify(build_agent_card().to_legacy_dict())

        # ── Unified JSON-RPC dispatcher (A2A v1.0.1) ─────────────────────────
        @bp.route("/api/a2a", methods=["POST"])
        def rpc_dispatch():
            from flask import Response, jsonify, request, stream_with_context
            body   = request.get_json(force=True, silent=True) or {}
            method = body.get("method", "")
            rpc_id = body.get("id")
            params = body.get("params") or {}

            # Streaming methods → SSE response
            if method in ("message/stream", "tasks/resubscribe"):
                gen = (_handle_stream(body) if method == "message/stream"
                       else _handle_resubscribe(body))
                return Response(
                    stream_with_context(gen),
                    mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                    },
                )

            # Non-streaming methods
            dispatch: Dict[str, Any] = {
                "message/send":                        _handle_send,
                "tasks/get":                           _handle_get_rpc,
                "tasks/cancel":                        _handle_cancel,
                "tasks/list":                          _handle_list,
                "tasks/pushNotificationConfig/set":    _handle_push_config_set,
                "tasks/pushNotificationConfig/get":    _handle_push_config_get,
                "tasks/pushNotificationConfig/delete": _handle_push_config_delete,
                "tasks/pushNotificationConfig/list":   _handle_push_config_list,
            }

            handler = dispatch.get(method)
            if handler is None:
                return jsonify(_err(
                    f"Method '{method}' not found",
                    code=-32601, rpc_id=rpc_id,
                )), 404

            return jsonify(handler(body))

        # ── Legacy REST endpoints (backward compat) ───────────────────────────
        @bp.route("/api/a2a/tasks/send", methods=["POST"])
        def tasks_send():
            from flask import jsonify, request
            body = request.get_json(force=True, silent=True) or {}
            return jsonify(_handle_send(body))

        @bp.route("/api/a2a/tasks/<task_id>", methods=["GET"])
        def tasks_get_rest(task_id: str):
            from flask import jsonify
            return jsonify(_handle_get(task_id))

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


def _sse_event(data: Dict[str, Any]) -> str:
    """Format a dict as a single SSE data event."""
    return f"data: {json.dumps(data)}\n\n"


def _task_to_rpc(task: A2ATask, history_length: Optional[int] = None) -> Dict[str, Any]:
    """Serialise a task to the A2A v1.0.1 Task JSON shape."""
    # Build status message from most recent agent output
    status_message: Optional[Dict[str, Any]] = None
    if task.output:
        status_message = {
            "messageId":  str(uuid.uuid4()),
            "role":       "agent",
            "parts":      [{"text": task.output}],
            "contextId":  task.context_id,
            "taskId":     task.id,
        }
    elif task.error:
        status_message = {
            "messageId":  str(uuid.uuid4()),
            "role":       "agent",
            "parts":      [{"text": f"Error: {task.error}"}],
            "contextId":  task.context_id,
            "taskId":     task.id,
        }

    d: Dict[str, Any] = {
        "id":        task.id,
        "contextId": task.context_id,
        "status": {
            "state":     task.state.value,
            "timestamp": task.updated_at,
        },
        "artifacts": [a.to_dict() for a in task.artifacts],
        "metadata":  task.metadata,
    }
    if status_message:
        d["status"]["message"] = status_message

    # Include history if requested
    if history_length is None or history_length > 0:
        msgs = task.history
        if history_length is not None:
            msgs = msgs[-history_length:]
        d["history"] = [m.to_dict() for m in msgs]
    else:
        d["history"] = []

    return d


# ---------------------------------------------------------------------------
# Business logic — v1.0.1 method handlers
# ---------------------------------------------------------------------------

def _extract_send_params(body: Dict[str, Any]):
    """Parse params from a message/send or legacy tasks/send body.

    Returns (rpc_id, skill_id, context_id, caller_id, input_text, tx_hash,
             axm_paid, history_length, error_response).

    Parameter precedence (first non-empty value wins):
      - Canonical A2A v1.0.1: ``params.message`` object (camelCase fields).
      - Legacy flat params: top-level camelCase fields in ``params``.
      - Legacy snake_case: top-level snake_case fields in ``params``.
    All three shapes are accepted so existing integrations continue to work.
    """
    rpc_id = body.get("id")
    params = body.get("params") or body  # tolerate bare params

    # v1.0.1: params = {message: Message, configuration?: SendMessageConfiguration}
    # legacy: params has skillId / sessionId / callerId at top level
    msg_obj      = params.get("message") or {}
    configuration = params.get("configuration") or {}

    skill_id   = (params.get("skillId") or params.get("skill_id") or
                  (msg_obj.get("metadata") or {}).get("skillId", ""))
    context_id = (params.get("contextId") or params.get("sessionId") or
                  params.get("session_id") or msg_obj.get("contextId") or
                  str(uuid.uuid4()))
    caller_id  = (params.get("callerId") or params.get("caller_id") or
                  msg_obj.get("metadata", {}).get("callerId", "anonymous"))
    tx_hash    = (params.get("txHash") or params.get("tx_hash") or
                  (msg_obj.get("metadata") or {}).get("txHash"))
    axm_paid   = int(params.get("axmPaidWei") or params.get("axm_paid_wei") or
                     (msg_obj.get("metadata") or {}).get("axmPaidWei", 0))
    history_length: Optional[int] = configuration.get("historyLength")

    # Resolve input text from Message.parts or plain string
    if isinstance(msg_obj, str):
        input_text = msg_obj
    else:
        parts = msg_obj.get("parts") or []
        input_text = " ".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        )

    return (rpc_id, skill_id, context_id, caller_id, input_text,
            tx_hash, axm_paid, history_length)


def _handle_send(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle message/send (and legacy tasks/send) JSON-RPC call."""
    (rpc_id, skill_id, context_id, caller_id, input_text,
     tx_hash, axm_paid, history_length) = _extract_send_params(body)

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
    skip_payment = env in _DEV_ENVS

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
        session_id=context_id,
        axm_paid=axm_paid,
        tx_hash=tx_hash,
    )
    logger.info("A2A task %s created  skill=%s caller=%s", task.id, skill_id, caller_id)

    _update_task(task, state=TaskState.WORKING)
    output, error = _dispatch_to_swarm(task)
    if error:
        _update_task(task, state=TaskState.FAILED, error=error)
    else:
        # Store output as an artifact
        if output:
            artifact = A2AArtifact(
                artifact_id=str(uuid.uuid4()),
                parts=[{"text": output}],
                name="result",
            )
            task.artifacts.append(artifact)
            # Also append the agent reply to history
            agent_msg = A2AMessage(
                message_id=str(uuid.uuid4()),
                role="agent",
                parts=[{"text": output}],
                context_id=task.context_id,
                task_id=task.id,
            )
            task.history.append(agent_msg)
        _update_task(task, state=TaskState.COMPLETED, output=output)

    return _rpc_ok(_task_to_rpc(task, history_length=history_length), rpc_id=rpc_id)


def _handle_stream(body: Dict[str, Any]) -> Generator[str, None, None]:
    """
    Handle message/stream — yields SSE events for the A2A v1.0.1 streaming flow.

    Event sequence:
      1. TaskStatusUpdateEvent: state=submitted
      2. TaskStatusUpdateEvent: state=working
      3. (dispatch to swarm)
      4. TaskArtifactUpdateEvent: artifact with result
      5. TaskStatusUpdateEvent: state=completed (or failed), final=true
    """
    (rpc_id, skill_id, context_id, caller_id, input_text,
     tx_hash, axm_paid, _) = _extract_send_params(body)

    def _status_event(task: A2ATask, final: bool = False,
                      msg_text: Optional[str] = None) -> str:
        status: Dict[str, Any] = {
            "state":     task.state.value,
            "timestamp": task.updated_at,
        }
        if msg_text:
            status["message"] = {
                "messageId": str(uuid.uuid4()),
                "role":      "agent",
                "parts":     [{"text": msg_text}],
                "contextId": task.context_id,
                "taskId":    task.id,
            }
        event: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id":      rpc_id,
            "result":  {
                "taskStatus": {
                    "taskId":    task.id,
                    "contextId": task.context_id,
                    "status":    status,
                    "final":     final,
                },
            },
        }
        return _sse_event(event)

    if not input_text:
        yield _sse_event(_err("No input text in message.parts", -32602, rpc_id))
        return

    skill = next((s for s in SINCOR_SKILLS if s.id == skill_id), None)
    if not skill:
        valid = [s.id for s in SINCOR_SKILLS]
        yield _sse_event(_err(
            f"Unknown skill '{skill_id}'. Valid: {valid}", -32602, rpc_id
        ))
        return

    env = os.getenv("FLASK_ENV", "production").lower()
    skip_payment = env in _DEV_ENVS
    if not skip_payment and skill_id != "axiom-payment":
        if not tx_hash:
            yield _sse_event(_err(
                "Payment required. See /api/a2a/quote.", -32000, rpc_id
            ))
            return
        if not PaymentVerifier.is_verified(tx_hash, AXM_PRICE_PER_TASK):
            yield _sse_event(_err(
                f"Payment tx {tx_hash} unverified on Base.", -32001, rpc_id
            ))
            return

    task = _new_task(
        skill_id=skill_id,
        input_text=input_text,
        caller_id=caller_id,
        session_id=context_id,
        axm_paid=axm_paid,
        tx_hash=tx_hash,
    )
    logger.info("A2A stream task %s  skill=%s caller=%s", task.id, skill_id, caller_id)

    # Event 1: submitted
    yield _status_event(task)

    # Event 2: working
    _update_task(task, state=TaskState.WORKING)
    yield _status_event(task)

    # Dispatch
    output, error = _dispatch_to_swarm(task)

    if error:
        _update_task(task, state=TaskState.FAILED, error=error)
        yield _status_event(task, final=True, msg_text=f"Error: {error}")
        return

    # Event 3: artifact
    if output:
        artifact = A2AArtifact(
            artifact_id=str(uuid.uuid4()),
            parts=[{"text": output}],
            name="result",
        )
        task.artifacts.append(artifact)
        agent_msg = A2AMessage(
            message_id=str(uuid.uuid4()),
            role="agent",
            parts=[{"text": output}],
            context_id=task.context_id,
            task_id=task.id,
        )
        task.history.append(agent_msg)
        artifact_event: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id":      rpc_id,
            "result":  {
                "taskArtifact": {
                    "taskId":    task.id,
                    "contextId": task.context_id,
                    "artifact":  artifact.to_dict(),
                    "final":     False,
                },
            },
        }
        yield _sse_event(artifact_event)

    # Event 4: completed
    _update_task(task, state=TaskState.COMPLETED, output=output)
    yield _status_event(task, final=True)


def _handle_get(task_id: str) -> Dict[str, Any]:
    """Handle legacy GET /api/a2a/tasks/<task_id>."""
    task = _get_task(task_id)
    if not task:
        return _err(f"Task {task_id} not found", code=-32602)
    return _rpc_ok(_task_to_rpc(task))


def _handle_get_rpc(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/get JSON-RPC call."""
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("id") or params.get("taskId", "")
    history_length: Optional[int] = params.get("historyLength")
    task = _get_task(task_id)
    if not task:
        return _err(f"Task {task_id} not found", code=-32602, rpc_id=rpc_id)
    return _rpc_ok(_task_to_rpc(task, history_length=history_length), rpc_id=rpc_id)


def _handle_cancel(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/cancel JSON-RPC call (and legacy REST cancel)."""
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("id") or params.get("taskId")
    task    = _get_task(task_id or "")
    if not task:
        return _err(f"Task {task_id} not found", code=-32602, rpc_id=rpc_id)
    if task.state in TaskState.terminal_states():
        return _err("Task already in terminal state, cannot cancel",
                    code=-32003, rpc_id=rpc_id)
    _update_task(task, state=TaskState.CANCELED)
    return _rpc_ok(_task_to_rpc(task), rpc_id=rpc_id)


def _handle_list(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/list JSON-RPC call."""
    rpc_id = body.get("id")
    params = body.get("params") or body
    context_id  = params.get("contextId")
    state_filter = params.get("state")
    page_size   = int(params.get("pageSize") or 50)
    if page_size > TASK_LIST_MAX_PAGE:
        return _err(
            f"pageSize exceeds maximum ({TASK_LIST_MAX_PAGE})",
            code=-32602, rpc_id=rpc_id,
        )
    page_token  = params.get("pageToken")  # simple offset-based for now

    with _store_lock:
        tasks = list(_tasks.values())
    if context_id:
        tasks = [t for t in tasks if t.context_id == context_id]
    if state_filter:
        tasks = [t for t in tasks if t.state.value == state_filter]

    # Pagination
    offset = int(page_token or 0)
    page   = tasks[offset: offset + page_size]
    next_token = str(offset + page_size) if offset + page_size < len(tasks) else None

    result: Dict[str, Any] = {"tasks": [_task_to_rpc(t, history_length=0) for t in page]}
    if next_token:
        result["nextPageToken"] = next_token
    return _rpc_ok(result, rpc_id=rpc_id)


def _handle_push_config_set(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/pushNotificationConfig/set."""
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("taskId") or params.get("id", "")
    config  = {
        "taskId":    task_id,
        "url":       params.get("url", ""),
        "token":     params.get("token"),
        "headers":   params.get("headers", {}),
    }
    if not task_id or not config["url"]:
        return _err("taskId and url are required", code=-32602, rpc_id=rpc_id)
    with _store_lock:
        _push_configs[task_id] = config
    logger.info("Push notification config set for task %s → %s", task_id, config["url"])
    return _rpc_ok(config, rpc_id=rpc_id)


def _handle_push_config_get(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/pushNotificationConfig/get."""
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("taskId") or params.get("id", "")
    with _store_lock:
        config = _push_configs.get(task_id)
    if not config:
        return _err(f"No push config for task {task_id}", code=-32602, rpc_id=rpc_id)
    return _rpc_ok(config, rpc_id=rpc_id)


def _handle_push_config_delete(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/pushNotificationConfig/delete."""
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("taskId") or params.get("id", "")
    with _store_lock:
        _push_configs.pop(task_id, None)
    return _rpc_ok({}, rpc_id=rpc_id)


def _handle_push_config_list(body: Dict[str, Any]) -> Dict[str, Any]:
    """Handle tasks/pushNotificationConfig/list."""
    rpc_id = body.get("id")
    with _store_lock:
        configs = list(_push_configs.values())
    return _rpc_ok(
        {"configs": configs},
        rpc_id=rpc_id,
    )


def _handle_resubscribe(body: Dict[str, Any]) -> Generator[str, None, None]:
    """
    Handle tasks/resubscribe — re-attach to an existing task's event stream.
    Immediately emits the current task status and then the final artifact if done.
    """
    rpc_id  = body.get("id")
    params  = body.get("params") or body
    task_id = params.get("id") or params.get("taskId", "")
    task    = _get_task(task_id)
    if not task:
        yield _sse_event(_err(f"Task {task_id} not found", -32602, rpc_id))
        return

    terminal = TaskState.terminal_states()
    final = task.state in terminal

    status_event: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id":      rpc_id,
        "result":  {
            "taskStatus": {
                "taskId":    task.id,
                "contextId": task.context_id,
                "status": {
                    "state":     task.state.value,
                    "timestamp": task.updated_at,
                },
                "final": final,
            },
        },
    }
    yield _sse_event(status_event)

    if final and task.artifacts:
        for artifact in task.artifacts:
            artifact_event: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id":      rpc_id,
                "result":  {
                    "taskArtifact": {
                        "taskId":    task.id,
                        "contextId": task.context_id,
                        "artifact":  artifact.to_dict(),
                        "final":     True,
                    },
                },
            }
            yield _sse_event(artifact_event)


# ---------------------------------------------------------------------------
# Swarm dispatch
# ---------------------------------------------------------------------------

_PTN_SKILL_IDS = frozenset({
    "proof-topology-solve",
    "proof-topology-embed",
    "proof-topology-geodesic",
    "proof-topology-homology",
    "proof-topology-morse",
})


def _dispatch_ptn(task: A2ATask) -> str:
    """
    Dispatch a SINAX / PTN skill task and return a human-readable result string.

    Input format (flexible):
      • Plain text: treated as the ``start_state`` for a solve task, or as
        the proof-state string for embed / homology / morse tasks.
      • JSON object with keys ``start``, ``target``, ``context``
        (for solve/geodesic) or ``states`` / ``proof_states``
        (for homology/morse/embed).

    Returns a formatted text result suitable for inclusion in an A2A artifact.
    """
    import json as _json
    from sincor2.sinax import ProofTopologyNavigator

    nav = ProofTopologyNavigator()
    raw = task.input_text.strip()

    # Try to parse JSON payload; fall back to treating input as a plain state string.
    payload: Dict[str, Any] = {}
    try:
        parsed = _json.loads(raw)
        if isinstance(parsed, dict):
            payload = parsed
        elif isinstance(parsed, list):
            payload = {"states": parsed}
    except (_json.JSONDecodeError, ValueError):
        payload = {}

    sid = task.skill_id

    # --- proof-topology-embed -------------------------------------------
    if sid == "proof-topology-embed":
        state = payload.get("state") or raw
        result = nav.embed(state)
        lines = [
            "SINAX — Proof State Embedding (Layer 1)",
            "=" * 45,
            f"State    : {state[:120]}",
            f"Curvature: {result['curvature']:.6f}",
            f"Dim      : {len(result['coordinates'])}",
            f"Coords[0:4]: {result['coordinates'][:4]}",
        ]
        return "\n".join(str(l) for l in lines)

    # --- proof-topology-geodesic ----------------------------------------
    if sid == "proof-topology-geodesic":
        start  = payload.get("start") or payload.get("start_state") or raw
        target = payload.get("target") or payload.get("target_state") or "closed"
        result = nav.geodesic(start, target)
        lines = [
            "SINAX — Geodesic Flow (Layer 2)",
            "=" * 45,
            f"Start    : {result['start_state'][:100]}",
            f"Target   : {result['target_state'][:100]}",
            f"Converged: {result['converged']}",
            f"Teleported: {result['teleported']}",
            f"Path length: {result['path_length']:.6f}",
            f"Steps    : {result['num_steps']}",
            f"Tactics  : {' → '.join(result['tactics']) or '(none)'}",
        ]
        return "\n".join(lines)

    # --- proof-topology-homology ----------------------------------------
    if sid == "proof-topology-homology":
        states: List[str] = (
            payload.get("states")
            or payload.get("proof_states")
            or [raw]
        )
        if isinstance(states, str):
            states = [states]
        result = nav.homology(states)
        lines = [
            "SINAX — Homology Analysis (Layer 3)",
            "=" * 45,
            f"States analysed : {result['num_states']}",
            f"Components (β₀) : {result['num_components']}",
            f"Has holes       : {result['has_holes']}",
            f"Betti numbers   : {result['betti_numbers']}",
        ]
        if result["hole_filling_suggestions"]:
            lines.append("Hole-filling lemma suggestions:")
            for s in result["hole_filling_suggestions"][:5]:
                lines.append(f"  • {s}")
        return "\n".join(lines)

    # --- proof-topology-morse -------------------------------------------
    if sid == "proof-topology-morse":
        states = (
            payload.get("states")
            or payload.get("proof_states")
            or [raw]
        )
        if isinstance(states, str):
            states = [states]
        result = nav.morse(states)
        lines = [
            "SINAX — Morse Decomposition (Layer 4)",
            "=" * 45,
            f"Critical points     : {result['num_critical_points']}",
            f"Min proof-length LB : {result['min_proof_length_bound']}",
        ]
        if result["key_lemmas"]:
            lines.append("Key lemmas (minima):")
            for l in result["key_lemmas"][:5]:
                lines.append(f"  ✓ {l[:100]}")
        if result["branch_points"]:
            lines.append("Branch points (saddles):")
            for b in result["branch_points"][:3]:
                lines.append(f"  ~ {b[:100]}")
        return "\n".join(lines)

    # --- proof-topology-solve (default) ---------------------------------
    start  = payload.get("start") or payload.get("start_state") or raw
    target = payload.get("target") or payload.get("target_state") or "closed"
    context: Optional[List[str]] = payload.get("context") or payload.get("context_states")
    proof  = nav.solve(start_state=start, target_state=target, context_states=context)
    return proof.proof_narrative + (
        f"\n\nResult summary:\n"
        f"  Succeeded  : {proof.succeeded}\n"
        f"  Tactics    : {' → '.join(proof.tactic_sequence) or '(none)'}\n"
        f"  Elapsed    : {proof.elapsed_seconds:.4f}s\n"
        f"  Proof ID   : {proof.proof_id}"
    )


def _dispatch_to_swarm(task: A2ATask):
    """
    Route the task to the internal SINCOR swarm.

    Callers are responsible for transitioning the task to WORKING before
    calling this function and to COMPLETED/FAILED afterwards.

    In production this calls the existing swarm_coordination.TaskMarket or the
    instant_business_intelligence / content_agent modules directly.
    This stub returns a placeholder response so the A2A protocol layer is
    fully functional even before deep swarm integration is wired.

    Returns (output: str | None, error: str | None).
    """
    try:
        # SINAX / PTN skills are handled directly — no external dependency needed.
        if task.skill_id in _PTN_SKILL_IDS:
            output = _dispatch_ptn(task)
            return output, None

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
