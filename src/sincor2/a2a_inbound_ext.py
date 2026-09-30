"""A2A inbound routes + agent operations. Imported by a2a_inbound.register."""
from __future__ import annotations

import hashlib
import hmac
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
    REGISTRATION_PROOF_FRESHNESS_MS,
    REREGISTRATION_DOMAIN,
    RegistrationAuthError,
    _AGENT_ID_RE,
    _HEARTBEAT_STOP,
    _PLATFORM_AGENT_ID,
    _WALLET_RE,
    _http_error,
    _normalize_registration,
    _now_ms,
    _reregistration_message_from_parsed,
    _save_agents,
    _apply_reputation,
    get_fabric,
    health_snapshot,
)

logger = logging.getLogger("sincor.a2a.inbound")
_HEARTBEAT_THREAD = None

HEARTBEAT_PROOF_DOMAIN = "SINCOR-HEARTBEAT"
HEARTBEAT_PROOF_FRESHNESS_MS = 5 * 60 * 1000


class HeartbeatAuthError(ValueError):
    """A heartbeat carried no usable proof of identity (→ HTTP 401)."""


def build_heartbeat_message(agent_id: str, ts_ms: int) -> str:
    """Canonical EIP-191 message an agent signs to prove heartbeat ownership.

    Binds the agent_id and a millisecond timestamp so the signature cannot
    be replayed as another agent's heartbeat or outside the freshness window.
    """
    return f"{HEARTBEAT_PROOF_DOMAIN}\n{agent_id}\n{int(ts_ms)}"


