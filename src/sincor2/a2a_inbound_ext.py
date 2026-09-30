"""A2A inbound routes + agent operations. Imported by a2a_inbound.register."""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request

from sincor2.contract_net import BASE_CHAIN_ID, probe_base_chain
from sincor2.a2a_inbound import (
    HEARTBEAT_TTL_S,
    MAX_AGENTS,
    MERIT_THRESHOLD_AXM,
    _AGENT_ID_RE,
    _HEARTBEAT_STOP,
    _PLATFORM_AGENT_ID,
    _WALLET_RE,
    _http_error,
    _normalize_registration,
    _now_ms,
    _save_agents,
    _apply_reputation,
    get_fabric,
    health_snapshot,
)

from sincor2.a2a_identity import (
    register_message as _register_message,
    transfer_message as _transfer_message,
    verify_wallet_proof as _verify_wallet_proof,
)

logger = logging.getLogger("sincor.a2a.inbound")
_HEARTBEAT_THREAD = None


# ---------------------------------------------------------------------------
# First-registration squatting control (wave 32)
# ---------------------------------------------------------------------------
#
# SOURCE OF THE PROTECTED LIST: protocol-reserved names (the platform agent id,
# service/system names) plus obvious brand-impersonation names. The list is
# explicit and reviewable in this module — never a hidden blocklist. It lives
# in code (not a config file) so a change is a reviewable diff.
PROTECTED_AGENT_IDS = frozenset({
    # Protocol / platform reserved
    "sincor", "sincor-platform", "sincor-agent-swarm", "sincor-team",
    "sadas", "axiom",
    # Service / system impersonation
    "admin", "administrator", "system", "root", "official", "support",
    "help", "security", "moderator", "treasury", "platform", "genesis",
    "foundation", "team", "staff",
})

# Any agent_id that is exactly "sincor" or starts with the "sincor-" prefix
# (case-insensitive) is reserved for the protocol, in addition to the list.
_PROTECTED_PREFIXES = ("sincor",)


def _is_protected_agent_id(agent_id: str) -> bool:
    lowered = str(agent_id).strip().lower()
    if lowered in PROTECTED_AGENT_IDS:
        return True
    return any(lowered == p or lowered.startswith(p + "-") or lowered.startswith(p + "_")
               or lowered.startswith(p + ".") for p in _PROTECTED_PREFIXES)


