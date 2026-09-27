#!/usr/bin/env python3
"""MCP server for the SINCOR agent marketplace (Phase 3 — discovery).

Speaks JSON-RPC 2.0 over stdio, newline-delimited, following the MCP method
shapes (``initialize``, ``tools/list``, ``tools/call``). Dependency-light:
**stdlib only** — no ``mcp`` package, no new requirements.

The server is a thin, honest client of the platform's real A2A HTTP surface::

    manifest   GET /.well-known/sincor-marketplace.json   (advertises this server)
    list_tasks GET /v1/a2a/auctions                        (sealed auctions, onchain anchors)
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
    req = urllib.request.Request(
        url, data=payload, method=method.upper(),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
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


# ---------------------------------------------------------------------------
# Sealed-bid commitment helper (reuses the canonical implementation)
# ---------------------------------------------------------------------------

def _sealed_commitment(price_wei: int, salt: bytes,
                       agent_id: str) -> bytes:
    """Compute keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash)).

    Reuses ``sincor2.a2a_inbound_market.sealed_commitment`` — the one canonical
    implementation (per AUCTION_GROUND_TRUTH.md) — instead of re-implementing
    keccak here. If the import is unavailable (e.g. the MCP host cannot import
    the platform package), the caller must supply a precomputed ``commitment``
    hex; the bidder-kit documents the exact scheme.
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
        "description": (
            "List open marketplace auctions (read-only). Backed by "
            "GET /v1/a2a/auctions — sealed auctions with an onchain anchor. "
            "UPGRADE PATH: once the sibling task-list endpoint "
            "GET /v1/a2a/tasks merges, this tool switches to it for the full "
            "task inventory (open, sealed, and assigned tasks)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string",
                          "description": "Filter by skill tag, e.g. 'lead-enrichment'."},
                "state": {"type": "string",
                          "description": "Filter by auction state ('open', 'auction', …)."},
                "limit": {"type": "integer", "default": 50,
                          "description": "Max entries to return."},
            },
        },
    },
    {
        "name": "get_task",
        "description": (
            "Full detail for one auctioned task (read-only): deadlines, "
            "commitment scheme, price bounds, and contract addresses. "
            "Backed by GET /v1/a2a/tasks/<task_id>/bidder-kit — 404 for tasks "
            "without an onchain auction anchor."
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
                                    "description": "Required: skill tags, e.g. ['lead-enrichment']."},
                "rpc_callback": {"type": "string",
                                 "description": "Your agent's JSON-RPC URL."},
                "wallet": {"type": "string",
                           "description": "0x-prefixed 20-byte hex address."},
                "agent_card": {"type": "object",
                               "description": "Alternative: full A2A agent_card dict (name, description, version, skills[] required)."},
                "confirmation_token": {"type": "string"},
                "action_id": {"type": "string"},
            },
        },
    },
    {
        "name": "submit_bid",
        "description": (
            "WRITE — place a bid on a marketplace task. Requires explicit "
            "operator confirmation per call (see register_agent). Modes: "
            "'auto' (default; inspects the task — sealed auctions use "
            "commit/reveal, open tasks use the legacy plaintext bid), "
            "'legacy' (POST /v1/a2a/bids), 'commit' (POST /v1/a2a/bids/commit "
            "with a keccak256 commitment computed canonically here — the "
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

def _tool_list_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    resp = _get("/v1/a2a/auctions")
    if not resp["ok"]:
        return {"error": "auctions endpoint failed", "upstream": resp}
    auctions = (resp["data"] or {}).get("auctions", [])
    skill = (args.get("skill") or "").strip().lower()
    state = (args.get("state") or "").strip().lower()
    limit = int(args.get("limit") or 50)
    out = []
    for a in auctions:
        if skill and skill != str(a.get("skill") or "").lower():
            continue
        if state and str(a.get("state", "")).lower() != state:
            continue
        a = dict(a)
        a["bidder_kit"] = f"/v1/a2a/tasks/{a.get('task_id')}/bidder-kit"
        out.append(a)
        if len(out) >= max(limit, 1):
            break
    return {
        "auctions": out, "count": len(out),
        "source": "GET /v1/a2a/auctions",
        "note": ("Sealed auctions with onchain anchors only. When the sibling "
                 "GET /v1/a2a/tasks endpoint merges, this tool will read the "
                 "full task inventory from it."),
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
        # A sealed auction task exposes a bidder-kit; open tasks 404 there.
        kit = _get(f"/v1/a2a/tasks/{urllib.parse.quote(task_id, safe='')}/bidder-kit")
        mode = "commit" if kit["ok"] else "legacy"
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
