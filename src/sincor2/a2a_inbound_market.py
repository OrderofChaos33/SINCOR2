"""A2A task auctions, bids, proofs. Mounted from a2a_inbound_ext.mount."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request, stream_with_context

import hmac as _hmac

from sincor2.a2a_inbound import (
    AUCTION_WINDOW_MS,
    HEARTBEAT_TTL_S,
    MAX_OPEN_TASKS,
    MERIT_THRESHOLD_AXM,
    PROBATION_SEEDS,
    SEALED_PROBATION_SEEDS,
    _http_error,
    _now_ms,
    _save_agents,
    get_fabric,
)
from sincor2.a2a_timeouts import assignment_deadline_ms
from sincor2.contract_net import calculate_bid_score, stage_payout

logger = logging.getLogger("sincor.a2a.inbound")

# ---------------------------------------------------------------------------
# Sealed-bid commit/reveal shim.
#
# Ratified auction decisions (docs/ops/AUCTION_SECURITY_DECISIONS.md) call for
# 5-minute commit + 5-minute reveal windows per auction with a permissionless
# timeout. The Solidity CommitRevealAuction is not yet deployed and has no
# Python wiring, so this shim implements the same commit/reveal discipline in
# the Python task flow as a forward-compatible stepping stone.
#
# Commitment scheme — mirrors CommitRevealAuction.reveal EXACTLY:
#     keccak256(abi.encodePacked(bytes32(price), salt, agentIdHash))
# where price is the bid in wei as a 32-byte big-endian integer, salt is a
# 32-byte bidder-chosen nonce, and agentIdHash = keccak256(agent_id).
# A client that computes commitments for this shim can reuse the identical
# preimage when the on-chain contracts go live.
#
# Documented divergences from the contracts:
#  * Task binding is STRUCTURAL, not cryptographic: the contract keys commits
#    by (auctionId, msg.sender) in a mapping; the shim keys fabric.commits by
#    (task_id, agent_id). The commitment itself does not hash the task id.
#  * Selection stays composite-score (score desc, earliest-commit tiebreak)
#    among REVEALED bids. Vickrey (lowest wins, second-lowest funds) lives in
#    the Solidity contracts and is intentionally NOT reimplemented here.
#  * No staking/slashing in the shim: sinc_stake is recorded at registration
#    but not enforced. Stake mechanics arrive with the contract wiring.
# ---------------------------------------------------------------------------
COMMIT_WINDOW_MS = 5 * 60 * 1000
REVEAL_WINDOW_MS = 5 * 60 * 1000
UINT96_MAX = 2**96 - 1

# Dispute authorizations expire quickly to bound replay; adjudicate() is
# idempotent on consumed locks, so in-window replays are harmless.
DISPUTE_MAX_SKEW_MS = 15 * 60 * 1000


def _dispute_message(task_id: str, agent_id: str, upheld: bool,
                     adjudicator: str, expires_at_ms: int) -> str:
    """Canonical EIP-191 message the adjudicator signs for a dispute ruling."""
    return "|".join([
        "SINCOR-DISPUTE",
        task_id,
        agent_id,
        "1" if upheld else "0",
        adjudicator.lower(),
        str(expires_at_ms),
    ])


def _recover_dispute_signer(message: str, signature: str) -> str:
    """Recover the signer address of a dispute authorization (EIP-191)."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except Exception as exc:
        raise ValueError("signature verification unavailable") from exc
    try:
        return str(Account.recover_message(
            encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise ValueError("bad signature") from exc


def _keccak256(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak
        return keccak(data)
    except Exception:
        from sha3 import keccak_256  # type: ignore
        return keccak_256(data).digest()


def sealed_commitment(price_wei: int, salt: bytes, agent_id: str) -> bytes:
    """Compute the sealed-bid commitment.

    Mirrors ``CommitRevealAuction.reveal``: ``keccak256(abi.encodePacked(
    bytes32(price), salt, agentIdHash))``. ``salt`` must be 32 bytes;
    ``agentIdHash`` is ``keccak256(agent_id)`` so the bidder's identity is
    pseudonymous in the commitment itself.
    """
    if price_wei <= 0:
        raise ValueError("price_wei must be positive")
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    agent_id_hash = _keccak256(str(agent_id).encode("utf-8"))
    return _keccak256(int(price_wei).to_bytes(32, "big") + bytes(salt) + agent_id_hash)


def _parse_bytes32_hex(value: Any, field: str) -> bytes:
    raw = str(value or "").strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) != 64:
        raise ValueError(f"{field} must be 32 bytes as hex (64 hex chars)")
    try:
        return bytes.fromhex(raw)
    except ValueError:
        raise ValueError(f"{field} is not valid hex")


def _commit_key(task_id: str, agent_id: str) -> str:
    return f"{task_id}\x00{agent_id}"


def _sealed_agent_checks(fabric: Any, task: Dict[str, Any], agent_id: str) -> Dict[str, Any]:
    """Shared bidder eligibility checks (mirrors place_bid's fail-fast set)."""
    agent = fabric.agents.get(agent_id)
    if not agent:
        raise KeyError("unknown agent")
    ts = _now_ms()
    if ts - int(agent.get("last_heartbeat") or 0) > HEARTBEAT_TTL_S * 1000:
        raise PermissionError("agent heartbeat expired")
    if not (set(agent.get("capability_tags") or []) & set(task.get("tags") or [])):
        raise PermissionError("capability mismatch")
    if task.get("requires_merit") and float(agent.get("reputation") or 0) < 0.15:
        raise PermissionError("merit required")
    return agent


def create_task(skill: str, tags: Optional[List[str]] = None, bounty_axm: float = 1.5,
                sealed: bool = False, poster_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a task. When ``sealed`` is true the task runs a sealed-bid
    commit/reveal auction: per-task 5-minute commit + 5-minute reveal windows
    (ratified in docs/ops/AUCTION_SECURITY_DECISIONS.md) are stamped at
    creation, and only commit/reveal bids are accepted — the legacy plaintext
    ``POST /v1/a2a/bids`` path is rejected for sealed tasks."""
    skill = str(skill or "").strip().lower()
    if not skill:
        raise ValueError("skill is required")
    bounty = float(bounty_axm)
    if bounty <= 0 or bounty > 10000:
        raise ValueError("bounty_axm out of range")
    tag_list = [str(t).lower() for t in (tags or [skill]) if t]
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        open_n = sum(1 for t in fabric.tasks.values() if t.get("state") in ("open", "auction"))
        if open_n >= MAX_OPEN_TASKS:
            raise OverflowError("too many open auctions")
        task_id = "tsk_" + uuid.uuid4().hex[:10]
        task = {
            "task_id": task_id,
            "skill": skill,
            "tags": tag_list,
            "bounty_axm": bounty,
            "requires_merit": bounty >= MERIT_THRESHOLD_AXM,
            "state": "open",
            "created_at": ts,
            "auction_closes_at": None,
            "sealed": bool(sealed),
            "poster_id": str(poster_id) if poster_id else None,
            # Sealed-bid windows are anchored at creation, mirroring the
            # contract's commitDeadline = open + commitW,
            # revealDeadline = open + commitW + revealW.
            "commit_deadline": (ts + COMMIT_WINDOW_MS) if sealed else None,
            "reveal_deadline": (ts + COMMIT_WINDOW_MS + REVEAL_WINDOW_MS) if sealed else None,
            "assigned_to": None,
            "winner_score": None,
            "winning_bid_axm": None,
            "time_est_ms": None,
            "proof_id": None,
            "payout_axm": None,
        }
        fabric.tasks[task_id] = task
        snap = dict(task)
    if sealed:
        _anchor_onchain_auction(fabric, task_id)
        with fabric.lock:
            snap = dict(fabric.tasks[task_id])
    fabric.publish("task.created", tag_list, {"task_id": task_id, "skill": skill, "bounty_axm": bounty, "sealed": bool(sealed), "auction_id": snap.get("auction_id")})
    return snap


def _anchor_onchain_auction(fabric: Any, task_id: str) -> None:
    """Open the onchain auction mirroring a sealed Python task.

    No-op unless AUCTION_ONCHAIN_ANCHOR=1.  Fail-closed: if the open
    transaction fails (or the relayer is misconfigured) the Python task is
    removed and creation raises — a task must not promise onchain
    settlement without the auction existing.
    """
    from sincor2.onchain.auction_relayer import (
        anchor_enabled,
        get_relayer,
    )

    if not anchor_enabled():
        return
    relayer = get_relayer()
    try:
        if relayer is None:
            raise RuntimeError("relayer is not configured")
        opened = relayer.open_auction(task_id)
    except Exception as err:
        with fabric.lock:
            fabric.tasks.pop(task_id, None)
        raise RuntimeError(f"onchain auction open failed: {err}")
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if task is not None:
            task["auction_id"] = opened["auction_id"]
            task["onchain_open_tx"] = opened["tx_hash"]


def close_auction(task_id: str) -> Optional[Dict[str, Any]]:
    expire_stale_assignments()
    fabric = get_fabric()
    ts = _now_ms()
    ghosts: List[str] = []
    losers: List[str] = []
    poster_id: Optional[str] = None
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task or task["state"] not in ("open", "auction"):
            return dict(task) if task else None
        poster_id = task.get("poster_id")
        ghosted = 0
        if task.get("sealed"):
            # Sealed-bid shim: the reveal deadline governs closing. Anyone may
            # trigger close once it passes (permissionless timeout, mirroring
            # the ratified instant-timeout). Only REVEALED bids are eligible;
            # unrevealed commits are dropped (ghosted).
            if ts < int(task.get("reveal_deadline") or 0):
                return dict(task)
            prefix = f"{task_id}\x00"
            ghost_records = [
                c for key, c in fabric.commits.items()
                if key.startswith(prefix) and not c.get("revealed")
            ]
            ghosted = len(ghost_records)
            ghosts = [c["agent_id"] for c in ghost_records]
        else:
            closes_at = task.get("auction_closes_at")
            if closes_at is None or ts < int(closes_at):
                return dict(task)
        cands = [b for b in fabric.bids.values()
                 if b.get("task_id") == task_id and b.get("revealed", True)]
        if not cands:
            task["state"] = "expired"
            task["expired_reason"] = "auction_timeout"
            if task.get("sealed"):
                task["ghosted_commits"] = ghosted
            snap = dict(task)
            tags = list(task.get("tags") or [])
        else:
            winner = sorted(cands, key=lambda b: (-float(b["score"]), int(b["received_at"]), b["bid_id"]))[0]
            losers = [b["agent_id"] for b in cands
                      if b["agent_id"] != winner["agent_id"]]
            task.update({
                "state": "assigned",
                "assigned_to": winner["agent_id"],
                "winner_score": winner["score"],
                "winning_bid_axm": winner["bid_axm"],
                "time_est_ms": winner.get("time_est_ms"),
                "assigned_at": ts,
            })
            if task.get("sealed"):
                task["ghosted_commits"] = ghosted
            snap = dict(task)
            tags = list(task.get("tags") or [])
    # Stake settlement (outside the fabric lock; separate ledger).
    # Ghosting => 100 % slash of the locked stake -> poster re-auction credit.
    # Losers are released; the winner's lock stays until proof settlement.
    if ghosts or losers:
        from sincor2.onchain.stake_ledger import stake_ledger
        ledger = stake_ledger()
        for agent_id in ghosts:
            try:
                ledger.slash_ghost(agent_id, task_id, poster_id=poster_id)
            except Exception as err:  # never brick auction close on accounting
                logger.warning("ghost slash failed for %s: %s", agent_id, err)
        for agent_id in losers:
            try:
                ledger.release(agent_id, task_id)
            except Exception as err:
                logger.warning("stake release failed for %s: %s", agent_id, err)
    if snap["state"] == "assigned":
        fabric.publish("task.assigned", tags, {"task_id": task_id, "assigned_agent": snap["assigned_to"], "bid_axm": snap["winning_bid_axm"]})
        if snap.get("auction_id"):
            _fund_onchain_escrow(fabric, task_id)
    return snap


def _fund_onchain_escrow(fabric: Any, task_id: str) -> None:
    """Poster step: select the onchain Vickrey winner and fund the escrow.

    No-op unless AUCTION_ONCHAIN_FUND=1.  Fail-LOUD, never brick: the Python
    assignment stands regardless; the outcome is recorded on the task for
    operator retry.  When nobody bid onchain (the normal case while the
    Python API is the primary path) this is a graceful no-op marked
    ``python_only``.
    """
    from sincor2.onchain.auction_relayer import fund_enabled, get_relayer

    if not fund_enabled():
        return
    relayer = get_relayer()
    outcome = "failed"
    try:
        if relayer is None:
            raise RuntimeError("relayer is not configured")
        vickrey = relayer.read_vickrey(task_id)
        if vickrey is None:
            outcome = "python_only"  # no onchain reveals; nothing to fund
        else:
            _winner, price_wei = vickrey
            tx_hash = relayer.select_winner_and_fund(task_id, price_wei)
            outcome = tx_hash
    except Exception as err:
        logger.error("onchain escrow funding failed for task %s: %s",
                     task_id, err)
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if task is not None:
            task["onchain_funding"] = outcome


def expire_stale_assignments() -> List[Dict[str, Any]]:
    """Auto-cancel assigned tasks whose execution window has elapsed."""
    fabric = get_fabric()
    ts = _now_ms()
    expired: List[Dict[str, Any]] = []
    with fabric.lock:
        for task in fabric.tasks.values():
            if task.get("state") != "assigned":
                continue
            assigned_at = int(task.get("assigned_at") or 0)
            estimate = int(task.get("time_est_ms") or 0)
            deadline = assignment_deadline_ms(assigned_at, estimate)
            if assigned_at and ts > deadline:
                task["state"] = "expired"
                task["expired_reason"] = "execution_timeout"
                task["expired_at"] = ts
                expired.append(dict(task))
    for snap in expired:
        fabric.publish(
            "task.expired",
            list(snap.get("tags") or []),
            {"task_id": snap["task_id"], "reason": "execution_timeout"},
        )
    return expired


def place_bid(task_id: str, agent_id: str, bid_axm: float, time_est_sec: int) -> Dict[str, Any]:
    """Legacy plaintext bid path.

    DEPRECATED in favor of the sealed-bid commit/reveal flow
    (``POST /v1/a2a/bids/commit`` + ``POST /v1/a2a/bids/reveal``). Kept
    working for pre-shim clients: a plaintext bid is treated as an
    immediately-revealed bid. Rejected for sealed tasks.
    """
    if bid_axm <= 0 or time_est_sec <= 0:
        raise ValueError("bid_axm and estimated_seconds must be positive")
    fabric = get_fabric()
    ts = _now_ms()
    close_auction(task_id)
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if task["state"] not in ("open", "auction"):
            raise RuntimeError("auction closed")
        if task.get("sealed"):
            raise RuntimeError("sealed auction: use commit/reveal, not plaintext bids")
        agent = fabric.agents.get(agent_id)
        if not agent:
            raise KeyError("unknown agent")
        if ts - int(agent.get("last_heartbeat") or 0) > HEARTBEAT_TTL_S * 1000:
            raise PermissionError("agent heartbeat expired")
        if not (set(agent.get("capability_tags") or []) & set(task.get("tags") or [])):
            raise PermissionError("capability mismatch")
        if task.get("requires_merit") and float(agent.get("reputation") or 0) < 0.15:
            raise PermissionError("merit required")
        score = calculate_bid_score(float(bid_axm), int(time_est_sec), float(agent.get("reputation") or 0))
        bid_id = "bid_" + uuid.uuid4().hex[:10]
        bid = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": float(bid_axm),
            "estimated_seconds": int(time_est_sec),
            "time_est_ms": int(time_est_sec) * 1000,
            "reputation": float(agent.get("reputation") or 0),
            "score": score,
            "received_at": ts,
            # Legacy plaintext bids count as immediately revealed so the
            # sealed-bid close path (revealed-only) stays backward compatible.
            "revealed": True,
            "via": "legacy-plaintext",
        }
        fabric.bids[bid_id] = bid
        task["state"] = "auction"
        start = task.get("auction_closes_at") is None
        if start:
            task["auction_closes_at"] = ts + AUCTION_WINDOW_MS
        snap = dict(bid)
        tags = list(task.get("tags") or [])
    fabric.publish("bid.received", tags, snap)
    if start:
        timer = threading.Timer(AUCTION_WINDOW_MS / 1000.0, lambda: close_auction(task_id))
        timer.daemon = True
        timer.start()
    return snap


def commit_bid(task_id: str, agent_id: str, commitment: Any) -> Dict[str, Any]:
    """Publish a sealed-bid commitment (commit phase).

    Mirrors ``CommitRevealAuction.commit``: one commitment per (task, agent),
    rejected after the commit deadline. The commitment is opaque to the
    platform until reveal — the bid price stays hidden.
    """
    commitment_bytes = _parse_bytes32_hex(commitment, "commitment")
    if commitment_bytes == bytes(32):
        raise ValueError("commitment must not be zero")
    fabric = get_fabric()
    ts = _now_ms()
    close_auction(task_id)
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if not task.get("sealed"):
            raise RuntimeError("task is not a sealed auction")
        if task["state"] not in ("open", "auction"):
            raise RuntimeError("auction closed")
        commit_deadline = task.get("commit_deadline")
        if commit_deadline is not None and ts > int(commit_deadline):
            raise PermissionError("commit window closed")
        _sealed_agent_checks(fabric, task, agent_id)
        # Stake enforcement (ratified minStakeBps=5000): the bid value is
        # still sealed, so the commit locks stake against the public bounty.
        # Insufficient stake => the commit itself is rejected.
        from sincor2.onchain.stake_ledger import stake_ledger, InsufficientStake
        bounty_wei = int(round(float(task.get("bounty_axm") or 0) * 1e18))
        try:
            stake_ledger().lock_for_commit(agent_id, task_id, bounty_wei)
        except InsufficientStake as err:
            raise PermissionError(str(err))
        key = _commit_key(task_id, agent_id)
        if key in fabric.commits:
            raise RuntimeError("already committed")
        record = {
            "task_id": task_id,
            "agent_id": agent_id,
            "commitment": "0x" + commitment_bytes.hex(),
            "committed_at": ts,
            "revealed": False,
            "revealed_at": None,
            "price_wei": None,
        }
        fabric.commits[key] = record
        task["state"] = "auction"
        snap = dict(record)
        tags = list(task.get("tags") or [])
    fabric.publish("bid.committed", tags, {"task_id": task_id, "agent_id": agent_id})
    return snap


# Neutral default when a reveal omits estimated_seconds. The time estimate is
# NOT part of the sealed commitment (the contract binds price only), so it is
# just a scheduling hint; the default is deliberately uncompetitive to
# incentivize bidders to state their real estimate.
DEFAULT_REVEAL_TIME_EST_SEC = 3600


def reveal_bid(task_id: str, agent_id: str, bid_axm: float, nonce: Any,
               time_est_sec: int = DEFAULT_REVEAL_TIME_EST_SEC) -> Dict[str, Any]:
    """Reveal a sealed bid (reveal phase).

    Mirrors ``CommitRevealAuction.reveal``: recomputes the commitment from
    (price_wei, salt, agent_id) with a constant-time comparison, enforces the
    reveal window, and rejects double-reveals. A valid reveal materializes a
    normal bid record (``revealed=True``) eligible for ``close_auction``.
    """
    bid_axm = float(bid_axm)
    if bid_axm <= 0:
        raise ValueError("bid_axm must be positive")
    if int(time_est_sec) <= 0:
        raise ValueError("estimated_seconds must be positive")
    salt = _parse_bytes32_hex(nonce, "nonce")
    price_wei = int(round(bid_axm * 1e18))
    if price_wei > UINT96_MAX:
        # Mirror the contract's PriceTooLarge guard: the on-chain selection
        # downcasts to uint96, so an unrepresentable price must be rejected
        # at reveal, not discovered at selection time.
        raise ValueError("price exceeds uint96 max")
    fabric = get_fabric()
    ts = _now_ms()
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if not task.get("sealed"):
            raise RuntimeError("task is not a sealed auction")
        key = _commit_key(task_id, agent_id)
        commit = fabric.commits.get(key)
        if not commit:
            raise KeyError("unknown commitment: commit first")
        if commit.get("revealed"):
            raise RuntimeError("already revealed")
        # Window is checked before task state so a late reveal reports the
        # specific "reveal window closed" error rather than generic
        # "auction closed".
        commit_deadline = task.get("commit_deadline")
        reveal_deadline = task.get("reveal_deadline")
        if commit_deadline is not None and reveal_deadline is not None:
            if ts <= int(commit_deadline):
                raise PermissionError("reveal window not open yet")
            if ts > int(reveal_deadline):
                raise PermissionError("reveal window closed")
        if task["state"] not in ("open", "auction"):
            raise RuntimeError("auction closed")
        expected = sealed_commitment(price_wei, salt, agent_id)
        if not _hmac.compare_digest(expected, bytes.fromhex(commit["commitment"][2:])):
            raise ValueError("commitment mismatch: wrong bid_axm or nonce")
        # Stake top-up: the true bid value is now known.  If bid*50 % exceeds
        # the bounty-based lock, the bidder must cover the difference or the
        # reveal is rejected.
        from sincor2.onchain.stake_ledger import stake_ledger, InsufficientStake
        try:
            stake_ledger().top_up_for_reveal(agent_id, task_id, price_wei)
        except InsufficientStake as err:
            raise PermissionError(str(err))
        agent = _sealed_agent_checks(fabric, task, agent_id)
        score = calculate_bid_score(bid_axm, int(time_est_sec), float(agent.get("reputation") or 0))
        bid_id = "bid_" + uuid.uuid4().hex[:10]
        bid = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": bid_axm,
            "price_wei": price_wei,
            "estimated_seconds": int(time_est_sec),
            "time_est_ms": int(time_est_sec) * 1000,
            "reputation": float(agent.get("reputation") or 0),
            "score": score,
            # Ties break to the earliest COMMIT (not the earliest reveal),
            # mirroring the ratified Vickrey tiebreak.
            "received_at": int(commit["committed_at"]),
            "revealed": True,
            "via": "commit-reveal",
        }
        fabric.bids[bid_id] = bid
        commit["revealed"] = True
        commit["revealed_at"] = ts
        commit["price_wei"] = price_wei
        snap = dict(bid)
        tags = list(task.get("tags") or [])
    fabric.publish("bid.revealed", tags, {"task_id": task_id, "agent_id": agent_id, "bid_id": bid_id})
    return snap


def submit_proof(task_id: str, agent_id: str, receipt_hash: str) -> Dict[str, Any]:
    receipt_hash = str(receipt_hash or "").strip()
    if not receipt_hash.startswith("0x") or len(receipt_hash) < 10:
        raise ValueError("receipt_hash must be 0x-prefixed")
    fabric = get_fabric()
    close_auction(task_id)
    ts = _now_ms()
    with fabric.lock:
        task = fabric.tasks.get(task_id)
        if not task:
            raise KeyError("unknown task")
        if task.get("state") != "assigned":
            raise RuntimeError("task is not assigned")
        if task.get("assigned_to") != agent_id:
            raise PermissionError("only the assigned agent may submit proof")
        agent = fabric.agents.get(agent_id) or {}
        amount = float(task.get("winning_bid_axm") or task.get("bounty_axm") or 0)
        wallet = str(agent.get("wallet") or agent_id)
        proof_id = "prf_" + uuid.uuid4().hex[:10]
        task["state"] = "proof_submitted"
        task["proof_id"] = proof_id
        tags = list(task.get("tags") or [])
    receipt = stage_payout(agent_id=agent_id, wallet=wallet, amount_axm=amount, task_id=task_id, receipt_hash=receipt_hash)
    with fabric.lock:
        task = fabric.tasks.get(task_id) or {}
        task["state"] = "settled" if receipt.get("ok") else "failed"
        task["payout_axm"] = amount
        task["payout_tx"] = receipt.get("staged_tx_digest")
        task["settled_at"] = ts
        if receipt.get("ok") and fabric.agents.get(agent_id):
            ag = fabric.agents[agent_id]
            ag["reputation"] = min(1.0, float(ag.get("reputation") or 0) + 0.2)
            ag["probation"] = float(ag["reputation"]) < 0.15
            ag["requires_merit"] = ag["probation"]
            ag["status"] = "probation" if ag["probation"] else "live"
        proof = {
            "proof_id": proof_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "receipt_hash": receipt_hash,
            "status": "paid" if receipt.get("ok") else "rejected",
            "http_status": 202,
            "submitted_at": ts,
            "settled_at": ts,
            "payout": receipt,
        }
        fabric.proofs[proof_id] = proof
        snap = dict(proof)
    # The winner's stake lock survives the auction; it releases here on
    # successful settlement.  A failed settlement keeps the lock so the
    # adjudicator can rule (quality slash is adjudicator-only).
    if receipt.get("ok"):
        try:
            from sincor2.onchain.stake_ledger import stake_ledger
            stake_ledger().release(agent_id, task_id)
        except Exception as err:
            logger.warning("winner stake release failed for %s: %s", agent_id, err)
    fabric.publish("proof.settled", tags, snap)
    _save_agents(fabric)
    snap["http_status"] = 202
    return snap


def seed_probation_tasks() -> List[Dict[str, Any]]:
    fabric = get_fabric()
    with fabric.lock:
        existing = [t for t in fabric.tasks.values() if not t.get("requires_merit") and t.get("state") in ("open", "auction")]
        if existing:
            return [dict(t) for t in existing]
    seeded = [create_task(skill, tags=[skill], bounty_axm=b) for skill, b in PROBATION_SEEDS]
    seeded += [
        create_task(skill, tags=[skill], bounty_axm=b, sealed=True)
        for skill, b in SEALED_PROBATION_SEEDS
    ]
    return seeded


def attach_market_routes(bp: Blueprint) -> None:
    @bp.post("/v1/a2a/tasks")
    def v1_tasks():
        body = request.get_json(silent=True) or {}
        try:
            task = create_task(str(body.get("skill") or body.get("skill_id") or ""), body.get("tags"), float(body.get("bounty_axm") or 1.5), sealed=bool(body.get("sealed")), poster_id=body.get("poster_id") or body.get("agent_id"))
            return jsonify(task), 201
        except (ValueError, OverflowError) as err:
            return _http_error(str(err), 400)

    @bp.post("/v1/a2a/bids")
    @bp.post("/api/v1/bids")
    def v1_bids():
        body = request.get_json(silent=True) or {}
        time_est = body.get("estimated_seconds")
        if time_est is None and body.get("time_est_ms") is not None:
            time_est = int(float(body["time_est_ms"]) / 1000) or 1
        try:
            bid = place_bid(str(body.get("task_id") or ""), str(body.get("agent_id") or ""), float(body.get("bid_axm") or body.get("bid_amount") or 0), int(time_est or 0))
            return jsonify(bid), 201
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.post("/v1/a2a/bids/commit")
    def v1_bids_commit():
        """Sealed-bid commit phase. Body: {task_id, agent_id, commitment}.

        commitment = keccak256(abi.encodePacked(bytes32(price_wei), salt,
        keccak256(agent_id))) as 0x hex — the exact CommitRevealAuction
        preimage, so clients can reuse it on-chain later.
        """
        body = request.get_json(silent=True) or {}
        try:
            record = commit_bid(str(body.get("task_id") or ""), str(body.get("agent_id") or ""), body.get("commitment"))
            return jsonify(record), 201
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.post("/v1/a2a/bids/reveal")
    def v1_bids_reveal():
        """Sealed-bid reveal phase. Body: {task_id, agent_id, bid_axm, nonce}
        (+ optional estimated_seconds). The commitment is recomputed and
        compared in constant time; only valid reveals become bids."""
        body = request.get_json(silent=True) or {}
        try:
            bid = reveal_bid(
                str(body.get("task_id") or ""),
                str(body.get("agent_id") or ""),
                float(body.get("bid_axm") or 0),
                body.get("nonce"),
                int(body.get("estimated_seconds") or DEFAULT_REVEAL_TIME_EST_SEC),
            )
            return jsonify(bid), 201
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.post("/v1/a2a/tasks/<task_id>/close")
    def v1_tasks_close(task_id):
        """Permissionless auction close (timeout). Anyone may call once the
        reveal deadline (sealed) or auction window (legacy) has passed;
        mirrors the ratified permissionless timeout()."""
        task = close_auction(str(task_id or ""))
        if task is None:
            return _http_error("unknown task", 404)
        return jsonify(task), 200

    @bp.post("/v1/a2a/disputes")
    def v1_disputes():
        """File a quality dispute against the winning agent's locked stake.

        Adjudicator-signed only (secp256k1 — HMAC is demo-only per the
        ratified EIP-712 decision, and slashing is on the money path).

        Body: {task_id, agent_id, upheld, adjudicator_address,
               expires_at_ms, signature}

        The adjudicator EIP-191-signs the canonical message
            SINCOR-DISPUTE|<task_id>|<agent_id>|<1|0>|<adjudicator>|<expires_at_ms>
        The recovered signer must equal SINCOR_ADJUDICATOR_ID (set it to the
        adjudicator's Ethereum address). expires_at_ms bounds replay to a
        15-minute window; replays inside the window are harmless because
        adjudicate() is idempotent on consumed locks.

        upheld=true  -> 50% slash of the winner's locked stake (proceeds
                          become poster re-auction credit).
        upheld=false -> stake released, no slash.
        """
        from sincor2.onchain.stake_ledger import (
            ADJUDICATOR_ENV, stake_ledger,
        )
        body = request.get_json(silent=True) or {}
        task_id = str(body.get("task_id") or "")
        agent_id = str(body.get("agent_id") or "")
        upheld = body.get("upheld")
        adjudicator = str(body.get("adjudicator_address") or "")
        signature = str(body.get("signature") or "")
        if (not task_id or not agent_id or upheld is None
                or not adjudicator or not signature
                or body.get("expires_at_ms") is None):
            return _http_error(
                "task_id, agent_id, upheld, adjudicator_address, "
                "expires_at_ms, signature required", 400)
        try:
            expires_at_ms = int(body.get("expires_at_ms"))
        except (TypeError, ValueError):
            return _http_error("expires_at_ms must be an integer", 400)
        now_ms = _now_ms()
        if expires_at_ms <= now_ms:
            return _http_error("dispute authorization expired", 403)
        if expires_at_ms - now_ms > DISPUTE_MAX_SKEW_MS:
            return _http_error("expires_at_ms too far in the future", 400)
        expected = os.environ.get(ADJUDICATOR_ENV, "").strip()
        if not expected:
            return _http_error("adjudicator not configured", 503)
        message = _dispute_message(task_id, agent_id, bool(upheld),
                                   adjudicator, expires_at_ms)
        try:
            signer = _recover_dispute_signer(message, signature)
        except Exception:  # noqa: BLE001 - any recovery failure rejects
            return _http_error("bad signature", 403)
        if signer.lower() != expected.lower() \
                or adjudicator.lower() != expected.lower():
            # Never reveal which of the two checks failed.
            return _http_error("not the adjudicator", 403)
        if get_fabric().tasks.get(task_id) is None:
            return _http_error("unknown task", 404)
        try:
            result = stake_ledger().adjudicate(
                agent_id, task_id, bool(upheld), adjudicator=expected)
            return jsonify(result), 200
        except PermissionError as err:
            return _http_error(str(err), 403)
        except (KeyError, ValueError) as err:
            return _http_error(str(err), 400)

    @bp.post("/v1/a2a/proofs")
    @bp.post("/api/v1/proofs")
    def v1_proofs():
        body = request.get_json(silent=True) or {}
        try:
            proof = submit_proof(str(body.get("task_id") or ""), str(body.get("agent_id") or ""), str(body.get("receipt_hash") or ""))
            return jsonify(proof), 202
        except ValueError as err:
            return _http_error(str(err), 400)
        except KeyError as err:
            return _http_error(str(err), 404)
        except PermissionError as err:
            return _http_error(str(err), 403)
        except RuntimeError as err:
            return _http_error(str(err), 409)

    @bp.get("/v1/a2a/registration-velocity")
    def v1_registration_velocity():
        """External-agent registration velocity (operating directive).

        Machine-readable ranking input: totals, external/internal split,
        per-day series, and the volume-over-vanity TOA objective weights
        driven by measured external registrations/day.
        """
        from sincor2.registration_velocity import (
            velocity_report,
            volume_over_vanity_weights,
        )
        fabric = get_fabric()
        with fabric.lock:
            agents = {aid: dict(a) for aid, a in fabric.agents.items()}
        report = velocity_report(agents)
        report["toa_objective_weights"] = volume_over_vanity_weights(report)
        return jsonify(report), 200

    @bp.get("/v1/a2a/stream")
    @bp.get("/api/v1/stream")
    def v1_stream():
        wanted = {t.strip().lower() for t in (request.args.get("tags") or "").split(",") if t.strip()}

        def _gen():
            fabric = get_fabric()
            last = 0
            idle = 0
            yield ": inbound stream\n\n"
            while idle < 120:
                batch = []
                with fabric.lock:
                    for ev in fabric.events:
                        if ev["seq"] > last:
                            tags = {x.lower() for x in ev.get("tags") or []}
                            if not wanted or wanted.intersection(tags):
                                batch.append(ev)
                            last = max(last, ev["seq"])
                if batch:
                    idle = 0
                    for ev in batch:
                        yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                else:
                    idle += 1
                    yield ": keepalive\n\n"
                time.sleep(2)

        return Response(
            stream_with_context(_gen()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )
