"""Lane 3 — "Break the Zeros" site features (founder mission 2026-11-12).

Three surfaces, all built on existing platform paths — no parallel stores,
no new money-movement code, no on-chain transfers:

1. Proof wall (GET /x402-live, rendered by mvp_blueprints/pages.py):
   :func:`proof_wall_data` assembles only counts that are sourceable from
   repo/site state. The MoebiusX tallies are a build-time snapshot (the
   deployed site cannot read the trading kits) and the page is stamped
   "as of" the snapshot date; the skill count/list is read live from the
   agent card builder.

2. Zero-friction external agent registration (GET /register page +
   POST /ap2/register): validates name / endpoint URL / contact, then
   stores the record through the EXISTING registration path
   (``a2a_inbound_ext.register_agent_record`` — the same function behind
   POST /v1/a2a/register). The first ``INTRO_BOUNTY_SLOTS`` external
   registrations are flagged on the record for an introductory AXM
   micro-bounty, and the flag is noted in the EXISTING bounty pool ledger
   with a plain ``BountyPool.allocate()`` call (a ledger reservation only).
   The bounty becomes payable through the existing a2a bid/settle flow
   when the agent completes its first verified handshake; this module
   moves no funds itself.

3. x402 paid endpoints (POST /x402-live/quick-scan, POST
   /audit-express/order) built on the in-repo sincor-x402 package
   (``packages/sincor-x402``). Settlement is FACILITATOR-GATED: until a
   real facilitator is configured via ``SINCOR_X402_FACILITATOR_URL``,
   a call carrying a payment header re-serves the package's 402
   challenge with a ``facilitator_gated`` marker and the paid work never
   executes. Nothing here ever fakes a settled payment.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

# ── Proof wall snapshot ──────────────────────────────────────────────────────
# Sourced 2026-11-12 by running the MoebiusX kits' own status reports:
#   v3: `node founder-submit-v3.mjs --status` in ops/moebiusx/v3-kit →
#       69 trade steps SUCCESS, then 3 BUY attempts VALIDATION_FAILED
#       (session-policy revert, no funds moved), circuit breaker tripped.
#   v2: founder-progress-v2.json trade outcomes → 12 SUCCESS,
#       25 VALIDATION_FAILED (the session's 12-op budget was spent by the
#       12 successes; later attempts failed policy validation).
# The mission's "79/79" figure could NOT be sourced and is not used.
PROOF_WALL_AS_OF = "2026-11-12"
MOEBIUSX_V2 = {"session": "v2", "successful_trades": 12,
               "validation_failed_attempts": 25}
MOEBIUSX_V3 = {"session": "v3", "successful_trades": 69,
               "validation_failed_attempts": 3}

# D0xed Dev (@D0xedDevi0) publicly praised sincor-x402 unprompted on
# 2026-11-12 — the first outside public endorsement. The specific post
# URL was not sourceable at build time, so the wall links the profile.
ENDORSEMENT_URL = "https://x.com/D0xedDevi0"
ENDORSEMENT_HANDLE = "@D0xedDevi0"

MODULE_SUMMARIES = [
    {
        "name": "ERC-8004 Validation + Reputation Oracle",
        "ref": "PR #654 · contracts/erc8004/SincorValidationReputationOracle.sol",
        "status": "ERC-8004-style draft — not deployed, not audited",
        "summary": (
            "Validation and reputation registries driven by SIGIL "
            "settlements: every settled task can record an attestation "
            "(agent, taskId, sigilCommitment, outcome, timestamp); "
            "replaying the same (taskId, sigil) reverts. Reputation is "
            "deterministic: +10 per success, −10 per failure, bounded "
            "[−100, 1000]. Ships with an identity-registry stub."
        ),
    },
    {
        "name": "ERC-7579 SIGIL Policy Module",
        "ref": "PR #652 · contracts/modules/SigilPolicyModule.sol",
        "status": "Candidate hook module — not deployed, not installed "
                  "on any production account, not audited",
        "summary": (
            "An ERC-7579 hook module (type 4) that gates fund release on "
            "a valid SIGIL EIP-712 (SEAL-style) attestation binding "
            "(account, taskSpecHash, resultCommitment, payee, token, "
            "amountCap, expiry, nonce). Installed via "
            "onInstall(verifier, maxCap, unverifiedThreshold, "
            "settlementHub); the threshold measures the decoded amount, "
            "never msgValue."
        ),
    },
]


def proof_wall_data() -> Dict[str, Any]:
    """Data for the /x402-live proof wall. Skill data is read live from
    the agent card builder so the count can never drift from the card."""
    from sincor2.a2a_integration import build_agent_card

    card = build_agent_card()
    skills = [{"id": s.id, "name": s.name} for s in card.skills]
    return {
        "as_of": PROOF_WALL_AS_OF,
        "moebiusx_v2": MOEBIUSX_V2,
        "moebiusx_v3": MOEBIUSX_V3,
        "moebiusx_total_success": (
            MOEBIUSX_V2["successful_trades"] + MOEBIUSX_V3["successful_trades"]
        ),
        "skill_count": len(skills),
        "skills": skills,
        "modules": MODULE_SUMMARIES,
        "endorsement_url": ENDORSEMENT_URL,
        "endorsement_handle": ENDORSEMENT_HANDLE,
    }


# ── External agent registration + introductory bounty flag ──────────────────

INTRO_BOUNTY_SLOTS = 20
INTRO_BOUNTY_DEFAULT_AXM = 1.0
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _intro_bounty_amount() -> float:
    raw = (os.environ.get("SINCOR_INTRO_BOUNTY_AXM") or "").strip()
    try:
        amount = float(raw) if raw else INTRO_BOUNTY_DEFAULT_AXM
    except ValueError:
        amount = INTRO_BOUNTY_DEFAULT_AXM
    return amount if amount > 0 else INTRO_BOUNTY_DEFAULT_AXM


def _external_ordinal(agents: Dict[str, Any], agent_id: str) -> Optional[int]:
    """1-based position of ``agent_id`` among origin=="external" records,
    ordered by (registered_at, agent_id). None if not present/external."""
    externals = [
        r for r in agents.values()
        if isinstance(r, dict) and r.get("origin") == "external"
    ]
    externals.sort(key=lambda r: (int(r.get("registered_at") or 0),
                                  str(r.get("agent_id") or "")))
    for idx, rec in enumerate(externals, start=1):
        if rec.get("agent_id") == agent_id:
            return idx
    return None


def apply_intro_bounty_flag(agent_id: str) -> Dict[str, Any]:
    """Flag ``agent_id`` for the introductory micro-bounty if it is among
    the first ``INTRO_BOUNTY_SLOTS`` external registrations, and note the
    flag in the bounty pool ledger via the pool's existing allocate()
    path. Idempotent: an already-flagged record returns its flag.

    The allocation is a ledger reservation only — payout happens through
    the existing a2a settle flow on the agent's first verified handshake.
    If the pool cannot reserve (unfunded / degraded), the flag still
    lands, marked ``flagged_unfunded`` — an allocation is never faked.
    """
    from sincor2.a2a_inbound import _save_agents, get_fabric

    fabric = get_fabric()
    amount = _intro_bounty_amount()
    with fabric.lock:
        record = fabric.agents.get(agent_id)
        if record is None:
            return {"eligible": False, "reason": "record_not_found"}
        existing = record.get("intro_bounty")
        if isinstance(existing, dict) and existing.get("eligible"):
            return dict(existing)
        ordinal = _external_ordinal(fabric.agents, agent_id)
        if ordinal is None:
            return {"eligible": False, "reason": "not_external"}
        if ordinal > INTRO_BOUNTY_SLOTS:
            return {"eligible": False, "ordinal": ordinal,
                    "slots": INTRO_BOUNTY_SLOTS}
        flag: Dict[str, Any] = {
            "eligible": True,
            "ordinal": ordinal,
            "slots": INTRO_BOUNTY_SLOTS,
            "amount_axm": amount,
            "status": "flagged",
            "payable_on": "first_verified_handshake",
            "allocation_id": None,
        }
        record["intro_bounty"] = flag
    _save_agents(fabric)

    # Ledger note through the pool's existing code path. A custom journal
    # action would make verify_ledger() inconclusive, so this is a plain
    # allocate() against a per-agent task id.
    try:
        from sincor2.a2a_bounty_pool import bounty_pool

        alloc = bounty_pool().allocate(
            task_id=f"intro-bounty:{agent_id}",
            amount_axm=amount,
            reason=(
                "Lane-3 introductory micro-bounty for external agent "
                f"{agent_id} (registration #{ordinal} of "
                f"{INTRO_BOUNTY_SLOTS}); payable on its first verified "
                "handshake via the existing a2a settle flow."
            ),
        )
        flag["allocation_id"] = alloc.get("allocation_id") or alloc.get("id")
        flag["status"] = "reserved"
    except Exception as exc:  # pool unfunded/degraded — flag stands alone
        flag["status"] = "flagged_unfunded"
        flag["note"] = f"{type(exc).__name__}: {exc}"

    with fabric.lock:
        live = fabric.agents.get(agent_id)
        if live is not None:
            live["intro_bounty"] = flag
    _save_agents(fabric)
    return dict(flag)


def register_external_agent(body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Zero-friction external registration for POST /ap2/register.

    Returns (http_status, payload). Validation failures are 400s; the
    storage itself is the platform's existing registration path.
    """
    from sincor2.a2a_inbound import RegistrationAuthError
    from sincor2.a2a_inbound_ext import register_agent_record

    body = body if isinstance(body, dict) else {}
    name = str(body.get("name") or "").strip()
    endpoint = str(
        body.get("endpoint") or body.get("endpoint_url")
        or body.get("agent_url") or body.get("url") or ""
    ).strip()
    contact = str(body.get("contact") or "").strip()

    if not name:
        return 400, {"ok": False, "error": "name is required"}
    if not endpoint:
        return 400, {"ok": False, "error": "endpoint is required"}
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return 400, {"ok": False,
                     "error": "endpoint must be a valid http(s) URL"}
    if not contact:
        return 400, {"ok": False, "error": "contact is required"}

    record_body = {
        "name": name,
        "rpc_callback": endpoint,
        "capability_tags": ["ap2", "external-onboarding"],
        "description": (
            "External agent registered via POST /ap2/register. "
            f"Contact: {contact}"
        ),
    }
    try:
        record = register_agent_record(record_body)
    except (PermissionError, RegistrationAuthError) as exc:
        return 403, {"ok": False, "error": str(exc)}
    except OverflowError as exc:
        return 503, {"ok": False, "error": str(exc)}
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    flag = apply_intro_bounty_flag(record["agent_id"])
    return 201, {
        "ok": True,
        "agent": {
            "agent_id": record["agent_id"],
            "name": record.get("name"),
            "endpoint": record.get("rpc_callback"),
            "origin": record.get("origin"),
            "identity": record.get("identity"),
        },
        "intro_bounty": flag,
    }


