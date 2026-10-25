"""KYA Trust Score — single-read x402 paid resource (skills gap G2).

One agent in (``agent_id`` and/or ``wallet``), one trust score out,
priced as the ``kya-trust-score`` resource in
``config/x402_pricing.yaml`` ($0.015 USDC on Base — the AsterPay deep-
analysis price point, skills-gap-map-2026-10-24 §2.4). A free verify
tier (``verify_agent`` / ``GET /api/kya/verify``) returns only
known/status/verified/tier — never the score or its components.

The score is computed ONLY from records that actually exist in three
real SINCOR stores — no invented inputs, no external data:

1. KYA registry — ``sincor2.kya.registry.KYARegistry`` records
   (status listed/bound/staked/verified/revoked, ``verified`` flag,
   ``stake_axm_wei`` against that registry's ``MIN_STAKE_WEI``).
2. Stake ledger — ``sincor2.onchain.stake_ledger.StakeLedger``:
   the per-agent reputation record (dispute-free completions,
   distinct posters, upheld disputes, first-seen tenure) scored with
   the ledger's OWN ``reputation_score`` (R_i), plus deposited /
   slashed wei from the same agent record.
3. Settlement history — ``sincor2.a2a_integration.ReputationLedger``
   settlement-row counts under the caller's namespaced keys
   (``wallet:<addr>`` / ``id:<agent_id>``).

Honesty contract:

- An agent with NO record in any of the three stores returns
  ``status: "unknown"`` with ``score: None`` — never a fabricated
  number. A revoked KYA identity returns ``status: "revoked"``.
- Every response carries the full component breakdown (points and
  the raw record values they came from) so a buyer can audit the
  score and recompute it by hand.
- Scope is labelled honestly: this is the SINCOR registry's view,
  ERC-8004-aligned in shape. It is NOT an ERC-8004 on-chain
  registration lookup and never claims to be.

Score (0-100, integer, hand-computable from the response itself):

- identity:   verified 30 / staked 25 / bound 20 / listed 10 /
              revoked-or-expired 0
- kya_stake:  min(10, stake_axm_wei * 10 // MIN_STAKE_WEI)
- execution:  round(40 * R_i)   (R_i from the stake ledger)
- settlements: min(20, 2 * settlement_count)

Tiers: >=75 trusted, >=50 established, >=25 emerging, else low;
``unknown`` when there is no evidence at all, ``revoked`` when the
KYA identity is revoked.

Payment is NOT handled here: the deep read sits behind the standard
x402 flow (bound challenge -> verify with
require_bound_challenge=True -> single-use access token consumed by
``access_granted`` in the billing blueprint).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

RESOURCE_ID = "kya-trust-score"

SCOPE_LABEL = "sincor_registry_only"
STANDARD_NOTE = (
    "ERC-8004-aligned response shape; scores are computed from the "
    "SINCOR KYA registry, stake ledger, and settlement ledger only — "
    "not an ERC-8004 on-chain registration"
)

_IDENTITY_POINTS = {
    "verified": 30,
    "staked": 25,
    "bound": 20,
    "listed": 10,
    "revoked": 0,
    "expired": 0,
}


# ---------------------------------------------------------------------------
# Store accessors (seams: tests inject real store instances / records)
# ---------------------------------------------------------------------------

def load_kya_record(
    agent_id: Optional[str] = None,
    wallet: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Production loader: the live KYA package registry singleton."""
    from sincor2.kya.registry import get_registry

    return get_registry().lookup(agent_id=agent_id, wallet=wallet)