def _registration_proof_required() -> bool:
    """Hard-reject mode: set SINCOR_REGISTRATION_PROOF_REQUIRED=1 in
    production to refuse unverified claims outright. Default (unset) marks
    unverified claims as untrusted instead, preserving existing clients."""
    return str(os.environ.get("SINCOR_REGISTRATION_PROOF_REQUIRED") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _extract_registration_proof(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "signature": body.get("registration_signature") or body.get("signature") or "",
        "wallet": body.get("registration_wallet") or body.get("wallet") or "",
        "ts": body.get("registration_ts") or body.get("reg_ts") or body.get("timestamp"),
    }


def _kya_listed(agent: Dict[str, Any]) -> None:
    try:
        from sincor2.kya_registry import hook_listed

        rec = hook_listed(agent)
        if rec:
            agent["kya_id"] = rec.get("kya_id")
            agent["kya_status"] = rec.get("status")
    except Exception as err:
        logger.warning("[KYA] list hook skipped: %s", err)


def _kya_flag_ghost(agent_id: str) -> None:
    """Tombstone the wallet+card behind a ghosted identity so a rebirth
    under a new agent_id is flagged. Never raises — shedding detection
    must not break auction close."""
    try:
        from sincor2.kya_registry import flag_ghost

        flag_ghost(agent_id)
    except Exception as err:
        logger.warning("[KYA] ghost flag skipped: %s", err)


def _kya_heartbeat(agent_id: str) -> None:
    try:
        from sincor2.kya_registry import hook_heartbeat

        hook_heartbeat(agent_id, ok=True)
    except Exception as err:
        logger.debug("[KYA] heartbeat hook skipped: %s", err)


def _live_kya_statuses() -> Dict[str, str]:
    """Fail-open KYA status lookup for directory listings.

    The directory must not 500 if KYA is unavailable: on any error we log
    and return {}, and list_agents() falls back to stored kya_status values.
    """
    try:
        from sincor2.kya_registry import live_statuses

        return live_statuses()
    except Exception as err:
        logger.warning("[KYA] directory status lookup skipped: %s", err)
        return {}


def register_agent_record(body: Dict[str, Any],
                          _internal_reputation: Optional[float] = None) -> Dict[str, Any]:
    """Register (or re-register) an agent record.

    Reputation is earned-only and never declared: ``body["reputation"]`` is
    silently ignored (existing clients may still send it; it is read-only
    in API responses). New agents start at 0.0 (probation); re-registration
    preserves already-earned reputation. The only path that assigns
    reputation at registration time is the internal platform seed, via
    ``_internal_reputation`` — the HTTP route never passes it.

    First-registration squatting control (wave 32):

    * Protected names (``PROTECTED_AGENT_IDS`` + the ``sincor`` prefix rule)
      are refused for new registrations.
    * A new claim may present a wallet-identity proof: an EIP-191 signature
      over ``SINCOR-REGISTER|<agent_id>|<timestamp_ms>`` with a mandatory
      wallet claim equal to the recovered signer (see ``a2a_identity``).
      Verified claims bind ``owner_wallet`` and set ``identity="verified"``.
    * Unverified claims are accepted but marked ``identity="unverified"``
      (grace for existing clients); set
      ``SINCOR_REGISTRATION_PROOF_REQUIRED=1`` to refuse them outright.
    * Re-registration of an owned record requires a fresh signature from
      the owner wallet (PermissionError → 403 otherwise) — this closes the
      agent-record hijack via re-registration.
    * Grandfathered records (registered before ownership) keep working;
      the first valid proof on re-registration claims ownership.
    * A claim on an owned id by a *different* wallet is rejected — a valid
      signature from the non-owner never transfers ownership (transfers go
      through ``transfer_agent_record``).
    """
    parsed = _normalize_registration(body)
    agent_id = parsed["agent_id"]
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError("agent_id must be 1-128 chars of A-Za-z0-9._:-")
    if _internal_reputation is None and _is_protected_agent_id(agent_id):
        # The platform agent is seeded internally at startup; it cannot be
        # registered (or have its wallet/callback overwritten) via the API.
        raise ValueError("reserved agent_id")
    wallet = parsed["wallet"]
    if wallet and not _WALLET_RE.match(wallet):
        raise ValueError("wallet must be a 0x-prefixed 20-byte hex address")
    proof = _extract_registration_proof(body)
    verified_wallet = _verify_wallet_proof(
        message=_register_message(agent_id, proof["ts"] or 0),
        signature=proof["signature"],
        wallet=proof["wallet"],
        timestamp_ms=proof["ts"],
    )
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        if agent_id not in fabric.agents and len(fabric.agents) >= MAX_AGENTS:
            raise OverflowError("directory full")
        existing = fabric.agents.get(agent_id) or {}
        owner = str(existing.get("owner_wallet") or "").lower()
        if _internal_reputation is not None:
            # Internal platform seed: bypasses proof, identity is "internal".
            identity = "internal"
        elif existing and owner:
            # Owned record: only the owner wallet may re-register.
            if not verified_wallet or verified_wallet != owner:
                raise PermissionError(
                    "re-registration requires the owner wallet signature")
            identity = "verified"
        elif existing:
            # Grandfathered record: first valid proof claims ownership.
            if verified_wallet:
                owner = verified_wallet
                identity = "verified"
            else:
                identity = str(existing.get("identity") or "unverified")
        else:
            # New claim.
            if verified_wallet:
                owner = verified_wallet
                identity = "verified"
            elif _registration_proof_required():
                raise PermissionError("registration proof required")
            else:
                identity = "unverified"
        if _internal_reputation is not None:
            reputation = float(_internal_reputation)
        else:
            reputation = float(existing.get("reputation") or 0.0)
        agent = {
            "agent_id": agent_id,
            "name": parsed["name"],
            "description": parsed["description"],
            "version": parsed["version"],
            "capability_tags": parsed["capability_tags"],
            "skills": parsed["skills"],
            "rpc_callback": parsed["rpc_callback"],
            "wallet": wallet.lower() if wallet else "",
            "chain_id": parsed["chain_id"],
            "sinc_staked": parsed["sinc_stake"],
            "reputation": reputation,
            # First-registration identity (wave 32): the wallet bound at
            # claim time, and whether the binding was signature-verified.
            "owner_wallet": owner,
            "identity": identity,
            "sponsored": bool(existing.get("sponsored", True)),
            "last_heartbeat": ts,
            "registered_at": int(existing.get("registered_at") or ts),
            # Origin classification for registration-velocity ranking
            # (operating directive): the platform agent is internal,
            # every API registration is external.  Preserved across
            # re-registrations like registered_at.
            "origin": str(existing.get("origin") or ("internal" if agent_id == _PLATFORM_AGENT_ID else "external")),
        }
        # probation / requires_merit / status derive from the merit
        # threshold in one place (never declared, never KYA-derived).
        _apply_reputation(agent, reputation)
        fabric.agents[agent_id] = agent
        snapshot = dict(agent)
    _save_agents(fabric)
    fabric.publish("agent.registered", snapshot["capability_tags"], {"agent_id": agent_id, "tags": snapshot["capability_tags"], "wallet": snapshot["wallet"]})
    _kya_listed(snapshot)
    if snapshot.get("kya_id"):
        with fabric.lock:
            live = fabric.agents.get(agent_id)
            if live is not None:
                live["kya_id"] = snapshot["kya_id"]
                live["kya_status"] = snapshot.get("kya_status")
        _save_agents(fabric)
    return snapshot


def transfer_agent_record(body: Dict[str, Any]) -> Dict[str, Any]:
    """Transfer an agent_id to a new owner wallet.

    Transfer policy (wave 32): only the current owner wallet can initiate a
    transfer, by presenting a fresh EIP-191 signature over the canonical
    transfer message ``SINCOR-TRANSFER|<agent_id>|<new_wallet>|<timestamp_ms>``
    (see ``a2a_identity``). The new wallet becomes ``owner_wallet`` and the
    record's declared ``wallet``; ``identity`` becomes ``"verified"``.

    Records without an owner (grandfathered, pre-ownership) cannot be
    transferred until ownership is first claimed via a signed
    re-registration. A transfer signature from any wallet other than the
    current owner is rejected — there is no admin override path.

    Raises: ValueError (bad input) → 400; PermissionError → 403;
    KeyError (unknown agent) → 404.
    """
    agent_id = str(body.get("agent_id") or "").strip()
    new_wallet = str(body.get("new_wallet") or body.get("wallet") or "").strip()
    if not agent_id:
        raise ValueError("agent_id is required")
    if not new_wallet or not _WALLET_RE.match(new_wallet):
        raise ValueError("new_wallet must be a 0x-prefixed 20-byte hex address")
    signature = body.get("transfer_signature") or body.get("signature") or ""
    ts = body.get("transfer_ts") or body.get("timestamp")
    fabric = get_fabric()
    with fabric.lock:
        existing = fabric.agents.get(agent_id)
        if not existing:
            raise KeyError(agent_id)
        owner = str(existing.get("owner_wallet") or "").lower()
        if not owner:
            raise PermissionError(
                "agent_id has no owner yet; claim it with a signed re-registration first")
        recovered = _verify_wallet_proof(
            message=_transfer_message(agent_id, new_wallet, ts or 0),
            signature=signature,
            wallet=owner,
            timestamp_ms=ts,
        )
        if not recovered or recovered != owner:
            raise PermissionError("transfer requires the current owner wallet signature")
        existing["owner_wallet"] = new_wallet.lower()
        existing["identity"] = "verified"
        existing["wallet"] = new_wallet.lower()
        snapshot = dict(existing)
    _save_agents(fabric)
    fabric.publish("agent.transferred",
                   list(snapshot.get("capability_tags") or []),
                   {"agent_id": agent_id, "owner_wallet": snapshot["owner_wallet"]})
    return snapshot


def heartbeat_agent(agent_id: str, signature: str = "") -> Dict[str, Any]:
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        agent = fabric.agents.get(agent_id)
        if not agent:
            raise KeyError(agent_id)
        agent["last_heartbeat"] = ts
        tags = list(agent.get("capability_tags") or [])
    fabric.publish("agent.heartbeat", tags, {"agent_id": agent_id, "ttl_s": HEARTBEAT_TTL_S})
    _kya_heartbeat(agent_id)
    try:
        from sincor2.a2a_inbound_market import expire_stale_assignments

        expire_stale_assignments()
    except Exception as err:
        logger.debug("expire_stale_assignments skipped: %s", err)
    return {"ok": True, "agent_id": agent_id, "expires_at": ts + HEARTBEAT_TTL_S * 1000}


def ensure_platform_agent() -> Dict[str, Any]:
    base = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("SITE_URL") or "https://getsincor.com").rstrip("/")
    snapshot = register_agent_record({
        "agent_id": _PLATFORM_AGENT_ID,
        "name": "SINCOR Agent Swarm",
        "description": "Native SINCOR execution surface on getsincor.com",
        "version": "2.0.0",
        "capability_tags": ["lead-enrichment", "outreach-sequence", "competitor-intel", "axiom-payment", "x402"],
        "rpc_callback": f"{base}/api/a2a",
        "wallet": os.environ.get("TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"),
        "chain_id": BASE_CHAIN_ID,
    }, _internal_reputation=1.0)
    _start_platform_heartbeat()
    logger.info("[A2A] Platform agent live id=%s status=%s", snapshot.get("agent_id"), snapshot.get("status"))
    return snapshot


