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

#: Minimum ledger reputation_score (R_i) for a poster to unlock
#: ledger-pool backing of its tasks (W-81 finding 1). R_i is the stake
#: ledger's verified execution reputation —
#: min(1,D/50) x min(1,P/3) x min(1,tenure/90d) x 0.5^U — deliberately
#: separate from the marketplace display ``reputation`` scalar, which
#: dust self-deals inflate to 1.0 for ~zero cost (reviewer-executed).
#: R_i is tenure-gated: a dust-farmed identity scores ~0 no matter how
#: many dust settlements it manufactures today. Tunable via
#: ``SYBIL_POOL_POSTER_REPUTATION``.
SYBIL_POOL_POSTER_REPUTATION = 0.05


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "")
        val = int(raw) if str(raw).strip() else default
    except (TypeError, ValueError):
        return default
    # Fail closed: a below-minimum value (0, negative) falls back to the
    # default instead of clamping to a neutered 1. A 1ms burst window or
    # a cohort-size-1 ring layer would silently disable the control —
    # the old max(1, ...) did exactly that for negatives.
    return val if val >= 1 else default


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "")
        val = float(raw) if str(raw).strip() else default
    except (TypeError, ValueError):
        return default
    # Fail closed: a reputation threshold <= 0 makes the newborn check
    # vacuous (nothing is < 0.0), silently disabling the ring layer.
    return val if val > 0 else default


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


def sybil_pool_poster_reputation() -> float:
    """Minimum ledger R_i for pool backing; env
    ``SYBIL_POOL_POSTER_REPUTATION`` overrides."""
    return _env_float("SYBIL_POOL_POSTER_REPUTATION", SYBIL_POOL_POSTER_REPUTATION)


#: Maximum age of a sybil hold before it auto-rejects (W-81): a held
#: winner's stake must not lock indefinitely with no SLA. Past the TTL
#: with no admin decision, the hold auto-rejects (fail closed — money
#: never moves without a human). The rejection preserves the proof
#: record for adjudication, exactly like a manual reject. Tunable via
#: ``SYBIL_HOLD_TTL_MS``.
SYBIL_HOLD_TTL_MS = 72 * 3_600_000


def sybil_hold_ttl_ms() -> int:
    """Hold auto-reject TTL; env ``SYBIL_HOLD_TTL_MS`` overrides."""
    return _env_int("SYBIL_HOLD_TTL_MS", SYBIL_HOLD_TTL_MS)


def _resolve_poster_records(poster_id: str) -> List["tuple[str, Dict[str, Any]]"]:
    """Agent records attributable to a task poster_id.

    poster_id is an agent_id for internal/engine posts, but a signer
    WALLET (lowercased hex) for external server-bound posts
    (``_server_bound_poster_id`` returns the recovered signer, not an
    agent id). Resolve both: exact agent_id match first, then wallet
    match. Returns (agent_id, record) pairs; empty when unattributable.
    """
    fabric = get_fabric()
    pid = str(poster_id or "").strip()
    if not pid:
        return []
    with fabric.lock:
        direct = fabric.agents.get(pid)
        if direct is not None:
            return [(pid, direct)]
        lowered = pid.lower()
        return [
            (aid, a) for aid, a in fabric.agents.items()
            if str(a.get("wallet") or "").strip().lower() == lowered
        ]


