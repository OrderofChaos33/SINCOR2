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

# 2026-08-19: SINC updated to new 8-decimal live contract
AXIOM_CONTRACT   = os.getenv("AXIOM_CONTRACT_ADDRESS", "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822")
SINC_CONTRACT    = os.getenv("SINC_CONTRACT_ADDRESS",  "0xe1D836087F6573b665d25CE088793E916D7892f8")
TREASURY_WALLET  = os.getenv("TREASURY_ADDRESS",       "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac")
DEAD_ADDRESS     = "0x000000000000000000000000000000000000dEaD"
CHAIN_ID         = int(os.getenv("BASE_CHAIN_ID", "8453"))  # Base mainnet

# Primary token for A2A task payments. Default is SINC; set A2A_PRIMARY_TOKEN=AXIOM
# for legacy AXIOM-based settlements.
A2A_PRIMARY_TOKEN = os.getenv("A2A_PRIMARY_TOKEN", "SINC").upper()

# SINC price per A2A task call (whole tokens; contract is 8 decimals).
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
    # Pricing fields (set per-skill; fall back to global defaults if zero)
    axm_price_wei: int = 0          # AXM payment amount in wei (18 decimals)
    sinc_price: int = 0             # SINC payment amount (whole tokens)
    # Input/output JSON Schema (draft-07 subset; empty dict = freeform)
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    # Estimated execution latency in seconds (0 = unspecified)
    estimated_latency_seconds: int = 0
    # Minimum caller reputation score required (0 = open to all)
    reputation_floor: int = 0
    # Number of free calls granted to new verified A2A callers (0 = no free quota)
    free_quota: int = 0

    def __post_init__(self) -> None:
        if not self.input_modes:
            self.input_modes = ["text/plain", "application/json"]
        if not self.output_modes:
            self.output_modes = ["text/plain", "application/json"]
        # Back-fill global defaults when per-skill prices are not set
        if self.axm_price_wei == 0:
            self.axm_price_wei = AXM_PRICE_PER_TASK
        if self.sinc_price == 0:
            self.sinc_price = SINC_PRICE_PER_TASK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":                       self.id,
            "name":                     self.name,
            "description":              self.description,
            "tags":                     self.tags,
            "examples":                 self.examples,
            "inputModes":               self.input_modes,
            "outputModes":              self.output_modes,
            "axmPriceWei":              str(self.axm_price_wei),
            "axmPriceDisplay":          f"{self.axm_price_wei / 10**18:.4f} AXM",
            "sincPrice":                self.sinc_price,
            "inputSchema":              self.input_schema,
            "outputSchema":             self.output_schema,
            "estimatedLatencySeconds":  self.estimated_latency_seconds,
            "reputationFloor":          self.reputation_floor,
            "freeQuota":                self.free_quota,
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
    # ── P0 Priority Skills (fully subsidised free quota) ──────────────────
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
        axm_price_wei=int(2.5 * 10**18),   # 2.5 AXM → 30-50% margin after 50% burn
        sinc_price=3,
        input_schema={
            "type": "object",
            "required": ["company"],
            "properties": {
                "company": {"type": "string", "description": "Company name or domain"},
                "segment": {"type": "string", "description": "Target ICP segment"},
                "enrichment_fields": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Fields to enrich: email, linkedin, revenue, headcount, …",
                },
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "leads": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "company": {"type": "string"},
                            "contact_name": {"type": "string"},
                            "email": {"type": "string"},
                            "fit_score": {"type": "number", "minimum": 0, "maximum": 100},
                            "outreach_draft": {"type": "string"},
                        },
                    },
                },
            },
        },
        estimated_latency_seconds=30,
        reputation_floor=0,
        free_quota=FREE_QUOTA_PER_CALLER,
    ),
    AgentSkill(
        id="competitor-intel",
        name="Competitor Intelligence & SWOT",
        description=(
            "Rapid competitor scans, positioning gap analysis, SWOT generation, and "
            "market landscape summaries. Powered by Scout-archetype agents."
        ),
        tags=["market", "competitive-analysis", "research", "SINC", "AXIOM"],
        examples=[
            "Give me a competitive landscape for AI infrastructure startups in 2026.",
            "Who are the top 5 competitors to a DeFi yield aggregator on Base?",
        ],
        axm_price_wei=int(2.0 * 10**18),
        sinc_price=2,
        input_schema={
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {"type": "string", "description": "Company or product to analyse"},
                "market": {"type": "string", "description": "Market / industry vertical"},
                "depth": {"type": "string", "enum": ["quick", "detailed"],
                          "description": "quick=top-5 summary, detailed=full SWOT"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "competitors": {"type": "array", "items": {"type": "object"}},
                "swot": {"type": "object",
                         "properties": {
                             "strengths": {"type": "array", "items": {"type": "string"}},
                             "weaknesses": {"type": "array", "items": {"type": "string"}},
                             "opportunities": {"type": "array", "items": {"type": "string"}},
                             "threats": {"type": "array", "items": {"type": "string"}},
                         }},
                "summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=25,
        reputation_floor=0,
        free_quota=FREE_QUOTA_PER_CALLER,
    ),
    AgentSkill(
        id="outreach-sequence",
        name="Outreach Sequence Builder",
        description=(
            "Generate multi-touch outbound sequences (email, LinkedIn, SMS) tailored "
            "to a specific ICP. Powered by Negotiator + Builder agents."
        ),
        tags=["sales", "outreach", "email", "linkedin", "sequence"],
        examples=[
            "Build a 5-touch email sequence for SMB dental practices in Texas.",
            "Write a LinkedIn InMail sequence for Series-A CFOs interested in AI finance tools.",
        ],
        axm_price_wei=int(3.0 * 10**18),
        sinc_price=3,
        input_schema={
            "type": "object",
            "required": ["icp", "offer"],
            "properties": {
                "icp": {"type": "string", "description": "Ideal customer profile"},
                "offer": {"type": "string", "description": "Value proposition to communicate"},
                "touches": {"type": "integer", "minimum": 1, "maximum": 10,
                            "description": "Number of sequence steps"},
                "channels": {"type": "array", "items": {"type": "string",
                              "enum": ["email", "linkedin", "sms"]}},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "sequence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer"},
                            "channel": {"type": "string"},
                            "delay_days": {"type": "integer"},
                            "subject": {"type": "string"},
                            "body": {"type": "string"},
                        },
                    },
                },
            },
        },
        estimated_latency_seconds=20,
        reputation_floor=0,
        free_quota=FREE_QUOTA_PER_CALLER,
    ),
    AgentSkill(
        id="healthcare-credential-check",
        name="Healthcare Provider Credential Check",
        description=(
            "Verify provider NPI, specialty, state licences, DEA registration, "
            "and payer credentialing status. Powered by Auditor agents."
        ),
        tags=["healthcare", "credentialing", "compliance", "rcm", "npi"],
        examples=[
            "Verify NPI 1234567890 is credentialed with Aetna in Texas.",
            "Check Dr Jane Smith's DEA and state licence status for Florida.",
        ],
        axm_price_wei=int(4.0 * 10**18),
        sinc_price=4,
        input_schema={
            "type": "object",
            "required": ["provider_npi"],
            "properties": {
                "provider_npi": {"type": "string", "description": "10-digit NPI"},
                "provider_name": {"type": "string"},
                "state": {"type": "string", "description": "2-letter US state code"},
                "payer_ids": {"type": "array", "items": {"type": "string"},
                              "description": "Payer IDs to check credentialing for"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "npi_valid": {"type": "boolean"},
                "specialty": {"type": "string"},
                "licence_status": {"type": "string"},
                "dea_active": {"type": "boolean"},
                "payer_credentialing": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "payer_id": {"type": "string"},
                            "status": {"type": "string"},
                        },
                    },
                },
            },
        },
        estimated_latency_seconds=45,
        reputation_floor=0,
        free_quota=0,
    ),
    AgentSkill(
        id="dental-billing-scrub",
        name="Dental Billing Scrub & Claims Cleanup",
        description=(
            "Scrub dental claims for code accuracy, CDT compliance, missing attachments, "
            "and prior-auth flags before submission. Powered by Auditor agents."
        ),
        tags=["dental", "billing", "rcm", "cdt", "claims", "compliance"],
        examples=[
            "Scrub this batch of 20 dental claims for CDT coding errors before submission.",
            "Flag any claims missing X-ray attachments for payer XYZ.",
        ],
        axm_price_wei=int(3.5 * 10**18),
        sinc_price=4,
        input_schema={
            "type": "object",
            "required": ["claims"],
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_id": {"type": "string"},
                            "cdt_codes": {"type": "array", "items": {"type": "string"}},
                            "tooth_number": {"type": "string"},
                            "provider_npi": {"type": "string"},
                            "payer_id": {"type": "string"},
                            "attachments": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "clean_claims": {"type": "array", "items": {"type": "object"}},
                "flagged_claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim_id": {"type": "string"},
                            "issues": {"type": "array", "items": {"type": "string"}},
                            "suggested_fix": {"type": "string"},
                        },
                    },
                },
                "summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=40,
        reputation_floor=0,
        free_quota=0,
    ),
    AgentSkill(
        id="compliance-sbom",
        name="Compliance SBOM & Regulatory Filing",
        description=(
            "Generate a Software Bill of Materials (SBOM), run licence compliance checks, "
            "and produce regulatory filing artefacts. Powered by Auditor agents."
        ),
        tags=["compliance", "sbom", "regulatory", "licence", "audit"],
        examples=[
            "Generate an SPDX SBOM for the SINCOR2 repo and flag GPL-incompatible licences.",
            "Produce a regulatory artefact package for FDA 510(k) SaMD submission.",
        ],
        axm_price_wei=int(5.0 * 10**18),
        sinc_price=5,
        input_schema={
            "type": "object",
            "required": ["repository"],
            "properties": {
                "repository": {"type": "string", "description": "GitHub owner/repo"},
                "artifacts": {"type": "array", "items": {"type": "string"},
                              "description": "Dependency manifests to scan"},
                "format": {"type": "string", "enum": ["spdx", "cyclonedx"],
                           "description": "SBOM output format"},
                "regulation": {"type": "string",
                               "description": "Applicable regulation (e.g. FDA-SaMD, HIPAA, SOC2)"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "sbom": {"type": "object"},
                "licence_issues": {"type": "array", "items": {"type": "object"}},
                "compliance_score": {"type": "number"},
                "filing_artefact": {"type": "string"},
            },
        },
        estimated_latency_seconds=60,
        reputation_floor=0,
        free_quota=0,
    ),
    AgentSkill(
        id="market-forecast",
        name="Market Forecast & Scenario Planning",
        description=(
            "Forward-looking market forecasts with confidence intervals, Monte Carlo "
            "simulations, and 'what-if' scenario modelling. Powered by Synthesizer agents."
        ),
        tags=["analytics", "forecasting", "data-science", "monte-carlo"],
        examples=[
            "Model ARR growth under three expansion scenarios for Q3 2026.",
            "Forecast SINC token price trajectory given current bonding curve and volume.",
        ],
        axm_price_wei=int(4.0 * 10**18),
        sinc_price=4,
        input_schema={
            "type": "object",
            "required": ["subject"],
            "properties": {
                "subject": {"type": "string", "description": "What to forecast"},
                "horizon": {"type": "string", "description": "Time horizon (e.g. 30d, 1y)"},
                "scenarios": {"type": "array", "items": {"type": "string"},
                              "description": "Named scenarios to model"},
                "data": {"type": "object", "description": "Historical data points (optional)"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "forecast": {"type": "object"},
                "scenarios": {"type": "array", "items": {"type": "object"}},
                "confidence_interval": {"type": "object"},
                "summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=35,
        reputation_floor=0,
        free_quota=FREE_QUOTA_PER_CALLER,
    ),
    AgentSkill(
        id="deal-scoring",
        name="Deal Scoring & Pipeline Prioritisation",
        description=(
            "Score open deals by ICP fit, intent signals, and close probability. "
            "Prioritise pipeline and surface next-best actions. Powered by Director agents."
        ),
        tags=["sales", "crm", "pipeline", "scoring", "deal"],
        examples=[
            "Score my 15 open enterprise deals and rank by close probability.",
            "Which deals should I focus on to hit Q3 quota?",
        ],
        axm_price_wei=int(2.5 * 10**18),
        sinc_price=3,
        input_schema={
            "type": "object",
            "required": ["deals"],
            "properties": {
                "deals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "deal_id": {"type": "string"},
                            "company": {"type": "string"},
                            "value": {"type": "number"},
                            "stage": {"type": "string"},
                            "last_activity": {"type": "string"},
                        },
                    },
                },
                "target_quota": {"type": "number"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "scored_deals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "deal_id": {"type": "string"},
                            "score": {"type": "number"},
                            "close_probability": {"type": "number"},
                            "next_action": {"type": "string"},
                            "priority_rank": {"type": "integer"},
                        },
                    },
                },
                "summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=20,
        reputation_floor=0,
        free_quota=0,
    ),
    AgentSkill(
        id="content-blog",
        name="Content & Blog Post Generation",
        description=(
            "High-quality blog posts, technical articles, thought-leadership pieces, "
            "and social copy. Powered by Builder + Synthesizer agents."
        ),
        tags=["content", "writing", "marketing", "blog", "documentation"],
        examples=[
            "Write a 1500-word blog post on tokenised AI compute markets.",
            "Create a LinkedIn article on how SMB dental practices can automate billing.",
        ],
        axm_price_wei=int(2.0 * 10**18),
        sinc_price=2,
        input_schema={
            "type": "object",
            "required": ["topic"],
            "properties": {
                "topic": {"type": "string"},
                "audience": {"type": "string"},
                "tone": {"type": "string", "enum": ["professional", "casual", "technical"]},
                "length_words": {"type": "integer", "minimum": 200, "maximum": 5000},
                "seo_keywords": {"type": "array", "items": {"type": "string"}},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "meta_description": {"type": "string"},
                "word_count": {"type": "integer"},
            },
        },
        estimated_latency_seconds=25,
        reputation_floor=0,
        free_quota=FREE_QUOTA_PER_CALLER,
    ),
    AgentSkill(
        id="cashflow-recovery",
        name="Cash-Flow Recovery & Invoice Management",
        description=(
            "Identify overdue invoices, draft recovery sequences, and escalate "
            "collections automatically. Settles in AXM/SINC. Powered by Negotiator agents."
        ),
        tags=["finance", "collections", "ar", "invoice", "cashflow"],
        examples=[
            "Identify all overdue invoices >30 days and generate recovery email sequences.",
            "Calculate total recoverable AR for a dental practice and prioritise by balance.",
        ],
        axm_price_wei=int(3.5 * 10**18),
        sinc_price=4,
        input_schema={
            "type": "object",
            "required": ["invoices"],
            "properties": {
                "invoices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "invoice_id": {"type": "string"},
                            "amount": {"type": "number"},
                            "due_date": {"type": "string", "format": "date"},
                            "debtor_name": {"type": "string"},
                            "debtor_email": {"type": "string"},
                        },
                    },
                },
                "escalation_threshold_days": {"type": "integer", "default": 30},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "overdue_invoices": {"type": "array", "items": {"type": "object"}},
                "recovery_sequences": {"type": "array", "items": {"type": "object"}},
                "total_recoverable": {"type": "number"},
                "summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=30,
        reputation_floor=0,
        free_quota=0,
    ),
    AgentSkill(
        id="local-business-site-builder",
        name="Local Business Site Builder",
        description=(
            "Discover local businesses via Yelp/Google Places, enrich their profile, "
            "and generate a ready-to-deploy single-page site scaffold. "
            "Flagship vertical for external agent resale. Powered by Scout + Builder agents."
        ),
        tags=["local-business", "website", "leadgen", "scout", "vertical"],
        examples=[
            "Find all dental practices in Austin TX without a modern website and build page scaffolds.",
            "Generate a landing-page template for Smith Plumbing in Chicago.",
        ],
        axm_price_wei=int(5.0 * 10**18),
        sinc_price=5,
        input_schema={
            "type": "object",
            "required": ["location"],
            "properties": {
                "location": {"type": "string", "description": "City, state or ZIP code"},
                "category": {"type": "string", "description": "Business type (e.g. dental, plumbing)"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50,
                          "description": "Max businesses to process"},
                "business_name": {"type": "string", "description": "For single-business mode"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "businesses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "address": {"type": "string"},
                            "phone": {"type": "string"},
                            "site_scaffold": {"type": "string",
                                              "description": "HTML/Markdown site template"},
                        },
                    },
                },
                "summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=60,
        reputation_floor=0,
        free_quota=0,
    ),
    AgentSkill(
        id="toa-decision",
        name="TOA Strategic Decision & Routing",
        description=(
            "Submit a multi-option decision to the Temporal Optimization Agent (TOA). "
            "TOA runs Monte Carlo simulations, scores options by utility, and returns "
            "a ranked action plan with confidence intervals. Powered by TOA + Director agents."
        ),
        tags=["toa", "strategy", "decision", "monte-carlo", "optimization"],
        examples=[
            "Should I expand to the UK market in Q4 or double down on US mid-market?",
            "Rank these three product roadmap options by projected 90-day revenue impact.",
        ],
        axm_price_wei=int(6.0 * 10**18),
        sinc_price=6,
        input_schema={
            "type": "object",
            "required": ["options"],
            "properties": {
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "description": {"type": "string"},
                            "estimated_cost": {"type": "number"},
                            "estimated_revenue_impact": {"type": "number"},
                        },
                    },
                },
                "context": {"type": "string",
                            "description": "Additional business context for the simulation"},
                "horizon_days": {"type": "integer", "default": 90},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "ranked_options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "utility_score": {"type": "number"},
                            "confidence": {"type": "number"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "recommended_action": {"type": "string"},
                "simulation_summary": {"type": "string"},
            },
        },
        estimated_latency_seconds=45,
        reputation_floor=0,
        free_quota=0,
    ),
    # ── Legacy / backward-compat skills kept under their original IDs ──────
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
        axm_price_wei=int(3.0 * 10**18),
        sinc_price=3,
        estimated_latency_seconds=30,
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
        axm_price_wei=int(2.0 * 10**18),
        sinc_price=2,
        estimated_latency_seconds=20,
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
        axm_price_wei=int(1.0 * 10**18),
        sinc_price=1,
        estimated_latency_seconds=10,
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
        axm_price_wei=0,  # payment-verification skill itself is free
        sinc_price=0,
        estimated_latency_seconds=5,
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
            "Negotiator, Director, Auditor, Caretaker). External agents pay in SINC "
            "or AXIOM (AXM) — the SINCOR ecosystem tokens on Base — and receive "
            "professional-grade intelligence, content, and automation in return. "
            "Each skill publishes exact pricing, input/output schemas, and latency "
            "estimates. The top 5 skills offer a free quota for new external callers. "
            "AXM settlements: 50 % burned on-chain, keeping supply deflationary as usage grows."
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
        # No API key required — quote and task submission are open to all A2A callers.
        # Payment is enforced on-chain via AXM/SINC transfer confirmation.
        security_schemes={},
        security_requirements=[],
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