def _recover_heartbeat_signer(message: str, signature: str) -> str:
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except Exception as exc:
        raise HeartbeatAuthError("signature verification unavailable") from exc
    try:
        return str(Account.recover_message(
            encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise HeartbeatAuthError("bad heartbeat signature") from exc


def _operator_heartbeat_token_ok() -> bool:
    """Shared-operator heartbeat credential — the same mechanism as the
    wardrobe ``/api/a2a/heartbeat``. Lets first-party/ops agents (including
    the liveness runner) heartbeat without holding wallet keys. The token is
    env-only (``AGENT_HEARTBEAT_TOKEN``) and header-only; it is never read
    from the request body."""
    expected = (os.environ.get("AGENT_HEARTBEAT_TOKEN") or "").strip()
    if not expected:
        return False
    got = (
        request.headers.get("X-Sincor-Heartbeat")
        or request.headers.get("Authorization", "").removeprefix("Bearer ")
        or ""
    ).strip()
    if not got:
        return False
    return hmac.compare_digest(
        hashlib.sha256(got.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _require_heartbeat_auth(agent_id: str, body: Dict[str, Any]) -> None:
    """Fail-closed heartbeat authentication.

    Accepts either the operator heartbeat token or an EIP-191
    (``personal_sign``) signature by the agent's *registered* wallet over
    ``build_heartbeat_message(agent_id, ts)`` with a fresh timestamp.
    Anything else → :class:`HeartbeatAuthError` (→ HTTP 401).
    """
    if _operator_heartbeat_token_ok():
        return
    signature = str(body.get("signature") or "").strip()
    ts_raw = body.get("ts") if body.get("ts") is not None else body.get("timestamp")
    if not signature or ts_raw is None:
        raise HeartbeatAuthError(
            "heartbeat requires an EIP-191 signature or the operator heartbeat token")
    try:
        ts = int(ts_raw)
    except (TypeError, ValueError) as exc:
        raise HeartbeatAuthError("bad heartbeat timestamp") from exc
    if abs(_now_ms() - ts) > HEARTBEAT_PROOF_FRESHNESS_MS:
        raise HeartbeatAuthError("stale heartbeat proof")
    fabric = get_fabric()
    with fabric.lock:
        agent = fabric.agents.get(agent_id)
    wallet = str((agent or {}).get("wallet") or "").strip()
    if not wallet:
        raise HeartbeatAuthError(
            "agent has no registered wallet; heartbeat requires the operator token")
    signer = _recover_heartbeat_signer(build_heartbeat_message(agent_id, ts), signature)
    if signer.lower() != wallet.lower():
        raise HeartbeatAuthError("heartbeat signature is not from the registered wallet")


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


def _recover_registration_signer(message: str, signature: str) -> str:
    """Recover the EIP-191 signer address of a re-registration proof.

    Fail-closed: if eth_account is unavailable the proof cannot be checked,
    so verification (and therefore the mutation) is refused.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except Exception as exc:
        raise RegistrationAuthError(
            "signature verification unavailable") from exc
    try:
        return str(Account.recover_message(
            encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise RegistrationAuthError("bad registration signature") from exc


def _require_reregistration_proof(existing: Dict[str, Any],
                                  parsed: Dict[str, Any],
                                  body: Dict[str, Any]) -> None:
    """Enforce proof-of-control before an existing agent record is mutated
    (G2.2). The caller must present an EIP-191 signature, made by the
    currently registered wallet, over the exact new record contents plus a
    fresh timestamp. Raises RegistrationAuthError (→ HTTP 403) otherwise."""
    signature = body.get("registration_signature") or ""
    ts_raw = body.get("registration_ts")
    if not signature or ts_raw is None:
        raise RegistrationAuthError(
            "re-registration requires an EIP-191 signature by the registered "
            "wallet (body fields: registration_signature, registration_ts)")
    try:
        ts_ms = int(ts_raw)
    except (TypeError, ValueError) as exc:
        raise RegistrationAuthError(
            "registration_ts must be an integer unix-ms timestamp") from exc
    if abs(_now_ms() - ts_ms) > REGISTRATION_PROOF_FRESHNESS_MS:
        raise RegistrationAuthError(
            "registration proof expired; sign a fresh challenge")
    registered_wallet = (existing.get("wallet") or "").strip().lower()
    if not registered_wallet:
        # No cryptographic identity is bound to this record, so control
        # cannot be proven. Fail closed rather than let anyone claim it:
        # the owner registers a new agent_id with a wallet.
        raise RegistrationAuthError(
            "this record has no wallet bound, so control cannot be proven; "
            "register a new agent_id with a wallet")
    message = _reregistration_message_from_parsed(parsed, ts_ms)
    signer = _recover_registration_signer(message, str(signature)).lower()
    if signer != registered_wallet:
        raise RegistrationAuthError(
            "registration signature is not from the registered wallet")


def register_agent_record(body: Dict[str, Any],
                          _internal_reputation: Optional[float] = None) -> Dict[str, Any]:
    """Register (or re-register) an agent record.

    Reputation is earned-only and never declared: ``body["reputation"]`` is
    silently ignored (existing clients may still send it; it is read-only
    in API responses). New agents start at 0.0 (probation); re-registration
    preserves already-earned reputation. The only path that assigns
    reputation at registration time is the internal platform seed, via
    ``_internal_reputation`` — the HTTP route never passes it.

    Re-registration (any update to an existing record) requires proof of
    control: an EIP-191 signature by the currently registered wallet over
    the exact new record contents plus a fresh ``registration_ts``
    (see ``build_reregistration_message`` in ``sincor2.a2a_inbound``).
    Without it the update is refused with ``RegistrationAuthError``.
    """
    parsed = _normalize_registration(body)
    agent_id = parsed["agent_id"]
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError("agent_id must be 1-128 chars of A-Za-z0-9._:-")
    if agent_id == _PLATFORM_AGENT_ID and _internal_reputation is None:
        # The platform agent is seeded internally at startup; it cannot be
        # registered (or have its wallet/callback overwritten) via the API.
        raise ValueError("reserved agent_id")
    wallet = parsed["wallet"]
    if wallet and not _WALLET_RE.match(wallet):
        raise ValueError("wallet must be a 0x-prefixed 20-byte hex address")
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        if agent_id not in fabric.agents and len(fabric.agents) >= MAX_AGENTS:
            raise OverflowError("directory full")
        existing = fabric.agents.get(agent_id) or {}
        if existing and _internal_reputation is None:
            _require_reregistration_proof(existing, parsed, body)
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


def heartbeat_agent(agent_id: str) -> Dict[str, Any]:
    """Mark an agent alive. Authentication happens at the HTTP layer
    (``v1_heartbeat`` → ``_require_heartbeat_auth``); direct callers here are
    server-side (platform bootstrap, liveness sweep) and already trusted."""
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
        except RegistrationAuthError as err:
            return _http_error(str(err), 403)
        except ValueError as err:
            return _http_error(str(err), 400)
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
            # Re-registration is proof-gated: any later update to this
            # record must carry registration_signature (EIP-191, by the
            # registered wallet) + registration_ts over the message built
            # by sincor2.a2a_inbound.build_reregistration_message().
            "reregistration": {
                "requires": "EIP-191 signature by the registered wallet",
                "fields": ["registration_signature", "registration_ts"],
                "domain": REREGISTRATION_DOMAIN,
                "freshness_ms": REGISTRATION_PROOF_FRESHNESS_MS,
            },
        }), 201

    @bp.post("/v1/a2a/heartbeat")
    def v1_heartbeat():
        body = request.get_json(silent=True) or {}
        agent_id = str(body.get("agent_id") or request.args.get("agent_id") or "").strip()
        if not agent_id:
            return _http_error("agent_id is required", 400)
        try:
            _require_heartbeat_auth(agent_id, body)
        except HeartbeatAuthError as err:
            return _http_error(str(err), 401)
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