def poster_pool_eligible(poster_id: Any) -> "tuple[bool, str]":
    """W-81 finding 1: gate ledger-pool backing on poster standing.

    The 20,000 AXM launch pool subsidizes task bounties. The reviewer
    EXECUTED the profit path: dust-farm a poster to marketplace rep 1.0
    (five 0.000001-AXM self-deals, ~zero cost), post a large pool-backed
    bounty, win it with a newborn burst, extract 0.95x from the pool.
    The marketplace display scalar cannot gate this — it is exactly
    what the dust-farming manufactures. So the gate uses the stake
    ledger's verified execution reputation R_i (tenure-gated: a
    dust-farmed identity scores ~0 regardless of manufactured
    settlements), OR internal origin:

    - internal origin -> eligible (engine exemption; origin is
      server-stamped at registration and external origin-spoof attempts
      stay external).
    - R_i >= ``sybil_pool_poster_reputation()`` -> eligible.
    - otherwise -> NOT eligible: the task still posts, self-funded.
      Without pool backing the self-deal loop loses the 5% platform fee
      per cycle — unprofitable by construction.

    Fail-closed on the subsidy, never on task creation: an anonymous
    poster, an unattributable poster, or a ledger read failure all post
    self-funded. Returns (eligible, reason).
    """
    pid = str(poster_id or "").strip()
    if not pid:
        return False, "anonymous poster has no standing; task posts self-funded"
    records = _resolve_poster_records(pid)
    if not records:
        return False, (
            "poster %s has no registered record; task posts self-funded" % pid)
    if any(str(rec.get("origin") or "") == "internal" for _, rec in records):
        return True, "internal-origin poster (engine exemption)"
    try:
        from sincor2.onchain.stake_ledger import stake_ledger
        ledger = stake_ledger()
    except Exception as err:
        return False, (
            "reputation ledger unavailable (%s); task posts self-funded" % err)
    best = 0.0
    for aid, _rec in records:
        try:
            best = max(best, float(ledger.reputation_score(aid)))
        except Exception:
            continue
    threshold = sybil_pool_poster_reputation()
    if best >= threshold:
        return True, "poster R_i %.4f >= %.4f" % (best, threshold)
    return False, (
        "poster R_i %.4f < %.4f; task posts self-funded" % (best, threshold))


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

    CHEAPEST REMAINING EVASIONS (honest residual analysis, corrected
    after the round-3 review):

    The round-2 residual claimed poster-standing farming cost "five
    genuine settlements" and priced the attack at real work. That was
    materially false: the reviewer EXECUTED five dust-bounty
    (0.000001 AXM) self-deals between attacker identities and reached
    marketplace rep 1.0 at ~zero cost (5% fee on dust), dodging pair
    caps with 3 posters + loss alternation. The marketplace display
    scalar is manufacturable and cannot gate anything of value. What
    actually stops the extraction is the pool-backing gate
    (``poster_pool_eligible``): it prices standing in the stake
    ledger's R_i, which is tenure-gated, so a dust-farmed identity
    scores ~0 no matter how many dust settlements it manufactures.

    1. **Dust-farm poster to 1.0 (reviewer-executed)** — cost ~zero,
       ~minutes. Result: the ring still flags the extraction shape,
       but pool backing is DENIED (R_i ~0 < 0.05). The task posts
       self-funded, and the self-deal loop loses the 5% platform fee
       per cycle — unprofitable by construction. The attack's profit
       is removed even though its mechanics still run.
    2. **Unlock pool backing honestly-ish** — reach R_i >= 0.05, e.g.
       50 settlements across 3+ distinct posters plus ~4.5 days of
       tenure. Cost: 50 full auction cycles (commit/reveal/close/
       settle each), days of wall-clock, and a complete wash trail in
       the ledger (every completion records task, poster, and
       timestamp). Then pool-backed extraction is possible — but the
       ring layer still flags every newborn-burst win and W-89 watches
       the pair pattern. Expensive, slow, and loud, versus the
       original free, instant, and silent.
    3. **Slow-roll registration** — one identity per window-plus-epsilon
       dodges the burst tripwire. Cost scales LINEARLY with fleet size
       (N x ~61 min wall-clock); identities remain visible to
       registration-velocity metrics. Still no pool backing without
       tenure (see 2).
    4. **Reputation exactly at threshold** — ``rep < threshold`` is
       strict; rep == 1.0 escapes "newborn". The 1.0 line is five
       dispute-free settlements either way; and it no longer unlocks
       the pool (R_i gates that separately).

    Closing (2) further needs poster-side stake. That is a product
    decision beyond the W-81 gate, which now prices the previously-free
    attack at 50+ settlements plus days of tenure, or accepts a 5%
    per-cycle loss.
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