def _start_platform_heartbeat() -> None:
    global _HEARTBEAT_THREAD
    if _HEARTBEAT_THREAD and _HEARTBEAT_THREAD.is_alive():
        return

    def _loop() -> None:
        while not _HEARTBEAT_STOP.wait(HEARTBEAT_TTL_S / 2):
            try:
                heartbeat_agent(_PLATFORM_AGENT_ID)
            except Exception:
                try:
                    ensure_platform_agent()
                except Exception as seed_err:
                    logger.warning("[A2A] platform reseed failed: %s", seed_err)

    _HEARTBEAT_THREAD = threading.Thread(target=_loop, name="sincor-platform-heartbeat", daemon=True)
    _HEARTBEAT_THREAD.start()


def list_agents(live_only: bool = False) -> List[Dict[str, Any]]:
    fabric = get_fabric()
    ts = _now_ms()
    ttl_ms = HEARTBEAT_TTL_S * 1000
    # Consulted outside fabric.lock: KYA has its own lock, one batch call
    # (no N+1). Fail-open: {} on error, rows keep their stored kya_status.
    kya_statuses = _live_kya_statuses()
    with fabric.lock:
        out = []
        for agent in fabric.agents.values():
            kya_id = agent.get("kya_id")
            live_kya = kya_statuses.get(kya_id) if kya_id else None
            if live_kya == "revoked":
                continue  # revoked agents disappear from the directory
            age = ts - int(agent.get("last_heartbeat") or 0)
            status = "live" if age <= ttl_ms else "stale"
            if agent.get("probation") and status == "live":
                status = "probation"
            if live_only and status == "stale":
                continue
            row = dict(agent)
            row["status"] = status
            if live_kya is not None:
                row["kya_status"] = live_kya  # replace stale value with live KYA status
            row["heartbeat_age_ms"] = age
            out.append(row)
        return sorted(out, key=lambda a: a.get("registered_at") or 0, reverse=True)


