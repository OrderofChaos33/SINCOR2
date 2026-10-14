"""Sybil-economics defenses for the SINCOR2 agent marketplace (2026-10-08).

Red-team finding (CONFIRMED): 5 free registrations/hour/IP with zero
KYA/stake/cost meant one operator could mint a sock-puppet fleet and
self-deal task payouts, and tombstoned wallets could be reborn for free.

This module is the funding-graph layer:

- :func:`cluster_identities_by_wallet` groups live agent identities by
  their registered funding wallet (no graph DB — plain wallet grouping).
- :func:`quarantine_check` is the payout-gate verdict for one agent:
  shared-wallet clusters (>1 identity) are FLAGGED on the payout record,
  payouts are BLOCKED when the cluster has >= 3 identities OR any
  cluster member sits behind a tombstoned wallet, and payouts are HELD
  for manual review when the W-81 ring layer fires on platform-funded
  tasks (see below).
- :func:`_ring_assessment` (W-81) closes the distinct-wallet bypass:
  identities minted in a registration burst that get paid while still
  newborn are ring-shaped — correlated as one operator even though
  every wallet is distinct.

Wired into the payout path in ``a2a_inbound_market.submit_proof()``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from sincor2.a2a_inbound import get_fabric
from sincor2 import kya_registry

#: A shared-wallet cluster at or above this size is sybil-shaped: the
#: payout is blocked, not staged.
QUARANTINE_CLUSTER_SIZE = 3

#: Registration-burst window (W-81): identities whose ``registered_at``
#: fall within this span of each other form a burst cohort. Fresh EOAs
#: are free, so distinct wallets cost the attacker nothing — the burst
#: is the visible signal. 60 minutes is the tripwire default; see
#: :func:`_ring_assessment` for the false-positive tradeoff. Tunable
#: via the ``SYBIL_BURST_WINDOW_MS`` environment variable.
SYBIL_BURST_WINDOW_MS = 3_600_000

#: Burst cohort at or above this size is ring-shaped enough to assess.
#: Tunable via ``SYBIL_RING_COHORT_SIZE``.
SYBIL_RING_COHORT_SIZE = 3

#: Below this reputation an identity counts as "newborn" for ring
#: purposes. One dispute-free settlement earns +0.2, so 1.0 is five
#: settled payouts — the cheapest credible history on the platform.
#: Tunable via ``SYBIL_LOW_REPUTATION``.
SYBIL_LOW_REPUTATION = 1.0

#: Below this reputation a task POSTER counts as "newborn" for ring
#: purposes. The W-81 rework keys the block on the poster's own
#: standing (not on poster-in-cohort timing, which the attacker
#: controls for free): a newborn burst paid by a newborn poster is the
#: atomic sybil extraction. Tunable via
#: ``SYBIL_POSTER_LOW_REPUTATION``.
SYBIL_POSTER_LOW_REPUTATION = 1.0


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def sybil_burst_window_ms() -> int:
    """Burst-cohort window; env ``SYBIL_BURST_WINDOW_MS`` overrides."""
    return _env_int("SYBIL_BURST_WINDOW_MS", SYBIL_BURST_WINDOW_MS)


def sybil_ring_cohort_size() -> int:
    """Minimum burst cohort that trips the ring layer; env override."""
    return _env_int("SYBIL_RING_COHORT_SIZE", SYBIL_RING_COHORT_SIZE)


def sybil_low_reputation() -> float:
    """Winner-newborn threshold; env ``SYBIL_LOW_REPUTATION`` overrides."""
    return _env_float("SYBIL_LOW_REPUTATION", SYBIL_LOW_REPUTATION)


def sybil_poster_low_reputation() -> float:
    """Poster-newborn threshold; env ``SYBIL_POSTER_LOW_REPUTATION``."""
    return _env_float("SYBIL_POSTER_LOW_REPUTATION", SYBIL_POSTER_LOW_REPUTATION)


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
    with fabric.lock:
        wallets = {
            aid: str((fabric.agents.get(aid) or {}).get("wallet") or "").strip().lower()
            for aid in members
        }
    tombstones = kya_registry.tombstones_snapshot().get("tombstones") or []
    tomb_wallets = {str(t.get("wallet") or "").lower() for t in tombstones}
    tomb_agent_ids = {str(t.get("agent_id") or "") for t in tombstones}
    tombstoned: List[str] = []
    for aid in members:
        member_wallet = wallets.get(aid) or ""
        if (member_wallet and member_wallet in tomb_wallets) or aid in tomb_agent_ids:
            tombstoned.append(aid)
    return sorted(tombstoned)


def _burst_cohort(agent_id: str) -> List[str]:
    """Identities registered in a burst alongside the candidate.

    Returns external-origin identities whose ``registered_at`` falls
    within the (env-tunable) burst window of the candidate's own
    ``registered_at`` (sorted). Internal (platform) agents are excluded —
    the engine mints its own cohort and must never trip the ring
    detector. A candidate with no (or a malformed) ``registered_at``
    yields no cohort: that is absence of signal, not an error.
    """
    fabric = get_fabric()
    window_ms = sybil_burst_window_ms()
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
            if ts > 0 and abs(ts - anchor_ms) <= window_ms:
                cohort.append(aid)
    return sorted(cohort)


def _ring_assessment(agent_id: str, task_id: str = "") -> Dict[str, Any]:
    """Distinct-wallet sybil-ring assessment (W-81, reworked).

    The wallet-cluster layer is blind to an operator who mints one fresh
    EOA per identity. This layer correlates what the attacker cannot
    cheaply rotate:

    - **time**: N identities minted in a burst share a registration
      window. Dodging it means aging the fleet (wall-clock cost) or
      trickling registrations (throughput cost).
    - **winner standing**: a newborn (reputation < ``sybil_low_reputation()``)
      has no credible history. Escaping it requires settled payouts,
      which is real work — the desired behavior, not an evasion.
    - **poster standing**: the payout's poster being itself newborn
      (reputation < ``sybil_poster_low_reputation()``) is the atomic
      sybil extraction — a zero-history identity funding a newborn
      fleet. The FIRST W-81 build keyed the block on poster-*in-cohort*
      timing; the reviewer demonstrated live that the attacker shifts
      the poster's registration 61 minutes early for free, so the block
      now keys on the poster's own standing, which costs real
      settlements to fake.

    Verdicts (in escalating order):

    - ``ring_shaped`` — audit-visible flag on the receipt. Set whenever
      a newborn burst winner trips the cohort gate. For an established
      external poster this is the whole response: a real poster paying
      newcomers is legitimate marketplace behavior, and the flag is
      telemetry for audit, NOT a control. It is never presented as
      coverage.
    - ``held`` — payout HELD for manual review (quarantine-until-cleared).
      Set when the poster is the platform itself (internal origin) or
      the poster record is missing: platform-funded tasks are the
      economically meaningful extraction target, and a missing poster
      means standing cannot be assessed (fail closed). The payout is
      NOT staged until an admin releases or rejects the hold.
    - ``quarantined`` — payout BLOCKED. Set only when the burst winner
      is newborn AND the external poster is itself newborn: behaviorally
      the sybil extraction, and no legitimate poster with zero history
      should be funding a newborn fleet.

    FALSE-POSITIVE TRADEOFF (deliberate, stated honestly): a legitimate
    newcomer team whose first payouts are purely internal (their own
    newborn poster paying their newborn burst) IS quarantined —
    behaviorally identical to a sybil ring, so it is treated as one.
    This is no longer a perverse asymmetry: the knowledgeable attacker
    faces the identical gate (poster standing cannot be shifted for
    free). The block is per-payout, never an account ban. Remedies, in
    order of cheapness: (1) the poster builds standing first — five
    dispute-free settlements as a worker (+0.2 each) reaches 1.0, then
    internal payouts flow; (2) winners build standing via open-market
    settlements; (3) stagger registrations outside the burst window.
    The engine's own market-maker cohort is origin-stamped internal and
    never trips the ring detector.

    OPERATIONAL BURDEN (honest): every newborn-burst win on a
    platform-posted task is held for a human decision. During launch,
    legitimate newcomer bursts WILL trip this on their first
    platform-task wins; an operator must review and release or reject
    each hold via the admin sybil-holds endpoints. The hold is
    self-limiting: once an identity reaches 1.0 reputation (five
    settlements) it is no longer newborn and flows without review.

    CHEAPEST REMAINING EVASIONS (honest residual analysis):

    1. **Slow-roll registration** — one identity per window-plus-epsilon
       dodges the burst tripwire entirely. Cost scales LINEARLY with
       fleet size (N x ~61 min wall-clock; ~4 days for 100 identities),
       versus O(1) for a burst. Identities remain visible to
       registration-velocity metrics.
    2. **Poster-standing farming** — the poster earns 1.0 reputation via
       five genuine settlements, then funds unlimited newborn bursts
       (flag-only). Cost: five real tasks with bound deliverables. Each
       subsequent burst still leaves pair/velocity traces for the W-89
       wash-anomaly guard and registration-velocity monitoring.
    3. **Winner-standing farming** — each fleet identity earns 1.0 via
       five genuine settlements. Cost scales linearly (5N settlements):
       doing real work is the desired outcome, not an evasion.
    4. **Reputation exactly at threshold** — ``rep < threshold`` is
       strict; rep == 1.0 escapes "newborn". The 1.0 line is five
       dispute-free settlements either way, so the hatch costs real
       work, not a rounding trick.

    Closing (1) further needs registration rate-limiting or KYA at
    mint; closing (2) further needs poster-side stake. Both are product
    decisions beyond the W-81 gate, which prices the previously-free
    attack at real work or real wall-clock.
    """
    out: Dict[str, Any] = {
        "cohort": [],
        "cohort_size": 0,
        "winner_low_reputation": False,
        "poster_id": None,
        "poster_origin": None,
        "poster_low_reputation": None,
        "ring_shaped": False,
        "held": False,
        "quarantined": False,
        "reason": None,
    }
    cohort = _burst_cohort(agent_id)
    out["cohort"] = cohort
    out["cohort_size"] = len(cohort)
    if len(cohort) < sybil_ring_cohort_size():
        return out
    fabric = get_fabric()
    with fabric.lock:
        me = fabric.agents.get(agent_id) or {}
        try:
            reputation = float(me.get("reputation") or 0.0)
        except (TypeError, ValueError):
            reputation = 0.0
        poster_id = None
        poster = None
        if task_id:
            poster_id = (fabric.tasks.get(task_id) or {}).get("poster_id")
            if poster_id:
                poster = fabric.agents.get(poster_id)
    winner_low = reputation < sybil_low_reputation()
    out["winner_low_reputation"] = winner_low
    out["poster_id"] = poster_id
    if not winner_low:
        # Established winner: no ring signal regardless of poster. An
        # identity that farmed standing did real work; the residual is
        # documented above, not gated here.
        return out
    # Newborn burst winner at/above cohort size: always at least
    # audit-visible.
    out["ring_shaped"] = True
    if not task_id:
        # No payout context: flag for audit, never block or hold —
        # there is no payout to gate.
        out["reason"] = (
            "ring-shaped burst newborn; no task context — flag only")
        return out
    if poster_id is None:
        out["held"] = True
        out["reason"] = (
            "sybil ring: newborn identity (rep %.2f) from a %d-identity "
            "registration burst won a task with no poster record; "
            "standing cannot be assessed — payout held for manual review"
            % (reputation, len(cohort))
        )
        return out
    poster_origin = str((poster or {}).get("origin") or "")
    out["poster_origin"] = poster_origin or None
    if poster is None or not poster_origin:
        # Poster id present but no agent record (or no origin stamp):
        # fail closed — hold, don't guess.
        out["held"] = True
        out["reason"] = (
            "sybil ring: newborn identity (rep %.2f) from a %d-identity "
            "registration burst won task %s whose poster %s has no "
            "assessable standing — payout held for manual review"
            % (reputation, len(cohort), task_id, poster_id)
        )
        return out
    if poster_origin == "internal":
        out["held"] = True
        out["reason"] = (
            "sybil ring: newborn identity (rep %.2f) from a %d-identity "
            "registration burst won platform-posted task %s — payout "
            "held for manual review" % (reputation, len(cohort), task_id)
        )
        return out
    try:
        poster_rep = float(poster.get("reputation") or 0.0)
    except (TypeError, ValueError):
        poster_rep = 0.0
    poster_low = poster_rep < sybil_poster_low_reputation()
    out["poster_low_reputation"] = poster_low
    if poster_low:
        out["quarantined"] = True
        out["reason"] = (
            "sybil ring: newborn identity (rep %.2f) from a %d-identity "
            "registration burst paid by newborn poster %s (rep %.2f); "
            "payout quarantined" % (reputation, len(cohort), poster_id, poster_rep)
        )
    else:
        out["reason"] = (
            "ring-shaped: newborn burst winner paid by established "
            "poster %s (rep %.2f) — legitimate shape, audit flag only"
            % (poster_id, poster_rep)
        )
    return out


def quarantine_check(agent_id: str, task_id: str = "") -> Dict[str, Any]:
    """Payout-gate verdict for one agent.

    ``task_id`` is optional: when the payout's task is known, the W-81
    ring layer also assesses the distinct-wallet sybil shape
    (registration burst + newborn winner + poster standing). Without
    it, only the wallet-cluster layer runs.

    Returns a verdict dict::

        {
            "agent_id": ...,
            "wallet": ...,            # the agent's registered wallet
            "cluster_size": N,         # identities sharing that wallet
            "cluster": [agent_ids...], # the full cluster (sorted)
            "tombstoned_members": [...],
            "ring": {...},             # W-81 ring assessment (see below)
            "flagged": bool,           # note on the payout receipt
            "held": bool,              # payout must be HELD for review
            "quarantined": bool,       # payout must be BLOCKED
            "reason": str | None,
        }

    Quarantine (block) when the cluster has >= QUARANTINE_CLUSTER_SIZE
    identities, OR any cluster member is tombstoned, OR the ring
    assessment finds a newborn burst paid by a newborn poster. Hold
    (manual review, payout not staged) when the ring assessment fires
    on a platform-posted or poster-unknown task. Fail-closed: on any
    internal error the verdict is ``quarantined: True`` with the error
    recorded in ``reason`` — the money path must not pay out blind.
    """
    verdict: Dict[str, Any] = {
        "agent_id": agent_id,
        "wallet": "",
        "cluster_size": 0,
        "cluster": [],
        "tombstoned_members": [],
        "ring": {},
        "flagged": False,
        "held": False,
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
        # cannot cheaply rotate (burst timing, winner standing, poster
        # standing) instead of the wallet alone.
        ring = _ring_assessment(agent_id, task_id)
        verdict["ring"] = ring
        if ring["ring_shaped"]:
            verdict["flagged"] = True
        if ring["held"] and not verdict["quarantined"]:
            verdict["held"] = True
            verdict["reason"] = ring["reason"]
        if ring["quarantined"] and not verdict["quarantined"]:
            verdict["quarantined"] = True
            verdict["reason"] = ring["reason"]
        return verdict
    except Exception as err:
        verdict["quarantined"] = True
        verdict["reason"] = "quarantine check error (fail closed): %s" % err
        return verdict
