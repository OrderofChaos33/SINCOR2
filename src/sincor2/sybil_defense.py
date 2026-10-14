"""Sybil-economics defenses for the SINCOR2 agent marketplace (2026-10-08).

Red-team finding (CONFIRMED): 5 free registrations/hour/IP with zero
KYA/stake/cost meant one operator could mint a sock-puppet fleet and
self-deal task payouts, and tombstoned wallets could be reborn for free.

This module is the funding-graph layer:

- :func:`cluster_identities_by_wallet` groups live agent identities by
  their registered funding wallet (no graph DB — plain wallet grouping).
- :func:`quarantine_check` is the payout-gate verdict for one agent:
  shared-wallet clusters (>1 identity) are FLAGGED on the payout record,
  and payouts are BLOCKED when the cluster has >= 3 identities OR any
  cluster member sits behind a tombstoned wallet.
- :func:`_ring_assessment` (W-81) closes the distinct-wallet bypass:
  identities minted in a registration burst that pay each other while
  still newborn are ring-shaped — correlated as one operator even though
  every wallet is distinct.

Wired into the payout path in ``a2a_inbound_market.submit_proof()``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sincor2.a2a_inbound import get_fabric
from sincor2 import kya_registry

#: A shared-wallet cluster at or above this size is sybil-shaped: the
#: payout is blocked, not staged.
QUARANTINE_CLUSTER_SIZE = 3

#: Registration-burst window (W-81): identities whose ``registered_at``
#: fall within this span of each other form a burst cohort. Fresh EOAs
#: are free, so distinct wallets cost the attacker nothing — but minting
#: N identities takes either a burst (visible) or wall-clock time
#: (expensive at fleet scale). 60 minutes is the tripwire; see
#: :func:`_ring_assessment` for the false-positive tradeoff.
SYBIL_BURST_WINDOW_MS = 3_600_000

#: Burst cohort at or above this size is ring-shaped enough to assess.
SYBIL_RING_COHORT_SIZE = 3

#: Below this reputation an identity counts as "newborn" for ring
#: purposes. One dispute-free settlement earns +0.2, so 1.0 is five
#: settled payouts — the cheapest credible history on the platform.
SYBIL_LOW_REPUTATION = 1.0


def cluster_identities_by_wallet() -> Dict[str, List[str]]:
    """Group agent identities by registered funding wallet.

    Returns only wallets backing MORE THAN ONE identity (singletons are
    not clusters). Wallets are normalized to lowercase; wallet-less
    identities are excluded — they carry no funding-graph signal.
    """
    fabric = get_fabric()
    with fabric.lock:
        groups: Dict[str, List[str]] = {}
        for agent_id, agent in fabric.agents.items():
            wallet = str(agent.get("wallet") or "").strip().lower()
            if wallet:
                groups.setdefault(wallet, []).append(agent_id)
    return {w: sorted(ids) for w, ids in groups.items() if len(ids) > 1}


def _tombstoned_members(members: List[str]) -> List[str]:
    """Cluster members that sit behind a tombstoned wallet.

    Checked two ways: the member's CURRENT wallet is tombstoned, or a
    tombstone was written naming the member's agent_id (covers the case
    where the identity re-registered onto a fresh wallet after ghosting —
    the shed is still visible).
    """
    fabric = get_fabric()
    tombstoned: List[str] = []
    with fabric.lock:
        wallets = {
            aid: str((fabric.agents.get(aid) or {}).get("wallet") or "").strip().lower()
            for aid in members
        }
    tombstones = kya_registry.tombstones_snapshot().get("tombstones") or []
    tomb_wallets = {str(t.get("wallet") or "").lower() for t in tombstones}
    tomb_agent_ids = {str(t.get("agent_id") or "") for t in tombstones}
    for aid in members:
        member_wallet = wallets.get(aid) or ""
        if (member_wallet and member_wallet in tomb_wallets) or aid in tomb_agent_ids:
            tombstoned.append(aid)
    return sorted(tombstoned)


def _burst_cohort(agent_id: str) -> List[str]:
    """Identities registered in a burst alongside the candidate.

    Returns external-origin identities whose ``registered_at`` falls
    within ``SYBIL_BURST_WINDOW_MS`` of the candidate's own
    ``registered_at`` (sorted). Internal (platform) agents are excluded —
    the engine mints its own cohort and must never trip the ring
    detector. A candidate with no (or a malformed) ``registered_at``
    yields no cohort: that is absence of signal, not an error.
    """
    fabric = get_fabric()
    with fabric.lock:
        me = fabric.agents.get(agent_id) or {}
        if str(me.get("origin") or "") == "internal":
            return []
        anchor = me.get("registered_at")
        try:
            anchor_ms = int(anchor)
        except (TypeError, ValueError):
            return []
        if anchor_ms <= 0:
            return []
        cohort = []
        for aid, a in fabric.agents.items():
            if str(a.get("origin") or "") == "internal":
                continue
            try:
                ts = int(a.get("registered_at") or 0)
            except (TypeError, ValueError):
                continue
            if ts > 0 and abs(ts - anchor_ms) <= SYBIL_BURST_WINDOW_MS:
                cohort.append(aid)
    return sorted(cohort)


def _ring_assessment(agent_id: str, task_id: str = "") -> Dict[str, Any]:
    """Distinct-wallet sybil-ring assessment (W-81).

    The wallet-cluster layer is blind to an operator who mints one fresh
    EOA per identity. This layer correlates what the attacker cannot
    cheaply rotate:

    - **time**: N identities minted in a burst share a registration
      window. Dodging it means aging the fleet (wall-clock cost) or
      trickling registrations (throughput cost).
    - **standing**: a newborn (reputation < ``SYBIL_LOW_REPUTATION``)
      has no credible history. Escaping it requires settled payouts,
      which is real work — the desired behavior, not an evasion.
    - **linkage**: the payout's poster sitting inside the same burst
      cohort is the atomic sybil extraction — minted together, paying
      each other.

    Verdict: ``quarantined`` (block the payout) only when all three
    hold — burst cohort >= ``SYBIL_RING_COHORT_SIZE``, newborn winner,
    poster in cohort. ``ring_shaped`` (flag on the receipt, payout
    proceeds) when the cohort exists plus either newborn or
    in-cohort poster: suspicious, audit-visible, not conclusive.

    FALSE-POSITIVE TRADEOFF (deliberate): honest multi-wallet users are
    safe — 1-2 identities stay under the cohort size, staggered
    registrations break the burst, built-up reputation escapes
    "newborn", and open-market posters are outside any cohort. The one
    accepted collision is a newborn team whose first payouts are purely
    internal (their own poster): behaviorally identical to a sybil
    ring, so it is treated as one. The block is per-payout, never an
    account ban, and the remedy is open-market activity — which breaks
    both the poster-in-cohort and the low-reputation conditions.

    Known residual: slow-roll registration (one identity per
    window-plus-epsilon) evades the burst tripwire at 60x the time cost;
    reputation farming via genuine settlements escapes "newborn" by
    doing real work. Both remain visible to registration-velocity
    metrics and the W-89 wash-anomaly guard (pair concentration,
    closed-loop settlement patterns).
    """
    out: Dict[str, Any] = {
        "cohort": [],
        "cohort_size": 0,
        "low_reputation": False,
        "poster_id": None,
        "poster_in_cohort": False,
        "ring_shaped": False,
        "quarantined": False,
        "reason": None,
    }
    cohort = _burst_cohort(agent_id)
    out["cohort"] = cohort
    out["cohort_size"] = len(cohort)
    if len(cohort) < SYBIL_RING_COHORT_SIZE:
        return out
    fabric = get_fabric()
    with fabric.lock:
        me = fabric.agents.get(agent_id) or {}
        try:
            reputation = float(me.get("reputation") or 0.0)
        except (TypeError, ValueError):
            reputation = 0.0
        poster_id = None
        if task_id:
            poster_id = (fabric.tasks.get(task_id) or {}).get("poster_id")
    out["low_reputation"] = reputation < SYBIL_LOW_REPUTATION
    out["poster_id"] = poster_id
    out["poster_in_cohort"] = bool(poster_id) and poster_id in cohort
    if out["low_reputation"] or out["poster_in_cohort"]:
        out["ring_shaped"] = True
    if out["low_reputation"] and out["poster_in_cohort"]:
        out["quarantined"] = True
        out["reason"] = (
            "sybil ring: newborn identity (rep %.2f) paid by poster %s "
            "from the same %d-identity registration burst; payout quarantined"
            % (reputation, poster_id, len(cohort))
        )
    return out


def quarantine_check(agent_id: str, task_id: str = "") -> Dict[str, Any]:
    """Payout-gate verdict for one agent.

    ``task_id`` is optional: when the payout's task is known, the W-81
    ring layer also assesses the distinct-wallet sybil shape
    (registration burst + newborn + in-cohort poster). Without it, only
    the wallet-cluster layer runs.

    Returns a verdict dict::

        {
            "agent_id": ...,
            "wallet": ...,            # the agent's registered wallet
            "cluster_size": N,         # identities sharing that wallet
            "cluster": [agent_ids...], # the full cluster (sorted)
            "tombstoned_members": [...],
            "ring": {...},             # W-81 ring assessment (see below)
            "flagged": bool,           # note on the payout receipt
            "quarantined": bool,       # payout must be BLOCKED
            "reason": str | None,
        }

    Quarantine (block) when the cluster has >= QUARANTINE_CLUSTER_SIZE
    identities, OR any cluster member is tombstoned, OR the ring
    assessment finds a newborn identity paid by a poster from the same
    registration burst. Fail-closed: on any internal error the verdict
    is ``quarantined: True`` with the error recorded in ``reason`` —
    the money path must not pay out blind.
    """
    verdict: Dict[str, Any] = {
        "agent_id": agent_id,
        "wallet": "",
        "cluster_size": 0,
        "cluster": [],
        "tombstoned_members": [],
        "ring": {},
        "flagged": False,
        "quarantined": False,
        "reason": None,
    }
    try:
        fabric = get_fabric()
        with fabric.lock:
            agent = fabric.agents.get(agent_id) or {}
            wallet = str(agent.get("wallet") or "").strip().lower()
            members: List[str] = []
            if wallet:
                members = sorted(
                    aid for aid, a in fabric.agents.items()
                    if str(a.get("wallet") or "").strip().lower() == wallet
                )
        verdict["wallet"] = wallet
        verdict["cluster_size"] = len(members)
        verdict["cluster"] = members
        verdict["flagged"] = len(members) > 1
        if wallet:
            verdict["tombstoned_members"] = _tombstoned_members(members)
        if len(members) >= QUARANTINE_CLUSTER_SIZE:
            verdict["quarantined"] = True
            verdict["reason"] = (
                "shared-wallet cluster of %d identities (>= %d); "
                "payout quarantined" % (len(members), QUARANTINE_CLUSTER_SIZE)
            )
        elif verdict["tombstoned_members"]:
            verdict["quarantined"] = True
            verdict["reason"] = (
                "cluster member(s) tombstoned: %s; payout quarantined"
                % ",".join(verdict["tombstoned_members"])
            )
        # W-81 distinct-wallet ring layer: correlates what the attacker
        # cannot cheaply rotate (burst timing, newborn standing, poster
        # linkage) instead of the wallet alone.
        ring = _ring_assessment(agent_id, task_id)
        verdict["ring"] = ring
        if ring["ring_shaped"]:
            verdict["flagged"] = True
        if ring["quarantined"] and not verdict["quarantined"]:
            verdict["quarantined"] = True
            verdict["reason"] = ring["reason"]
        return verdict
    except Exception as err:
        verdict["quarantined"] = True
        verdict["reason"] = "quarantine check error (fail closed): %s" % err
        return verdict