def stake_view_from_ledger(
    ledger: Any,
    agent_id: str,
    *,
    now: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Extract the score-relevant view of one agent from a REAL
    StakeLedger. Pure read of the ledger's stored agent record: the
    record must already exist (a peek, never a create), R_i comes from
    the ledger's own ``reputation_score``, and the raw rep counts are
    read back from the same stored record after its pruning."""
    raw_agents = ledger._data.get("agents", {})
    if agent_id not in raw_agents:
        return None
    now = int(now) if now is not None else int(time.time())
    r_i = ledger.reputation_score(agent_id, now=now)
    raw = ledger._data.get("agents", {}).get(agent_id) or {}
    rep = raw.get("rep") or {}
    completions = [c for c in rep.get("completions", [])
                   if isinstance(c, dict)]
    posters = {str(c.get("poster")) for c in completions if c.get("poster")}
    first_seen = rep.get("first_seen")
    tenure_days = (
        max(0.0, (now - int(first_seen)) / 86400.0) if first_seen else 0.0
    )
    locks = raw.get("locks") or {}
    return {
        "deposited_wei": str(raw.get("deposited_wei", "0")),
        "locked_wei": str(sum(int(v) for v in locks.values())),
        "slashed_wei": str(raw.get("slashed_wei", "0")),
        "r_i": r_i,
        "completions": len(completions),
        "distinct_posters": len(posters),
        "upheld_disputes": len(rep.get("upheld", [])),
        "first_seen": first_seen,
        "tenure_days": tenure_days,
    }


def load_stake_view(
    agent_id: Optional[str],
    *,
    now: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Production loader: the default-path StakeLedger."""
    if not agent_id:
        return None
    from sincor2.onchain.stake_ledger import StakeLedger

    return stake_view_from_ledger(StakeLedger(), agent_id, now=now)


def settlement_keys(
    agent_id: Optional[str],
    wallet: Optional[str],
) -> list:
    """ReputationLedger namespaced keys for this agent (see
    ``a2a_integration._reputation_key``): verified-wallet rows live
    under ``wallet:<addr>``, caller-id rows under ``id:<agent_id>``.
    The two namespaces are disjoint, so summing them cannot
    double-count a settlement."""
    keys = []
    if wallet:
        keys.append("wallet:" + str(wallet).lower())
    if agent_id:
        keys.append("id:" + str(agent_id))
    return keys


def load_settlement_count(
    agent_id: Optional[str] = None,
    wallet: Optional[str] = None,
) -> int:
    """Production loader: counts from the live A2A ReputationLedger."""
    from sincor2.a2a_integration import _reputation_ledger

    return sum(
        _reputation_ledger.score(k)
        for k in settlement_keys(agent_id, wallet)
    )


# ---------------------------------------------------------------------------
# Scoring (pure: every input is a real record extracted above)
# ---------------------------------------------------------------------------

def _tier_for(score: int) -> str:
    if score >= 75:
        return "trusted"
    if score >= 50:
        return "established"
    if score >= 25:
        return "emerging"
    return "low"


def compute_trust_score(
    *,
    agent_id: Optional[str] = None,
    wallet: Optional[str] = None,
    kya_record: Optional[Dict[str, Any]] = None,
    stake_view: Optional[Dict[str, Any]] = None,
    settlements: int = 0,
) -> Dict[str, Any]:
    """Score one agent from already-extracted real records.

    ``kya_record`` is a KYA registry record dict (or None),
    ``stake_view`` the dict from ``stake_view_from_ledger`` (or None),
    ``settlements`` the ReputationLedger row count. Nothing else is
    consulted; an all-empty input is the honest ``unknown``.
    """
    from sincor2.kya.registry import MIN_STAKE_WEI

    agent_id = agent_id or (kya_record or {}).get("agent_id")
    wallet = wallet or (kya_record or {}).get("wallet") or (
        kya_record or {}).get("principal")

    revoked = bool(kya_record and (
        kya_record.get("revoked")
        or kya_record.get("status") == "revoked"))
    status = str((kya_record or {}).get("status") or "")
    identity_points = (
        _IDENTITY_POINTS.get(status, 0) if kya_record else 0)
    if revoked:
        identity_points = 0

    stake_axm_wei = 0
    if kya_record:
        try:
            stake_axm_wei = int(kya_record.get("stake_axm_wei") or 0)
        except (TypeError, ValueError):
            stake_axm_wei = 0
    kya_stake_points = min(10, stake_axm_wei * 10 // MIN_STAKE_WEI)

    r_i = float(stake_view["r_i"]) if stake_view else 0.0
    execution_points = int(round(40 * r_i))
    settlement_points = min(20, 2 * int(settlements))

    components = {
        "identity": {
            "points": identity_points,
            "max_points": 30,
            "source": "sincor2.kya.registry.KYARegistry",
            "kya_id": (kya_record or {}).get("kya_id"),
            "status": status or None,
            "verified": bool((kya_record or {}).get("verified")),
            "revoked": revoked,
        },
        "kya_stake": {
            "points": kya_stake_points,
            "max_points": 10,
            "source": "sincor2.kya.registry.KYARegistry.stake_axm_wei",
            "stake_axm_wei": str(stake_axm_wei),
            "min_stake_wei": str(MIN_STAKE_WEI),
        },
        "execution": {
            "points": execution_points,
            "max_points": 40,
            "source": "sincor2.onchain.stake_ledger.StakeLedger",
            "r_i": r_i,
            "completions": (stake_view or {}).get("completions", 0),
            "distinct_posters": (stake_view or {}).get(
                "distinct_posters", 0),
            "upheld_disputes": (stake_view or {}).get(
                "upheld_disputes", 0),
            "tenure_days": (stake_view or {}).get("tenure_days", 0.0),
            "first_seen": (stake_view or {}).get("first_seen"),
            "deposited_wei": (stake_view or {}).get(
                "deposited_wei", "0"),
            "locked_wei": (stake_view or {}).get("locked_wei", "0"),
            "slashed_wei": (stake_view or {}).get("slashed_wei", "0"),
        },
        "settlements": {
            "points": settlement_points,
            "max_points": 20,
            "source": "sincor2.a2a_integration.ReputationLedger",
            "count": int(settlements),
            "ledger_keys": settlement_keys(agent_id, wallet),
        },
    }

    has_evidence = bool(
        kya_record is not None or stake_view is not None
        or int(settlements) > 0)
    base: Dict[str, Any] = {
        "ok": True,
        "resource": RESOURCE_ID,
        "agent_id": agent_id,
        "wallet": wallet,
        "scope": SCOPE_LABEL,
        "standard_note": STANDARD_NOTE,
        "components": components,
    }
    if not has_evidence:
        return {
            **base,
            "status": "unknown",
            "score": None,
            "tier": "unknown",
            "detail": (
                "no KYA registry record, no stake-ledger record, and "
                "no settlement history for this agent — trust is "
                "unknown, not zero"
            ),
        }
    score = (identity_points + kya_stake_points + execution_points
             + settlement_points)
    if revoked:
        return {
            **base,
            "status": "revoked",
            "score": score,
            "tier": "revoked",
            "detail": "KYA identity is revoked in the SINCOR registry",
        }
    return {
        **base,
        "status": "scored",
        "score": score,
        "tier": _tier_for(score),
        "detail": "score computed from SINCOR registry/ledger records",
    }


def _resolve(
    agent_id: Optional[str],
    wallet: Optional[str],
    *,
    now: Optional[int] = None,
) -> Dict[str, Any]:
    kya_record = load_kya_record(agent_id=agent_id, wallet=wallet)
    eff_agent = agent_id or (kya_record or {}).get("agent_id")
    eff_wallet = wallet or (kya_record or {}).get("wallet") or (
        kya_record or {}).get("principal")
    stake_view = load_stake_view(eff_agent, now=now)
    settlements = load_settlement_count(
        agent_id=eff_agent, wallet=eff_wallet)
    return compute_trust_score(
        agent_id=eff_agent, wallet=eff_wallet, kya_record=kya_record,
        stake_view=stake_view, settlements=settlements)


def verify_agent(
    agent_id: Optional[str] = None,
    wallet: Optional[str] = None,
) -> Dict[str, Any]:
    """Free verify tier: known / status / verified / tier ONLY.

    The score and every component value are paid fields and are never
    included here."""
    full = _resolve(agent_id, wallet)
    return {
        "ok": True,
        "resource": RESOURCE_ID,
        "tier_level": "free_verify",
        "agent_id": full["agent_id"],
        "wallet": full["wallet"],
        "known": full["status"] != "unknown",
        "status": full["status"],
        "verified": full["components"]["identity"]["verified"],
        "tier": full["tier"],
        "scope": SCOPE_LABEL,
        "standard_note": STANDARD_NOTE,
    }


def serve_kya_verify(
    agent_id: Optional[str],
    wallet: Optional[str],
) -> tuple[int, Dict[str, Any]]:
    agent_id = (agent_id or "").strip() or None
    wallet = (wallet or "").strip() or None
    if not agent_id and not wallet:
        return 400, {"ok": False, "error": "agent_id_or_wallet_required"}
    try:
        return 200, verify_agent(agent_id=agent_id, wallet=wallet)
    except Exception:
        return 500, {"ok": False, "error": "trust_stores_unavailable"}


def serve_kya_trust_score(
    payload: Optional[Dict[str, Any]],
) -> tuple[int, Dict[str, Any]]:
    """Serve-path handler for the paid deep read (called by the
    billing blueprint ONLY after the x402 access token has been
    consumed). The agent to score arrives as ``agent_id`` and/or
    ``wallet`` (aliases: ``wallet_address``, ``agent_wallet``)."""
    payload = payload or {}
    agent_id = (
        payload.get("agent_id") or payload.get("agentId") or "")
    wallet = (
        payload.get("wallet") or payload.get("wallet_address")
        or payload.get("agent_wallet") or "")
    agent_id = str(agent_id).strip() or None
    wallet = str(wallet).strip() or None
    if not agent_id and not wallet:
        return 400, {"ok": False, "error": "agent_id_or_wallet_required"}
    try:
        return 200, _resolve(agent_id, wallet)
    except Exception:
        return 500, {"ok": False, "error": "trust_stores_unavailable"}