def mount(app: Flask) -> None:
    from flask import Blueprint
    from sincor2.a2a_inbound_market import attach_market_routes, seed_probation_tasks

    bp = Blueprint("a2a_inbound", __name__)

    @bp.post("/api/marketplace/register")
    @bp.post("/v1/a2a/register")
    @bp.post("/api/v1/a2a/register")
    def v1_register():
        body = request.get_json(silent=True) or {}
        try:
            agent = register_agent_record(body)
        except ValueError as err:
            return _http_error(str(err), 400)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except OverflowError as err:
            return _http_error(str(err), 503)
        return jsonify({
            "agent_id": agent["agent_id"],
            "status": "registered",
            "name": agent["name"],
            "probation": bool(agent.get("probation")),
            "heartbeat_ttl_s": HEARTBEAT_TTL_S,
            "stream_url": "/v1/a2a/stream",
            "kya_id": agent.get("kya_id"),
            "kya_status": agent.get("kya_status"),
            "identity": agent.get("identity"),
            "owner_wallet": agent.get("owner_wallet"),
        }), 201

    @bp.post("/v1/a2a/transfer")
    def v1_transfer():
        body = request.get_json(silent=True) or {}
        try:
            agent = transfer_agent_record(body)
        except ValueError as err:
            return _http_error(str(err), 400)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except KeyError:
            return _http_error("unknown agent", 404)
        return jsonify({
            "agent_id": agent["agent_id"],
            "status": "transferred",
            "owner_wallet": agent.get("owner_wallet"),
            "identity": agent.get("identity"),
        }), 200

    @bp.post("/v1/a2a/heartbeat")
    def v1_heartbeat():
        body = request.get_json(silent=True) or {}
        agent_id = str(body.get("agent_id") or request.args.get("agent_id") or "").strip()
        if not agent_id:
            return _http_error("agent_id is required", 400)
        try:
            return jsonify(heartbeat_agent(agent_id))
        except KeyError:
            return _http_error("unknown agent", 404)

    @bp.get("/v1/a2a/agents")
    @bp.get("/api/marketplace/agents")
    def v1_agents():
        agents = list_agents(live_only=request.args.get("live", "0") in ("1", "true", "yes"))
        return jsonify({"agents": agents, "count": len(agents)})

    @bp.get("/v1/a2a/directory")
    def v1_directory():
        kpis = health_snapshot()
        try:
            from sincor2.kya_registry import snapshot as kya_snapshot

            kpis["kya"] = kya_snapshot()
        except Exception:
            pass
        try:
            from sincor2.a2a_integration import PLATFORM_URL
        except Exception:
            PLATFORM_URL = ""
        return jsonify({
            "agents": list_agents(),
            "kpis": kpis,
            # Machine-readable entry points, surfaced aggressively.
            "quote": f"{PLATFORM_URL}/api/a2a/quote",
            "docs": f"{PLATFORM_URL}/docs/a2a",
            "agentCard": f"{PLATFORM_URL}/.well-known/agent-card.json",
            "agentCards": f"{PLATFORM_URL}/v1/a2a/cards",
        })

    @bp.get("/v1/a2a/cards")
    def v1_cards():
        """Full agent-card registry: platform card + vertical pack cards.

        Every skill in every card carries a ``quoteUrl`` so agent clients
        can price a skill without scraping HTML or guessing endpoints.
        """
        from sincor2.a2a_integration import PLATFORM_URL, build_agent_card

        def _with_quote_urls(card: Dict[str, Any]) -> Dict[str, Any]:
            for skill in card.get("skills", []) or []:
                sid = skill.get("id")
                if sid and "quoteUrl" not in skill:
                    skill["quoteUrl"] = f"{PLATFORM_URL}/api/a2a/quote?skill_id={sid}"
            return card

        cards = [_with_quote_urls(build_agent_card().to_dict())]
        try:
            from verticals.loader import load_agent_cards

            cards.extend(_with_quote_urls(c) for c in load_agent_cards())
        except Exception as err:
            logger.warning("[A2A] vertical cards unavailable: %s", err)
        return jsonify({"cards": cards, "count": len(cards)})

    @bp.get("/v1/a2a/chain")
    def v1_chain():
        return jsonify(probe_base_chain())

    attach_market_routes(bp)
    # A2A abuse-class rate limits (policies in sincor2.a2a_rate_limits).
    # Only ENDPOINT_POLICY routes are limited; admin/health/heartbeat/
    # proofs/stream stay untouched.
    from sincor2.a2a_rate_limits import a2a_rate_limit_check
    bp.before_request(a2a_rate_limit_check)
    app.register_blueprint(bp)
    try:
        from sincor2.kya_blueprint import kya_bp

        if "kya" not in (getattr(app, "blueprints", {}) or {}):
            app.register_blueprint(kya_bp)
            logger.info("[KYA] /v1/kya mounted")
    except Exception as err:
        logger.warning("[KYA] blueprint skipped: %s", err)
    try:
        ensure_platform_agent()
    except Exception as err:
        logger.warning("[A2A] Platform agent seed skipped: %s", err)
    try:
        seeded = seed_probation_tasks()
        logger.info("[A2A] Seeded %s probation auctions", len(seeded))
    except Exception as err:
        logger.warning("[A2A] Probation seed skipped: %s", err)
    logger.info("[A2A] Inbound register + market + heartbeat mounted")