# ---------------------------------------------------------------------------
# Skill Pricing Engine  (fill-rate tracking + 24 h auto-adjustment)
# ---------------------------------------------------------------------------

class _SkillPriceEngine:
    """
    Tracks quote and fill counts per skill and adjusts prices every 24 hours.

    Adjustment rule:
      - If fill_count >= PRICE_ADJUST_TARGET_FILLS  → increase price by PRICE_ADJUST_STEP
      - If fill_count == 0                          → decrease price by PRICE_ADJUST_STEP
      - Otherwise                                   → no change

    Prices are adjusted relative to each skill's current price and are bounded
    to [50% of initial, 500% of initial] to prevent runaway drift.

    Thread-safe. Prices are stored in memory; the adjustment background thread
    starts automatically on first instantiation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # skill_id → current axm_price_wei (mutable copy from SINCOR_SKILLS)
        self._prices: Dict[str, int] = {
            s.id: s.axm_price_wei for s in SINCOR_SKILLS
        }
        # initial prices kept for bounding
        self._initial_prices: Dict[str, int] = dict(self._prices)
        # fill counter: skill_id → count since last adjustment
        self._fills: Dict[str, int] = {s.id: 0 for s in SINCOR_SKILLS}
        # quote counter
        self._quotes: Dict[str, int] = {s.id: 0 for s in SINCOR_SKILLS}
        # Start background adjustment thread (daemon — does not block shutdown)
        self._thread = threading.Thread(
            target=self._adjustment_loop, daemon=True, name="a2a-price-adjuster"
        )
        self._thread.start()

    def get_price(self, skill_id: str) -> int:
        with self._lock:
            return self._prices.get(skill_id, AXM_PRICE_PER_TASK)

    def record_quote(self, skill_id: str) -> None:
        with self._lock:
            self._quotes[skill_id] = self._quotes.get(skill_id, 0) + 1

    def record_fill(self, skill_id: str) -> None:
        with self._lock:
            self._fills[skill_id] = self._fills.get(skill_id, 0) + 1

    def snapshot(self) -> Dict[str, Any]:
        """Return a public snapshot of current prices and fill counts."""
        with self._lock:
            return {
                sid: {
                    "axm_price_wei": self._prices[sid],
                    "fills_24h": self._fills[sid],
                    "quotes_24h": self._quotes[sid],
                }
                for sid in self._prices
            }

    def _adjustment_loop(self) -> None:
        """Run forever; sleep 24h between adjustments."""
        while True:
            time.sleep(24 * 3600)
            self._adjust()

    def _adjust(self) -> None:
        with self._lock:
            for skill_id, current_price in list(self._prices.items()):
                fills = self._fills.get(skill_id, 0)
                initial = self._initial_prices.get(skill_id, AXM_PRICE_PER_TASK) or 1
                if fills >= PRICE_ADJUST_TARGET_FILLS:
                    new_price = int(current_price * (1 + PRICE_ADJUST_STEP))
                elif fills == 0:
                    new_price = int(current_price * (1 - PRICE_ADJUST_STEP))
                else:
                    new_price = current_price
                # Bound to [50% initial, 500% initial]
                new_price = max(initial // 2, min(new_price, initial * 5))
                if new_price != current_price:
                    logger.info(
                        "Price adjustment skill=%s  old=%.4f AXM  new=%.4f AXM  fills_24h=%d",
                        skill_id, current_price / 10**18, new_price / 10**18, fills,
                    )
                self._prices[skill_id] = new_price
                # Reset counters
                self._fills[skill_id] = 0
                self._quotes[skill_id] = 0


# Module-level singleton
_price_engine = _SkillPriceEngine()


# ---------------------------------------------------------------------------
# Reputation Ledger  (SQLite-backed)
# ---------------------------------------------------------------------------

class ReputationLedger:
    """
    Persists and queries external A2A caller reputation based on successful
    AXM/SINC settlements.

    Schema:
        settlements(caller_id TEXT, skill_id TEXT, task_id TEXT,
                    axm_paid_wei INTEGER, ts INTEGER, PRIMARY KEY(task_id))

    The ledger is stored in the SINCOR data directory so it survives restarts.
    Thread-safe via a per-instance lock.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        if db_path is None:
            try:
                from sincor2.data_paths import data_dir
                db_path = str(data_dir() / "a2a_reputation.db")
            except Exception:
                db_path = os.path.join(
                    os.path.dirname(__file__), "..", "..", "..", "data", "a2a_reputation.db"
                )
        self._db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settlements (
                    caller_id  TEXT NOT NULL,
                    skill_id   TEXT NOT NULL,
                    task_id    TEXT NOT NULL,
                    axm_paid_wei INTEGER NOT NULL DEFAULT 0,
                    ts         INTEGER NOT NULL,
                    PRIMARY KEY (task_id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_caller ON settlements (caller_id)"
            )
            conn.commit()
            conn.close()

    def record(self, caller_id: str, skill_id: str, task_id: str,
               axm_paid_wei: int = 0) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR IGNORE INTO settlements (caller_id, skill_id, task_id, "
                "axm_paid_wei, ts) VALUES (?, ?, ?, ?, ?)",
                (caller_id, skill_id, task_id, axm_paid_wei, int(time.time())),
            )
            conn.commit()
            conn.close()

    def score(self, caller_id: str) -> int:
        """Return the total number of successful settlements for a caller."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM settlements WHERE caller_id = ?",
                (caller_id,),
            ).fetchone()
            conn.close()
            return int(row["cnt"]) if row else 0

    def leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return top callers ranked by total settlements and AXM volume."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT caller_id,
                       COUNT(*) AS total_settlements,
                       SUM(axm_paid_wei) AS total_axm_wei
                FROM settlements
                GROUP BY caller_id
                ORDER BY total_settlements DESC, total_axm_wei DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            conn.close()
            return [
                {
                    "caller_id":          r["caller_id"],
                    "total_settlements":  r["total_settlements"],
                    "total_axm_wei":      r["total_axm_wei"],
                    "total_axm_display":  f"{(r['total_axm_wei'] or 0) / 10**18:.4f} AXM",
                }
                for r in rows
            ]


# Module-level singleton
_reputation_ledger = ReputationLedger()


# ---------------------------------------------------------------------------
# Free-quota tracker  (in-memory; caller_id + skill_id → used count)
# ---------------------------------------------------------------------------

class _FreeQuotaTracker:
    """
    Tracks how many free calls each caller has used for each free-quota skill.
    Thread-safe; resets on restart (intentional — prevents abuse across deployments).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: Dict[Tuple[str, str], int] = {}

    def is_free(self, caller_id: str, skill: AgentSkill) -> bool:
        """Return True if this caller still has free quota for the given skill."""
        if skill.free_quota <= 0 or skill.id not in FREE_QUOTA_SKILLS:
            return False
        key = (caller_id, skill.id)
        with self._lock:
            used = self._usage.get(key, 0)
        return used < skill.free_quota

    def consume(self, caller_id: str, skill_id: str) -> None:
        """Increment free-quota usage for this caller + skill pair."""
        key = (caller_id, skill_id)
        with self._lock:
            self._usage[key] = self._usage.get(key, 0) + 1

    def remaining(self, caller_id: str, skill: AgentSkill) -> int:
        key = (caller_id, skill.id)
        with self._lock:
            used = self._usage.get(key, 0)
        return max(0, skill.free_quota - used)


_free_quota_tracker = _FreeQuotaTracker()


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
        metadata={
            "skill_id": skill_id,
            "caller_id": caller_id,
            "simulation_mode": bool(
                axm_paid and tx_hash and str(tx_hash).startswith("0xSIMULATED")
            ),
        },
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
            logger.error("PaymentVerifier RPC error: %s", e xc)

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
                        "id":                        s.id,
                        "name":                      s.name,
                        "tags":                      s.tags,
                        "sinc_price":                s.sinc_price,
                        "sinc_price_per_task":       s.sinc_price,  # backward compat alias
                        "axm_price_wei":             str(_price_engine.get_price(s.id)),
                        "axm_price_display":         (
                            f"{_price_engine.get_price(s.id) / 10**18:.4f} AXM"
                        ),
                        "estimated_latency_seconds": s.estimated_latency_seconds,
                        "reputation_floor":          s.reputation_floor,
                        "free_quota":                s.free_quota,
                    }
                    for s in SINCOR_SKILLS
                ],
                "primary_token":   A2A_PRIMARY_TOKEN,
                "sinc_contract":   SINC_CONTRACT,
                "axiom_contract":  AXIOM_CONTRACT,
                "treasury":        TREASURY_WALLET,
                "chain_id":        CHAIN_ID,
            })

        # ── SINC/AXIOM payment quote (GET or POST, no auth required) ─────────
        @bp.route("/api/a2a/quote", methods=["GET", "POST"])
        def quote():
            from flask import jsonify, request
            if request.method == "GET":
                skill_id = request.args.get("skill_id", "")
                caller_id = request.args.get("caller_id", "anonymous")
            else:
                body = request.get_json(force=True, silent=True) or {}
                skill_id = body.get("skill_id", "")
                caller_id = body.get("caller_id", "anonymous")

            skill = next((s for s in SINCOR_SKILLS if s.id == skill_id), None)
            if not skill:
                return jsonify(_err(f"Unknown skill: {skill_id}", code=-32602)), 400

            current_axm_price = _price_engine.get_price(skill_id)
            _price_engine.record_quote(skill_id)
            free_remaining = _free_quota_tracker.remaining(caller_id, skill)
            is_free_call = free_remaining > 0

            logger.info(
                "A2A quote  skill=%s  caller=%s  axm=%.4f AXM  free_remaining=%d",
                skill_id, caller_id, current_axm_price / 10**18, free_remaining,
            )

            return jsonify({
                "skill_id":                 skill_id,
                "skill_name":               skill.name,
                # SINC (primary)
                "sinc_amount":              skill.sinc_price if not is_free_call else 0,
                "sinc_contract":            SINC_CONTRACT,
                # AXM (live price from pricing engine)
                "axm_price_wei":            str(current_axm_price) if not is_free_call else "0",
                "axm_price_display":        (
                    f"{current_axm_price / 10**18:.4f} AXM" if not is_free_call else "FREE"
                ),
                "axiom_contract":           AXIOM_CONTRACT,
                "primary_token":            A2A_PRIMARY_TOKEN,
                "pay_to":                   TREASURY_WALLET,
                "chain_id":                 CHAIN_ID,
                "estimated_latency_seconds": skill.estimated_latency_seconds,
                "reputation_floor":         skill.reputation_floor,
                "free_quota_remaining":     free_remaining,
                "is_free":                  is_free_call,
                "input_schema":             skill.input_schema,
                "output_schema":            skill.output_schema,
                "note": (
                    "FREE — include caller_id in your tasks/send request (no txHash needed)."
                    if is_free_call else
                    f"Pay {skill.sinc_price} SINC (or {current_axm_price / 10**18:.4f} AXM) "
                    f"to pay_to on Base (chain 8453), then include txHash in your tasks/send request."
                ),
            })

        # ── Proof-of-settlement endpoint ──────────────────────────────────────
        @bp.route("/api/a2a/settle", methods=["POST"])
        def settle():
            """
            Accept a completed task's payment tx hash and return a signed
            proof-of-settlement JSON that callers can share publicly.

            Required body fields: task_id, tx_hash
            Optional:            caller_id
            """
            from flask import jsonify, request
            body     = request.get_json(force=True, silent=True) or {}
            task_id  = body.get("task_id", "")
            tx_hash  = body.get("tx_hash", "")
            caller_id = body.get("caller_id", "anonymous")

            task = _get_task(task_id)
            if not task:
                return jsonify(_err(f"Task {task_id} not found", code=-32602)), 404

            if task.state not in TaskState.terminal_states():
                return jsonify(_err("Task is not yet complete", code=-32003)), 400

            # Build deterministic result hash
            result_content = task.output or task.error or ""
            result_hash = hashlib.sha256(
                (task_id + result_content).encode()
            ).hexdigest()

            proof = {
                "proof_of_settlement": {
                    "task_id":        task.id,
                    "skill_id":       task.skill_id,
                    "caller_id":      task.caller_id or caller_id,
                    "tx_hash":        tx_hash or task.tx_hash or "",
                    "axm_paid_wei":   str(task.axm_paid),
                    "axm_paid_display": f"{task.axm_paid / 10**18:.4f} AXM",
                    "burn_amount_wei":  str(task.axm_paid // 2),
                    "burn_to":        DEAD_ADDRESS,
                    "treasury_amount_wei": str(task.axm_paid - task.axm_paid // 2),
                    "treasury":       TREASURY_WALLET,
                    "result_hash":    result_hash,
                    "settled_at":     task.updated_at,
                    "chain_id":       CHAIN_ID,
                    "basescan_url":   (
                        f"https://basescan.org/tx/{tx_hash}" if tx_hash else ""
                    ),
                }
            }

            # Record in reputation ledger
            _reputation_ledger.record(
                caller_id=task.caller_id or caller_id,
                skill_id=task.skill_id,
                task_id=task.id,
                axm_paid_wei=task.axm_paid,
            )
            logger.info(
                "Proof of settlement issued  task=%s  caller=%s  axm=%.4f AXM  tx=%s",
                task.id, task.caller_id, task.axm_paid / 10**18, tx_hash,
            )
            return jsonify(proof)

        # ── Reputation leaderboard ────────────────────────────────────────────
        @bp.route("/api/a2a/leaderboard", methods=["GET"])
        def leaderboard():
            """Return top external A2A callers by volume and settlement count."""
            from flask import jsonify, request
            limit = min(int(request.args.get("limit", 10)), 100)
            return jsonify({
                "leaderboard": _reputation_ledger.leaderboard(limit=limit),
                "description": (
                    "Top external A2A agents ranked by successful SINC/AXM settlements. "
                    "High-volume callers receive priority routing and SINC staking boosts."
                ),
            })

        # ── Pricing engine snapshot ───────────────────────────────────────────
        @bp.route("/api/a2a/pricing", methods=["GET"])
        def pricing():
            """Return current live prices and 24h fill stats for all skills."""
            from flask import jsonify
            return jsonify({
                "pricing": _price_engine.snapshot(),
                "adjust_target_fills_per_24h": PRICE_ADJUST_TARGET_FILLS,
                "adjust_step_pct":             PRICE_ADJUST_STEP * 100,
                "primary_token":               A2A_PRIMARY_TOKEN,
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

    # --- Reputation score → priority flag --------------------------------
    caller_reputation = _reputation_ledger.score(caller_id)
    is_high_rep = caller_reputation >= REPUTATION_HIGH_THRESHOLD

    # --- Free-quota check ------------------------------------------------
    free_call = _free_quota_tracker.is_free(caller_id, skill)

    # --- Payment gate (skip for free calls, axiom-payment skill, and dev) --
    env = os.getenv("FLASK_ENV", "production").lower()
    skip_payment = env in _DEV_ENVS or free_call

    if not skip_payment and skill_id != "axiom-payment":
        skill_price = _price_engine.get_price(skill_id)
        if not tx_hash:
            return _err(
                "Payment required. Call /api/a2a/quote to get the AXM amount and "
                "treasury address, send the transfer on Base, then include txHash. "
                f"Current price: {skill_price / 10**18:.4f} AXM",
                code=-32000, rpc_id=rpc_id,
            )
        if not PaymentVerifier.is_verified(tx_hash, skill_price):
            return _err(
                f"Payment tx {tx_hash} could not be verified on Base. "
                "Ensure the transfer is confirmed (≥1 block).",
                code=-32001, rpc_id=rpc_id,
            )

    # Consume free quota before dispatch
    if free_call:
        _free_quota_tracker.consume(caller_id, skill_id)

    # --- Create task & dispatch ------------------------------------------
    task = _new_task(
        skill_id=skill_id,
        input_text=input_text,
        caller_id=caller_id,
        session_id=context_id,
        axm_paid=axm_paid,
        tx_hash=tx_hash,
    )
    # Annotate high-rep and free-call status in metadata
    task.metadata["high_rep_caller"] = is_high_rep
    task.metadata["free_call"] = free_call
    logger.info(
        "A2A task %s created  skill=%s caller=%s rep=%d high_rep=%s free=%s",
        task.id, skill_id, caller_id, caller_reputation, is_high_rep, free_call,
    )

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

        # Build proof of settlement
        result_content = output or ""
        result_hash = hashlib.sha256((task.id + result_content).encode()).hexdigest()
        proof = {
            "task_id":            task.id,
            "skill_id":           skill_id,
            "caller_id":          caller_id,
            "tx_hash":            tx_hash or "",
            "axm_paid_wei":       str(axm_paid),
            "burn_amount_wei":    str(axm_paid // 2),
            "burn_to":            DEAD_ADDRESS,
            "treasury_amount_wei": str(axm_paid - axm_paid // 2),
            "treasury":           TREASURY_WALLET,
            "result_hash":        result_hash,
            "settled_at":         _now(),
            "chain_id":           CHAIN_ID,
            "basescan_url":       (
                f"https://basescan.org/tx/{tx_hash}" if tx_hash else ""
            ),
            "free_call":          free_call,
        }
        _update_task(task, state=TaskState.COMPLETED, output=output,
                     metadata={**task.metadata, "proof_of_settlement": proof})

        # Record settlement and update pricing fill count
        if (axm_paid > 0 and tx_hash) or free_call:
            _record_a2a_settlement(task, axm_paid, tx_hash or "")
            _reputation_ledger.record(
                caller_id=caller_id,
                skill_id=skill_id,
                task_id=task.id,
                axm_paid_wei=axm_paid,
            )
            _price_engine.record_fill(skill_id)
            _fire_toa_feedback(task, success=True)

    task_dict = _task_to_rpc(task, history_length=history_length)
    # Surface proof of settlement at top-level for convenience
    if "proof_of_settlement" in task.metadata:
        task_dict["proof_of_settlement"] = task.metadata["proof_of_settlement"]
    if is_high_rep:
        task_dict["priority"] = True
    return _rpc_ok(task_dict, rpc_id=rpc_id)


def _record_a2a_settlement(task: "A2ATask", axm_paid: int, tx_hash: str) -> None:
    """Create a settlement record in the platform coordinator for a paid A2A task."""
    try:
        from decimal import Decimal

        from flask import current_app, has_request_context

        if not has_request_context():
            return

        platform_state = current_app.extensions.get("sincor_platform")
        if not platform_state:
            return

        settlement = platform_state.get("settlement")
        if settlement is None:
            return

        amount_display = Decimal(axm_paid) / Decimal(10 ** 18)
        # Use 15-minute expiry — enough time for on-chain confirmation + retries.
        settlement_expiry = int(os.getenv("A2A_SETTLEMENT_EXPIRY_MINUTES", "15"))
        quote = settlement.create_quote(
            task_reference=task.id,
            payer=task.caller_id,
            payee=TREASURY_WALLET,
            amount=amount_display,
            token_symbol="AXIOM",
            expires_in_minutes=settlement_expiry,
        )
        settlement.confirm_payment(
            quote_id=quote.quote_id,
            tx_hash=tx_hash,
            confirmed_amount=amount_display,
        )
        logger.info(
            "A2A settlement recorded task=%s axm=%.4f tx=%s",
            task.id,
            float(amount_display),
            tx_hash,
        )
    except Exception as e xc:
        logger.warning("Settlement record failed for task %s: %s", task.id, e xc)


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
        return _err("Task {task_id} not found".format(task_id=task_id), code=-32602, rpc_id=rpc_id)
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
# TOA feedback hook
# ---------------------------------------------------------------------------

def _fire_toa_feedback(task: "A2ATask", success: bool) -> None:
    """
    Send task outcome data to the TOA decision router so future bid pricing
    and skill routing can improve over time.

    Fires asynchronously in a daemon thread to avoid blocking the response.
    """
    def _send() -> None:
        try:
            from integration.polyclaw_toa_decision_router import get_router
            router = get_router()
            router.ingest_feedback({
                "task_id":    task.id,
                "skill_id":   task.skill_id,
                "caller_id":  task.caller_id,
                "axm_paid":   task.axm_paid,
                "success":    success,
                "ts":         task.updated_at,
            })
        except Exception as e xc:
            logger.debug("TOA feedback skipped (router unavailable): %s", e xc)

    threading.Thread(target=_send, daemon=True, name="toa-feedback").start()


# ---------------------------------------------------------------------------
# Swarm dispatch
# ---------------------------------------------------------------------------

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
        from flask import current_app, has_request_context

        from sincor2.vertical_dispatch import dispatch_vertical_task, dispatch_via_router

        platform_state = None
        if has_request_context():
            platform_state = current_app.extensions.get("sincor_platform")

        vertical_result = dispatch_vertical_task(
            task.skill_id,
            task.input_text,
            platform_state,
            task_id=task.id,
            caller_id=task.caller_id,
        )
        if vertical_result:
            return vertical_result

        routed_result = dispatch_via_router(task.id, task.skill_id, task.input_text, platform_state)
        if routed_result:
            return routed_result

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

    except Exception as e xc:
        logger.exception("Swarm dispatch error for task %s", task.id)
        return None, str(e xc)


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
