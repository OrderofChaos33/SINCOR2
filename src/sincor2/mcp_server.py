#!/usr/bin/env python3
"""MCP server for the SINCOR agent marketplace (Phase 3 — discovery).

Speaks JSON-RPC 2.0 over stdio, newline-delimited, following the MCP method
shapes (``initialize``, ``tools/list``, ``tools/call``). Dependency-light:
**stdlib only** — no ``mcp`` package, no new requirements.

The server is a thin, honest client of the platform's real A2A HTTP surface::

    manifest   GET /.well-known/sincor-marketplace.json   (advertises this server)
    list_tasks GET /v1/a2a/tasks                           (the real task board)
    get_task   GET /v1/a2a/tasks/<task_id>/bidder-kit
    get_agent_card GET /v1/a2a/cards
    get_quote  GET /api/a2a/quote?skill_id=...&caller_id=...
    register_agent POST /v1/a2a/register            (write — confirmation gated)
    submit_bid     POST /v1/a2a/bids | /bids/commit | /bids/reveal   (write — confirmation gated)

Configuration:
    SINCOR_MCP_BASE_URL   Platform base URL (default ``http://127.0.0.1:5000``).

Run:
    python -m sincor2.mcp_server

Write-tool confirmation model (default-deny, two-phase):
    1. First ``tools/call`` for ``register_agent``/``submit_bid`` WITHOUT a
       ``confirmation_token`` NEVER executes. It returns a ``confirmation_required``
       payload describing the *exact* HTTP request that would be sent
       (method, path, canonical body, payload hash) plus a one-shot
       ``confirmation_token`` and ``action_id`` (TTL 300 s).
    2. The operator (human or orchestrating agent) reviews that payload and
       re-invokes the SAME tool with IDENTICAL arguments plus
       ``confirmation_token`` and ``action_id``.
    3. The server re-derives the outbound request from the arguments and
       requires: token match (constant-time), payload-hash match, unexpired,
       and single-use. Any mismatch → JSON-RPC error, nothing executed.

Rate limits: read tools each cost one HTTP GET against the platform's
``read`` tier (120/min, 5000/hour per IP); write tools cost one POST against
the ``register``/``bid`` tiers. The server identifies itself with a stable
``User-Agent`` so platform ops can attribute its traffic, and performs no
polling loops — staying under the read tier by construction.

Canonical invariants: docs/architecture/AUCTION_GROUND_TRUTH.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from sincor2.mcp_schema_guard import validate_arguments

SERVER_NAME = "sincor-marketplace-mcp"
SERVER_VERSION = "1.0.0"
MCP_PROTOCOL_VERSION = "2025-06-18"
USER_AGENT = (
    "sincor-mcp-server/1.0 "
    "(marketplace discovery client; +/.well-known/sincor-marketplace.json)"
)
CONFIRM_TTL_S = 300
HTTP_TIMEOUT_S = 20


def _base_url() -> str:
    return os.environ.get("SINCOR_MCP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


# ---------------------------------------------------------------------------
# HTTP transport (stdlib urllib)
# ---------------------------------------------------------------------------

def _request(method: str, path: str,
             body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Perform one platform HTTP call. Returns {"ok", "status", "data"}.

    Factored as a module-level function so tests can monkeypatch it onto a
    Flask test client instead of a live socket.
    """
    url = _base_url() + path
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # W-77: sealed-bid commit/reveal require bidder auth.  The MCP server
    # is a first-party operator tool, so it presents the operator
    # heartbeat token (same credential as the liveness runner) instead
    # of per-agent EIP-191 signatures.
    _op_token = (os.environ.get("SINCOR_HEARTBEAT_TOKEN")
                 or os.environ.get("AGENT_HEARTBEAT_TOKEN") or "").strip()
    if _op_token:
        headers["X-Sincor-Heartbeat"] = _op_token
    req = urllib.request.Request(
        url, data=payload, method=method.upper(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", "replace")
    except Exception as exc:  # connection refused, DNS, timeout …
        return {"ok": False, "status": 0, "data": None,
                "transport_error": f"{type(exc).__name__}: {exc}"}
    try:
        data = json.loads(raw) if raw.strip() else None
    except ValueError:
        data = {"_raw": raw[:2000]}
    return {"ok": 200 <= status < 300, "status": status, "data": data}


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if params:
        path += "?" + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
    return _request("GET", path)


# ---------------------------------------------------------------------------
# Confirmation gate for write tools (default-deny, two-phase)
# ---------------------------------------------------------------------------

# action_id -> {"tool", "token", "payload_hash", "method", "path", "body", "expires_at"}
_PENDING: Dict[str, Dict[str, Any]] = {}


def _canonical_payload(method: str, path: str,
                       body: Optional[Dict[str, Any]]) -> bytes:
    return json.dumps(
        {"method": method.upper(), "path": path, "body": body},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _payload_hash(method: str, path: str,
                  body: Optional[Dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_payload(method, path, body)).hexdigest()


def _issue_confirmation(tool: str, method: str, path: str,
                        body: Dict[str, Any],
                        summary: str) -> Dict[str, Any]:
    # Prune expired entries: without this, unconfirmed confirmations
    # accumulate in _PENDING forever (slow memory exhaustion via repeated
    # unconfirmed write-tool calls). Pruning here keeps the store bounded
    # by live (unexpired, unconfirmed) entries only.
    now = time.time()
    for stale_id in [aid for aid, e in _PENDING.items()
                     if now > float(e.get("expires_at", 0))]:
        _PENDING.pop(stale_id, None)
    action_id = "mcpact_" + secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    phash = _payload_hash(method, path, body)
    _PENDING[action_id] = {
        "tool": tool, "token": token, "payload_hash": phash,
        "method": method, "path": path, "body": body,
        "expires_at": time.time() + CONFIRM_TTL_S,
    }
    return {
        "status": "confirmation_required",
        "action_id": action_id,
        "confirmation_token": token,
        "expires_in_s": CONFIRM_TTL_S,
        "http_request": {
            "method": method.upper(), "url": _base_url() + path, "body": body,
        },
        "payload_sha256": phash,
        "summary": summary,
        "how_to_confirm": (
            "Review the http_request above. To execute, re-invoke the SAME tool "
            "with IDENTICAL arguments plus 'confirmation_token' and 'action_id'. "
            "The token is single-use and expires in 300 s; any argument change "
            "invalidates it."
        ),
    }


def _confirm_and_execute(tool: str, action_id: str, token: str,
                         method: str, path: str,
                         body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a confirmation and run the write. Raises _Denied on any mismatch."""
    entry = _PENDING.get(action_id or "")
    if entry is None or entry.get("tool") != tool:
        raise _Denied("unknown or mismatched action_id — re-request confirmation")
    # Single-use: remove first so a replay can never double-execute.
    _PENDING.pop(action_id, None)
    if time.time() > float(entry["expires_at"]):
        raise _Denied("confirmation expired — re-request confirmation")
    if not secrets.compare_digest(str(token or ""), str(entry["token"])):
        raise _Denied("confirmation token mismatch")
    if _payload_hash(method, path, body) != entry["payload_hash"]:
        raise _Denied(
            "arguments changed since confirmation was issued — "
            "re-request confirmation with the new arguments"
        )
    return _request(method, path, body)


class _Denied(Exception):
    """A write was refused by the confirmation gate (default-deny)."""


class PaymentRequired(Exception):
    """A tool call needs x402 payment before it can run.

    Carries the challenge fields so the dispatcher can return them as a
    successful result (isError=True) instead of a JSON-RPC -32000 error —
    MCP clients swallow JSON-RPC errors before the model ever sees them
    (dev-watch item 80, ScriptMasterLabs 2026-10-02).
    """

    def __init__(self, price: str, pay_to: str,
                 accepts: List[str] | None = None) -> None:
        super().__init__(f"payment required: {price} to {pay_to}")
        self.price = price
        self.pay_to = pay_to
        self.accepts = list(accepts) if accepts else []


# ---------------------------------------------------------------------------
# x402 payment challenge (dev-watch item 80)
# ---------------------------------------------------------------------------
#
# MCP clients (ScriptMasterLabs, 2026-10-02) swallow JSON-RPC errors before
# the model sees them: returning JSON-RPC error -32000 "Payment Required"
# from tools/call means the payment challenge (accepts array, price, payTo)
# NEVER reaches the agent. So the payment-required path returns the
# challenge as a SUCCESSFUL result dict with isError:true — it passes
# through result channels that drop error objects.

DEFAULT_X402_PRICE = "0.01"
DEFAULT_X402_PAY_TO = ""
DEFAULT_X402_ACCEPTS: List[str] = ["eip3009-usdc-base"]

#: Tool names that require x402 payment before execution. Production wiring
#: registers the metered tools here (with pricing in PAID_TOOL_PRICING);
#: the gate raises PaymentRequired unless the caller attaches a valid
#: ``x402_access_token`` argument.
PAID_TOOLS: set = set()

#: tool name -> {"price": str, "pay_to": str, "accepts": [str]}
PAID_TOOL_PRICING: Dict[str, Dict[str, Any]] = {}


def register_paid_tool(name: str, *, price: str = DEFAULT_X402_PRICE,
                       pay_to: str = DEFAULT_X402_PAY_TO,
                       accepts: List[str] | None = None) -> None:
    """Mark a tool as payment-gated with its x402 challenge fields."""
    PAID_TOOLS.add(name)
    PAID_TOOL_PRICING[name] = {
        "price": price,
        "pay_to": pay_to,
        "accepts": list(accepts) if accepts else list(DEFAULT_X402_ACCEPTS),
    }


def payment_challenge_result(price: str, pay_to: str,
                             accepts: List[str] | None) -> Dict[str, Any]:
    """Build the payment challenge as a SUCCESSFUL MCP result.

    Returns a result dict with ``isError: True`` (via _mcp_content) —
    deliberately NOT a JSON-RPC error object — so the challenge (price,
    payTo, accepts array, retry instructions) survives MCP clients that
    drop JSON-RPC errors before the model sees them.
    """
    accepts_text = ", ".join(accepts) if accepts else "none specified"
    challenge = {
        "status": "payment_required",
        "price": price,
        "payTo": pay_to,
        "accepts": list(accepts) if accepts else [],
        "retry_instructions": (
            "This tool requires payment before it can run. Sign an EIP-3009 "
            f"transferWithAuthorization for {price} payable to {pay_to} "
            f"(accepts: {accepts_text}), then retry tools/call with the "
            "authorization attached as the 'x402_access_token' argument."
        ),
    }
    return _mcp_content(challenge, is_error=True)


def _require_x402_payment(name: str, arguments: Dict[str, Any]) -> None:
    """Raise PaymentRequired if a payment-gated tool lacks a token."""
    if name in PAID_TOOLS and not arguments.get("x402_access_token"):
        pricing = PAID_TOOL_PRICING.get(name) or {}
        raise PaymentRequired(
            price=str(pricing.get("price", DEFAULT_X402_PRICE)),
            pay_to=str(pricing.get("pay_to", DEFAULT_X402_PAY_TO)),
            accepts=list(pricing.get("accepts", DEFAULT_X402_ACCEPTS)),
        )


# ---------------------------------------------------------------------------
# Sealed-bid commitment helper (reuses the canonical implementation)
# ---------------------------------------------------------------------------

def _sealed_commitment(price_wei: int, salt: bytes,
                       agent_id: str) -> bytes:
    """Compute the offchain sealed-bid commitment for the platform API.

    Reuses ``sincor2.a2a_inbound_market.sealed_commitment`` — the one
    canonical OFFCHAIN implementation (per AUCTION_GROUND_TRUTH.md) —
    instead of re-implementing keccak here. The offchain scheme is
    ``keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash))``; it
    is NOT the onchain scheme (which additionally binds auctionId and
    chainId) and must never be submitted to
    ``CommitRevealAuction.commit()``. If the import is unavailable (e.g.
    the MCP host cannot import the platform package), the caller must
    supply a precomputed ``commitment`` hex; the bidder-kit documents the
    exact offchain scheme.
    """
    try:
        from sincor2.a2a_inbound_market import sealed_commitment
    except Exception as exc:
        raise RuntimeError(
            "sealed_commitment unavailable on this host (cannot import "
            "sincor2.a2a_inbound_market); pass an explicit 'commitment' hex "
            "computed per the bidder-kit scheme instead"
        ) from exc
    return sealed_commitment(price_wei, salt, agent_id)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_tasks",
        "annotations": {"title": "List tasks", "read_only_hint": True},
        "description": (
            "List marketplace tasks from the real task board (read-only). "
            "Backed by GET /v1/a2a/tasks — every task the board serves "
            "(open, sealed, assigned, …), not just the onchain-anchored "
            "subset the legacy /v1/a2a/auctions endpoint returns. "
            "Defaults to biddable states (open, auction); pass 'state' "
            "to widen or narrow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string",
                          "description": "Filter by skill tag, e.g. 'lead-enrichment'."},
                "state": {"type": "string",
                          "description": "Filter by task state ('open', 'auction', …). Defaults to 'open,auction'."},
                "limit": {"type": "integer", "default": 50,
                          "description": "Max entries to return."},
            },
        },
    },
    {
        "name": "get_task",
        "annotations": {"title": "Get task", "read_only_hint": True},
        "description": (
            "Full bidding kit for one task (read-only): deadlines, the "
            "board commitment scheme with a worked example, price "
            "bounds, endpoints, and — for onchain-anchored tasks — the "
            "contract addresses and onchain scheme. Backed by "
            "GET /v1/a2a/tasks/<task_id>/bidder-kit, which serves every "
            "board task."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
                "agent_id": {"type": "string",
                             "description": "Optional: include your precomputed agent_id_hash."},
            },
        },
    },
    {
        "name": "get_agent_card",
        "annotations": {"title": "Get agent card", "read_only_hint": True},
        "description": (
            "Read the marketplace agent-card registry (read-only): the platform "
            "card plus vertical pack cards. Every skill carries a quoteUrl. "
            "Backed by GET /v1/a2a/cards."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string",
                             "description": "Return only cards offering this skill id."},
                "name": {"type": "string",
                         "description": "Substring match on card name."},
            },
        },
    },
    {
        "name": "get_quote",
        "annotations": {"title": "Get quote", "read_only_hint": True},
        "description": (
            "Price one skill execution in AXM before bidding (read-only). "
            "Backed by GET /api/a2a/quote. Unauthenticated; free-quota callers "
            "may see FREE."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["skill_id"],
            "properties": {
                "skill_id": {"type": "string",
                             "description": "e.g. 'lead-enrichment'."},
                "caller_id": {"type": "string", "default": "mcp-client",
                              "description": "Caller identity for free-quota tracking."},
            },
        },
    },
    {
        "name": "register_agent",
        "annotations": {
            "title": "Register agent",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,   # re-register with identical args rewrites the same directory record
            "open_world_hint": False,  # closed world: the SINCOR platform directory only
        },
        "description": (
            "WRITE — register (or re-register) an agent in the marketplace "
            "directory. Requires explicit operator confirmation per call: the "
            "first call returns a confirmation_required payload and performs "
            "NO write; re-invoke with identical arguments plus "
            "confirmation_token and action_id to execute. Reputation is "
            "earned-only and never declared — any supplied reputation value is "
            "ignored by the platform; new agents start at 0.0 (probation). "
            "KYA gating applies: revoked agents are excluded from discovery."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string",
                             "description": "1-128 chars [A-Za-z0-9._:-]; derived from name if omitted."},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "version": {"type": "string"},
                "capability_tags": {"type": "array", "items": {"type": "string"},
                                    "description": "Skill tags, e.g. ['lead-enrichment'] (the platform requires tags, or an agent_card carrying skills)."},
                "rpc_callback": {"type": "string",
                                 "description": "Your agent's JSON-RPC URL."},
                "wallet": {"type": "string",
                           "description": "0x-prefixed 20-byte hex address."},
                "agent_url": {"type": "string",
                              "description": "Your agent's base URL; forwarded upstream as its callback URL when rpc_callback is omitted."},
                "chain_id": {"type": "integer",
                             "description": "Chain the agent operates on (Base mainnet = 8453); forwarded upstream and recorded as an integer on the registration."},
                "agent_card": {"type": "object",
                               "description": "Alternative: full A2A agent_card dict (name, description, version, skills[] required)."},
                "confirmation_token": {"type": "string"},
                "action_id": {"type": "string"},
            },
        },
    },
    {
        "name": "submit_bid",
        "annotations": {
            "title": "Submit bid",
            "read_only_hint": False,
            "destructive_hint": False,   # bids lock stake, never delete state
            "idempotent_hint": False,    # each call may commit a fresh stake-locking commitment
            "open_world_hint": False,    # closed world: the SINCOR marketplace only
        },
        "description": (
            "WRITE — place a bid on a marketplace task. Requires explicit "
            "operator confirmation per call (see register_agent). Modes: "
            "'auto' (default; inspects the task — sealed auctions use "
            "commit/reveal, open tasks use the legacy plaintext bid), "
            "'legacy' (POST /v1/a2a/bids), 'commit' (POST /v1/a2a/bids/commit "
            "with an offchain keccak256 commitment computed canonically here — the "
            "commit LOCKS stake at 50% of bounty per minStakeBps=5000), "
            "'reveal' (POST /v1/a2a/bids/reveal — needs the nonce/salt from "
            "your commit call). Commit returns the salt hex: keep it secret "
            "until reveal."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "agent_id", "bid_axm"],
            "properties": {
                "task_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "bid_axm": {"type": "number",
                            "description": "Bid price in AXM (must be positive)."},
                "mode": {"type": "string", "default": "auto",
                         "enum": ["auto", "legacy", "commit", "reveal"]},
                "estimated_seconds": {"type": "integer", "default": 300},
                "nonce": {"type": "string",
                          "description": "32-byte salt as 0x hex. Commit mode: generated if omitted and returned to you. Reveal mode: REQUIRED (your commit salt)."},
                "commitment": {"type": "string",
                               "description": "Commit mode only: precomputed commitment hex. Use only if this host cannot compute it canonically."},
                "confirmation_token": {"type": "string"},
                "action_id": {"type": "string"},
            },
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOLS}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _project_board_task(t: Dict[str, Any]) -> Dict[str, Any]:
    """Project one raw board task record onto the tool's entry shape.

    Keeps the fields the auctions-backed tool returned (task_id, skill,
    bounty_axm, state, *_deadline_ms, bidder_kit) and adds the board
    fields a bidder needs to triage (sealed, requires_merit, tags);
    internal record fields (seed keys, pool allocation ids, poster
    internals) are not forwarded.
    """
    entry = {
        "task_id": t.get("task_id"),
        "skill": t.get("skill"),
        "tags": t.get("tags"),
        "bounty_axm": t.get("bounty_axm"),
        "state": t.get("state"),
        "sealed": bool(t.get("sealed")),
        "requires_merit": bool(t.get("requires_merit")),
        "commit_deadline_ms": t.get("commit_deadline"),
        "reveal_deadline_ms": t.get("reveal_deadline"),
        "created_at": t.get("created_at"),
        "bidder_kit": f"/v1/a2a/tasks/{t.get('task_id')}/bidder-kit",
    }
    if t.get("auction_id"):
        entry["auction_id"] = t.get("auction_id")
    return entry


def _tool_list_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    # Read the REAL task board — the same source the HTTP board serves.
    # The legacy /v1/a2a/auctions endpoint only lists sealed tasks with
    # an onchain anchor, which in production is an empty set: this tool
    # used to report an empty marketplace while the board held
    # thousands of tasks (outsider sweep K2, 2026-11-20).
    skill = (args.get("skill") or "").strip().lower()
    state = (args.get("state") or "").strip().lower()
    limit = max(int(args.get("limit") or 50), 1)
    states = state or "open,auction"
    out: List[Dict[str, Any]] = []
    page = 1
    while len(out) < limit and page <= 20:  # bounded: <= 2000 board rows
        resp = _get("/v1/a2a/tasks",
                    {"state": states, "page": page, "per_page": 100})
        if not resp["ok"]:
            return {"error": "tasks endpoint failed", "upstream": resp}
        data = resp["data"] or {}
        tasks = data.get("tasks", [])
        for t in tasks:
            if skill and skill != str(t.get("skill") or "").lower():
                continue
            out.append(_project_board_task(t))
            if len(out) >= limit:
                break
        if not tasks or page >= int(data.get("pages") or 1):
            break
        page += 1
    return {
        "tasks": out, "count": len(out),
        "source": "GET /v1/a2a/tasks",
        "note": ("The full task board (same source as the HTTP board). "
                 "Each entry links its bidder-kit with the exact "
                 "commitment scheme for that task."),
    }


def _tool_get_task(args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    params = {"agent_id": args["agent_id"]} if args.get("agent_id") else None
    resp = _get(f"/v1/a2a/tasks/{urllib.parse.quote(task_id, safe='')}/bidder-kit",
                params)
    if not resp["ok"]:
        return {"error": "task lookup failed", "task_id": task_id, "upstream": resp}
    return resp["data"]


def _tool_get_agent_card(args: Dict[str, Any]) -> Dict[str, Any]:
    resp = _get("/v1/a2a/cards")
    if not resp["ok"]:
        return {"error": "cards endpoint failed", "upstream": resp}
    cards = (resp["data"] or {}).get("cards", [])
    skill_id = (args.get("skill_id") or "").strip()
    name = (args.get("name") or "").strip().lower()
    matched = []
    for card in cards:
        skills = card.get("skills") or []
        if skill_id and not any(str(s.get("id")) == skill_id for s in skills):
            continue
        if name and name not in str(card.get("name", "")).lower():
            continue
        matched.append({
            "name": card.get("name"),
            "version": card.get("version"),
            "description": (card.get("description") or "")[:500],
            "skills": [
                {"id": s.get("id"), "name": s.get("name"),
                 "quoteUrl": s.get("quoteUrl")}
                for s in skills
            ],
        })
    return {"cards": matched, "count": len(matched),
            "source": "GET /v1/a2a/cards"}


def _tool_get_quote(args: Dict[str, Any]) -> Dict[str, Any]:
    skill_id = str(args.get("skill_id") or "").strip()
    if not skill_id:
        raise ValueError("skill_id is required")
    resp = _get("/api/a2a/quote",
                {"skill_id": skill_id,
                 "caller_id": args.get("caller_id") or "mcp-client"})
    if not resp["ok"]:
        return {"error": "quote failed", "skill_id": skill_id, "upstream": resp}
    return resp["data"]


def _tool_register_agent(args: Dict[str, Any],
                         confirmed: bool) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if args.get("agent_card") is not None:
        body["agent_card"] = args["agent_card"]
    for key in ("agent_id", "name", "description", "version",
                "capability_tags", "rpc_callback", "wallet",
                "agent_url", "chain_id"):
        if args.get(key) is not None:
            body[key] = args[key]
    summary = (
        f"POST /v1/a2a/register — register agent "
        f"'{body.get('agent_id') or body.get('name') or '?'}' with tags "
        f"{body.get('capability_tags') or (body.get('agent_card') or {}).get('skills')}. "
        "Reputation starts at 0.0 (probation); any declared reputation is ignored."
    )
    if not confirmed:
        return _issue_confirmation("register_agent", "POST",
                                   "/v1/a2a/register", body, summary)
    return _confirm_and_execute("register_agent", args.get("action_id"),
                                args.get("confirmation_token"),
                                "POST", "/v1/a2a/register", body)


def _plan_bid(args: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any], str, Dict[str, Any]]:
    """Derive (mode, method, path, body, extra_result) for a bid.

    Pure function of the arguments — the confirmation gate hashes its output,
    so planning must be deterministic for a fixed argument set.
    """
    task_id = str(args.get("task_id") or "").strip()
    agent_id = str(args.get("agent_id") or "").strip()
    bid_axm = float(args.get("bid_axm") or 0)
    if not task_id:
        raise ValueError("task_id is required")
    if not agent_id:
        raise ValueError("agent_id is required")
    if bid_axm <= 0:
        raise ValueError("bid_axm must be positive")
    est = int(args.get("estimated_seconds") or 300)
    mode = str(args.get("mode") or "auto").strip().lower()
    extra: Dict[str, Any] = {}

    if mode == "auto":
        # The bidder-kit serves every board task and states whether the
        # task is sealed: sealed tasks use commit/reveal, open tasks use
        # the legacy plaintext bid. (Kit detection used to key off the
        # kit returning 404 for unanchored tasks — the kit no longer
        # 404s, so read the explicit 'sealed' flag instead.)
        kit = _get(f"/v1/a2a/tasks/{urllib.parse.quote(task_id, safe='')}/bidder-kit")
        kit_data = kit["data"] if kit["ok"] and isinstance(
            kit.get("data"), dict) else {}
        mode = "commit" if kit_data.get("sealed") else "legacy"
        extra["detected_mode"] = mode

    if mode == "legacy":
        body = {"task_id": task_id, "agent_id": agent_id,
                "bid_axm": bid_axm, "estimated_seconds": est}
        return mode, "POST", "/v1/a2a/bids", body, extra

    if mode == "commit":
        price_wei = int(round(bid_axm * 1e18))
        commitment = args.get("commitment")
        salt_hex: Optional[str] = None
        if commitment:
            commitment_hex = str(commitment)
        else:
            nonce = args.get("nonce")
            if nonce:
                raw = str(nonce).strip().lower()
                raw = raw[2:] if raw.startswith("0x") else raw
                salt = bytes.fromhex(raw)
                if len(salt) != 32:
                    raise ValueError("nonce must be 32 bytes as hex")
                extra["supplied_salt_hex"] = "0x" + salt.hex()
            else:
                # Generated salt MUST be echoed back as 'nonce' on the
                # confirmation call: _plan_bid must be a pure function of the
                # arguments or the confirmation payload-hash check would deny.
                salt = secrets.token_bytes(32)
                extra["generated_salt_hex"] = "0x" + salt.hex()
            salt_hex = "0x" + salt.hex()
            commitment_hex = "0x" + _sealed_commitment(price_wei, salt, agent_id).hex()
        body = {"task_id": task_id, "agent_id": agent_id,
                "commitment": commitment_hex}
        extra["stake_note"] = ("Commit LOCKS stake at 50% of the task bounty "
                               "(minStakeBps=5000) until reveal/close.")
        return mode, "POST", "/v1/a2a/bids/commit", body, extra

    if mode == "reveal":
        nonce = args.get("nonce")
        if not nonce:
            raise ValueError("reveal mode requires 'nonce' (your commit salt)")
        body = {"task_id": task_id, "agent_id": agent_id, "bid_axm": bid_axm,
                "nonce": nonce, "estimated_seconds": est}
        return mode, "POST", "/v1/a2a/bids/reveal", body, extra

    raise ValueError(f"unknown mode '{mode}' (auto|legacy|commit|reveal)")


def _tool_submit_bid(args: Dict[str, Any],
                     confirmed: bool) -> Dict[str, Any]:
    mode, method, path, body, extra = _plan_bid(args)
    summary = (
        f"{method} {path} — {mode} bid of {args.get('bid_axm')} AXM on "
        f"task '{args.get('task_id')}' as agent '{args.get('agent_id')}'. "
        + extra.get("stake_note", "")
    ).strip()
    if not confirmed:
        conf = _issue_confirmation("submit_bid", method, path, body, summary)
        if extra.get("detected_mode"):
            conf["detected_mode"] = extra["detected_mode"]
        if extra.get("generated_salt_hex"):
            # The operator must echo this back as 'nonce' when confirming,
            # otherwise the confirmation payload-hash check fails closed.
            conf["generated_salt_hex"] = extra["generated_salt_hex"]
            conf["how_to_confirm"] += (
                " For this commit, also pass 'nonce' = the generated_salt_hex "
                "above in your confirmation call (and keep it secret until reveal)."
            )
        return conf
    result = _confirm_and_execute("submit_bid", args.get("action_id"),
                                  args.get("confirmation_token"),
                                  method, path, body)
    # Surface commit-phase artifacts (salt) alongside the platform response.
    if extra:
        return {"mode": mode, "upstream": result,
                **{k: v for k, v in extra.items()
                   if k in ("supplied_salt_hex", "detected_mode")}}
    return result


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 dispatcher (stdio, newline-delimited)
# ---------------------------------------------------------------------------

def _rpc_ok(rpc_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(rpc_id: Any, code: int, message: str,
               data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


def _mcp_content(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text",
                     "text": json.dumps(payload, indent=2, default=str)}],
        "isError": is_error,
    }


def _handle_initialize(rpc_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _rpc_ok(rpc_id, {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    })


def _handle_tools_list(rpc_id: Any) -> Dict[str, Any]:
    return _rpc_ok(rpc_id, {"tools": TOOLS})


def _handle_tools_call(rpc_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _rpc_error(rpc_id, -32602, "arguments must be an object")
    if name not in _TOOL_NAMES:
        return _rpc_error(rpc_id, -32601, f"unknown tool '{name}'")
    try:
        # Layering (dev-watch 80 + 316): the x402 payment gate stays
        # OUTERMOST for unpaid calls — its challenge is itself the loud
        # response, and an unpaid call never executes, so nothing can be
        # coerced or mis-shaped through it. Every call that passes the
        # gate (non-paid tools, and paid tools carrying a token) then
        # goes through dev-watch 316 strict schema validation BEFORE
        # dispatch: a literal "null", a missing required field, or a
        # wrong-typed value is rejected (-32602 via the ValueError
        # handler below), never coerced by the tool implementations.
        _require_x402_payment(name, arguments)
        validate_arguments(
            name,
            next(t["inputSchema"] for t in TOOLS if t["name"] == name),
            arguments,
        )
        if name == "list_tasks":
            result = _tool_list_tasks(arguments)
        elif name == "get_task":
            result = _tool_get_task(arguments)
        elif name == "get_agent_card":
            result = _tool_get_agent_card(arguments)
        elif name == "get_quote":
            result = _tool_get_quote(arguments)
        elif name == "register_agent":
            confirmed = bool(arguments.get("confirmation_token"))
            result = _tool_register_agent(arguments, confirmed)
        elif name == "submit_bid":
            confirmed = bool(arguments.get("confirmation_token"))
            result = _tool_submit_bid(arguments, confirmed)
        else:  # pragma: no cover — guarded by _TOOL_NAMES
            return _rpc_error(rpc_id, -32601, f"unknown tool '{name}'")
    except PaymentRequired as exc:
        # Item 80: return the challenge as a SUCCESSFUL result with
        # isError:true — a JSON-RPC -32000 error would be swallowed by MCP
        # clients before the model ever sees it.
        return _rpc_ok(rpc_id, payment_challenge_result(
            exc.price, exc.pay_to, exc.accepts))
    except _Denied as exc:
        return _rpc_ok(rpc_id, _mcp_content(
            {"status": "denied", "error": str(exc)}, is_error=True))
    except ValueError as exc:
        return _rpc_error(rpc_id, -32602, str(exc))
    except RuntimeError as exc:
        return _rpc_error(rpc_id, -32603, str(exc))
    except Exception as exc:  # never leak a traceback-shaped crash to the client
        return _rpc_error(rpc_id, -32603, f"tool failed: {type(exc).__name__}")
    is_error = isinstance(result, dict) and (
        result.get("status") == "denied"
        or (isinstance(result.get("upstream"), dict)
            and not result["upstream"].get("ok", True)))
    return _rpc_ok(rpc_id, _mcp_content(result, is_error=is_error))


def handle_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch one JSON-RPC message; returns the response (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None,
                          -32700, "parse error: expected JSON-RPC 2.0 object")
    method = msg.get("method", "")
    rpc_id = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        return _handle_initialize(rpc_id, params)
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _rpc_ok(rpc_id, {})
    if method == "tools/list":
        return _handle_tools_list(rpc_id)
    if method == "tools/call":
        return _handle_tools_call(rpc_id, params)
    if rpc_id is None:
        return None  # unknown notification — ignore
    return _rpc_error(rpc_id, -32601, f"unknown method '{method}'")


def serve() -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin, stdout = sys.stdin, sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps(_rpc_error(None, -32700, "parse error"))
                         + "\n")
            stdout.flush()
            continue
        try:
            resp = handle_message(msg)
        except Exception as exc:  # absolute last resort
            resp = _rpc_error(msg.get("id") if isinstance(msg, dict) else None,
                              -32603, f"internal error: {type(exc).__name__}")
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