# ── x402 paid endpoints (sincor-x402, in-repo package) ──────────────────────

TREASURY_WALLET = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
QUICK_SCAN_AMOUNT_ATOMIC = "10000"        # 0.01 USDC (6 decimals)
AUDIT_ORDER_AMOUNT_ATOMIC = "500000000"   # 500 USDC — Audit Express entry tier
BASE_CHAIN_ID = 8453
_FACILITATOR_ENV = "SINCOR_X402_FACILITATOR_URL"


def _load_sincor_x402():
    """Import the sincor-x402 package, preferring the in-repo source
    (packages/sincor-x402/src) so local, test, and production runs all
    execute the exact code that ships in this repo; fall back to an
    installed distribution when the vendored tree is absent."""
    vendored = (Path(__file__).resolve().parents[2]
                / "packages" / "sincor-x402" / "src")
    if vendored.is_dir() and str(vendored) not in sys.path:
        sys.path.insert(0, str(vendored))
    import sincor_x402

    return sincor_x402


class _HttpFacilitator:
    """Minimal facilitator client for a configured x402 facilitator.

    Implements the package's FacilitatorClient protocol against the
    standard facilitator ``/verify`` endpoint. Fails closed: any
    transport error, malformed response, or missing payment reference
    is a verification failure, never a success.
    """

    def __init__(self, base_url: str) -> None:
        self._verify_url = base_url.rstrip("/") + "/verify"

    def verify_and_hold(self, *, payment_payload, requirement):
        import sincor_x402

        try:
            payload = json.dumps({
                "paymentPayload": payment_payload,
                "paymentRequirements": requirement.to_accepts_dict(),
            }).encode("utf-8")
            req = urllib.request.Request(
                self._verify_url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return sincor_x402.FacilitatorResult(
                False, error=f"facilitator_unreachable: {exc}")
        ok = bool(data.get("isValid", data.get("ok")))
        ref = data.get("payment_ref") or data.get("paymentRef") or ""
        if ok and not str(ref).strip():
            return sincor_x402.FacilitatorResult(
                False, error="facilitator returned success without a "
                             "payment reference")
        return sincor_x402.FacilitatorResult(
            ok,
            payer=str(data.get("payer") or ""),
            payment_ref=str(ref),
            error=str(data.get("invalidReason") or data.get("error") or ""),
        )


def _resolve_facilitator(sx) -> Tuple[Any, bool]:
    """(facilitator, configured). Unconfigured → the package's local
    reference facilitator is constructed only so challenges can be
    built; the views never hand it a payment (see _paid_endpoint_call).
    """
    url = (os.environ.get(_FACILITATOR_ENV) or "").strip()
    if url:
        return _HttpFacilitator(url), True
    return sx.LocalFacilitator(), False


def _base_rpc(method: str, params: list) -> Any:
    """One JSON-RPC call against public Base state. Transport seam —
    tests substitute this function; production uses BASE_RPC_URL or the
    public Base endpoint."""
    url = (os.environ.get("BASE_RPC_URL") or "").strip() \
        or "https://mainnet.base.org"
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(f"base rpc {method}: {data['error']}")
    return data.get("result")


def _run_quick_scan(**kwargs) -> Dict[str, Any]:
    """The paid work behind POST /x402-live/quick-scan: a small surface
    summary of one Base address from public chain state."""
    sx = _load_sincor_x402()
    address = str(kwargs.get("address") or "").strip()
    if not _ADDRESS_RE.match(address):
        return {
            "address": address,
            "chain_id": BASE_CHAIN_ID,
            "network": "base",
            "is_contract": False,
            "error": "address must be a 0x-prefixed 20-byte hex address",
        }
    code = _base_rpc("eth_getCode", [address, "latest"])
    balance_wei = int(_base_rpc("eth_getBalance", [address, "latest"]), 16)
    block = int(_base_rpc("eth_blockNumber", []), 16)
    code_bytes = (len(code) - 2) // 2 if code and code.startswith("0x") else 0
    return {
        "address": address,
        "chain_id": BASE_CHAIN_ID,
        "network": "base",
        "is_contract": code not in (None, "0x", "0x0"),
        "code_size_bytes": code_bytes,
        "balance_wei": str(balance_wei),
        "balance_eth": balance_wei / 1e18,
        "is_usdc_contract": address.lower() == sx.USDC_BASE_ADDRESS.lower(),
        "block_number": block,
        "source": "public Base JSON-RPC state",
    }


def _run_audit_order(**kwargs) -> Dict[str, Any]:
    """The paid work behind POST /audit-express/order: records the
    intake for an Audit Express security check. No fulfillment system
    is implied — the intake reference is what the buyer receives, and
    scope/price confirmation follows through the contact given."""
    import uuid

    contact = str(kwargs.get("contact") or "").strip()
    repo = str(kwargs.get("repo") or kwargs.get("repo_url") or "").strip()
    return {
        "order_ref": f"audit-express-{uuid.uuid4().hex[:12]}",
        "status": "intake_received",
        "tier": "audit-express",
        "contact": contact,
        "repo": repo,
        "note": (
            "Intake received. Final scope and price (500–1500 USDC) are "
            "confirmed before work starts; the 48-hour clock starts at "
            "scope confirmation. This is a security check, not a formal "
            "audit."
        ),
    }


def _build_endpoint(sx, *, resource: str, amount_atomic: str,
                    description: str, work, spec):
    facilitator, configured = _resolve_facilitator(sx)
    requirement = sx.PaymentRequirement(
        scheme="exact",
        network="base",
        max_amount_required=amount_atomic,
        asset=sx.USDC_BASE_ADDRESS,
        pay_to=TREASURY_WALLET,
        max_timeout_seconds=120,
        resource=resource,
        description=description,
    )
    endpoint = sx.VerifiedEndpoint(
        work, requirement=requirement, spec=spec, facilitator=facilitator)
    return endpoint, configured


# Endpoints are cached per (resource, facilitator configuration) so the
# package's payment-replay protection (W-146: one authorization fulfills
# exactly one call) holds across requests within the process, matching
# the platform's other process-local stores. A facilitator-config change
# builds a fresh endpoint.
_ENDPOINT_CACHE: Dict[Tuple[str, str], Tuple[Any, bool]] = {}


def _get_endpoint(kind: str):
    sx = _load_sincor_x402()
    fac_key = (os.environ.get(_FACILITATOR_ENV) or "").strip()
    key = (kind, fac_key)
    cached = _ENDPOINT_CACHE.get(key)
    if cached is not None:
        return cached
    if kind == "quick-scan":
        built = _build_endpoint(
            sx,
            resource="/x402-live/quick-scan",
            amount_atomic=QUICK_SCAN_AMOUNT_ATOMIC,
            description="SINCOR quick scan: Base address surface summary "
                        "(contract check, code size, balance, chain "
                        "state) for 0.01 USDC. Body: {\"address\": "
                        "\"0x…\"}.",
            work=_run_quick_scan,
            spec={"required_keys": ["address", "chain_id", "is_contract"]},
        )
    elif kind == "audit-order":
        built = _build_endpoint(
            sx,
            resource="/audit-express/order",
            amount_atomic=AUDIT_ORDER_AMOUNT_ATOMIC,
            description="Audit Express entry reservation: 48-hour "
                        "AI-assisted Foundry fuzzing & security check. "
                        "Final scope price (500–1500 USDC) confirmed at "
                        "intake. Not a formal audit.",
            work=_run_audit_order,
            spec={"required_keys": ["order_ref", "status"]},
        )
    else:  # pragma: no cover - internal guard
        raise ValueError(f"unknown paid endpoint kind: {kind}")
    _ENDPOINT_CACHE[key] = built
    return built


def _paid_endpoint_call(endpoint, configured: bool, headers, body):
    """Shared challenge/gating flow for both paid endpoints.

    * No payment header → the package's 402 challenge.
    * Payment header but no facilitator configured → the same 402
      challenge plus a facilitator_gated marker; the paid work NEVER
      executes, so no payment can be faked as settled.
    * Payment header + facilitator → the package's full verified flow.
    """
    has_payment = any(
        k.lower() in ("x-payment", "payment-signature") for k in headers)
    if not has_payment:
        resp = endpoint.challenge()
        return resp.status, resp.body
    if not configured:
        resp = endpoint.challenge()
        gated = dict(resp.body)
        gated["ok"] = False
        gated["error"] = "facilitator_not_configured"
        gated["settlement"] = "facilitator_gated"
        gated["note"] = (
            "Settlement is facilitator-gated: no x402 facilitator is "
            "configured on this deployment yet, so paid calls cannot "
            "settle. The challenge above is the live, correct offer."
        )
        return 402, gated
    resp = endpoint.handle(headers=dict(headers), kwargs=dict(body or {}))
    return resp.status, resp.body


def quick_scan(headers, body) -> Tuple[int, Dict[str, Any]]:
    """POST /x402-live/quick-scan — $0.01 Base address surface scan."""
    endpoint, configured = _get_endpoint("quick-scan")
    return _paid_endpoint_call(endpoint, configured, headers, body)


def audit_order(headers, body) -> Tuple[int, Dict[str, Any]]:
    """POST /audit-express/order — Audit Express entry tier (500 USDC)
    intake, same challenge pattern as the quick scan."""
    endpoint, configured = _get_endpoint("audit-order")
    return _paid_endpoint_call(endpoint, configured, headers, body)
