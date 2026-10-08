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

Wired into the payout path in ``a2a_inbound_market.submit_proof()``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sincor2.a2a_inbound import get_fabric
from sincor2 import kya_registry

#: A shared-wallet cluster at or above this size is sybil-shaped: the
#: payout is blocked, not staged.
QUARANTINE_CLUSTER_SIZE = 3


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


def quarantine_check(agent_id: str) -> Dict[str, Any]:
    """Payout-gate verdict for one agent.

    Returns a verdict dict::

        {
            "agent_id": ...,
            "wallet": ...,            # the agent's registered wallet
            "cluster_size": N,         # identities sharing that wallet
            "cluster": [agent_ids...], # the full cluster (sorted)
            "tombstoned_members": [...],
            "flagged": bool,           # cluster_size > 1 — note on receipt
            "quarantined": bool,       # payout must be BLOCKED
            "reason": str | None,
        }

    Quarantine (block) when the cluster has >= QUARANTINE_CLUSTER_SIZE
    identities OR any cluster member is tombstoned. Fail-closed: on any
    internal error the verdict is ``quarantined: True`` with the error
    recorded in ``reason`` — the money path must not pay out blind.
    """
    verdict: Dict[str, Any] = {
        "agent_id": agent_id,
        "wallet": "",
        "cluster_size": 0,
        "cluster": [],
        "tombstoned_members": [],
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
        return verdict
    except Exception as err:
        verdict["quarantined"] = True
        verdict["reason"] = "quarantine check error (fail closed): %s" % err
        return verdict
