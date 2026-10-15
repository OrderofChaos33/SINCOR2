"""W-89 wash-anomaly guard (close -> settle path).

A wash settlement — colluding poster + worker, fabricated ``receipt_hash``,
no real work — must never be booked as genuine economic activity.  Before
this guard, such a settlement could manufacture realized treasury revenue
and strand a real bounty-pool allocation with no signal left behind.

DESIGN — false-positive-first, never bricks settlement
------------------------------------------------------
* The guard is DETECT-ONLY.  It never raises and never blocks ``submit_proof``:
  a flagged settlement still completes and the payout still stages.  What
  changes is *revenue recognition*: a flagged settlement is never booked as
  realized, and the anomaly is stamped on the proof and task for the
  adjudicator, APIs, and future fee rails.
* Internal cohort winners (``origin == "internal"``) are exempt: they are the
  operator's own agents, registered through the protected internal path.
  This gives the activity-engine loop — synthetic by design — a zero
  false-positive baseline, and it cannot be spoofed by an external actor
  (becoming "internal" requires the protected seeding path, and the winner
  — not the poster string — carries the exemption).
* External settlements are scored on corroborated signals.  A bare receipt
  alone never flags (legacy callers are legitimate); it must corroborate
  with at least two of: sole-bidder win, instant settle, pair concentration.
  A poster/worker shared wallet is a smoking gun and flags alone.

SIGNALS
-------
* ``unbound_no_acceptance`` — proof carries a bare ``receipt_hash``
  (``content_bound=False``) and no poster EIP-191 acceptance.  Wash never
  binds a deliverable and never seeks acceptance; but legacy legitimate
  callers are also unbound, so this is necessary but not sufficient.
* ``sole_bidder`` — the winner was the only distinct bidder (plaintext bids
  + sealed commits).  Wash avoids competition; legitimate auctions usually
  attract several bidders.
* ``instant_settle`` — proof submitted within ``INSTANT_SETTLE_MS`` of
  assignment.  Wash does no work; legitimate work takes time.  (The engine
  is exempt via the internal-winner rule, so its fast synthetic settles
  never trip this.)
* ``pair_concentration`` — the (poster, winner) pair has
  ``>= PAIR_CONCENTRATION_THRESHOLD`` wins inside the anti-gaming rolling
  window.  Rotation caps normally prevent this; reaching it means cap
  evasion or a standing collusion pair.
* ``self_deal_wallet`` — poster wallet == winner wallet (both non-empty).
  Paying yourself is wash by definition.

FALSE-POSITIVE ANALYSIS (why legitimate users don't trip this)
--------------------------------------------------------------
* Engine loop: winners are internal -> exempt.  Zero FP by construction.
* Legit external, bound deliverable: ``unbound_no_acceptance`` False ->
  cannot flag without ``self_deal_wallet``.  Zero FP.
* Legit external, legacy bare receipt, competitive bidding, normal timing:
  at most one corroborating signal -> clean.  Zero FP.
* Legit external, bare receipt, sole bidder, settles in <2 min: FLAGGED.
  This is the residual FP surface (urgent single-bidder gig, trivially fast
  work).  Cost of the FP is deliberately small: settlement still completes,
  payout still stages; only the anomaly flag is set and any fee is booked
  projected rather than realized.  The poster can clear a NON-self-deal
  flag at any time with an EIP-191 acceptance over the canonical message
  (``SINCOR-POSTER-ACCEPT\\n{task_id}\\n{proof_id}\\n{receipt_hash}``),
  verified against the poster's registered wallet — two wired paths:
  (1) pass ``poster_acceptance_sig`` with ``submit_proof`` (clears at
  settle time); (2) POST ``/v1/a2a/proofs/clear-wash-flag`` afterwards
  (``clear_wash_anomaly``), which also re-books a quarantined fee as
  realized.  Invalid acceptances keep the flag and are logged loudly;
  clearance never raises.
* ``self_deal_wallet``: no legitimate FP — even "testing" self-payment is
  correctly labeled wash; the flag is informational, not blocking.  A
  self-deal flag can NEVER be cleared by poster acceptance — a colluding
  poster's self-attestation proves nothing (C1).  Both the assess-time
  acceptance path and ``clear_wash_anomaly`` refuse it (the latter with
  ``{"cleared": False, "reason": "self-deal not clearable by poster"}``)
  and the flag stays ``anomalous=True``.

EVASION NOTES (adversarial review, 2026-10-14)
----------------------------------------------
* Shill second bidder: defeats ``sole_bidder`` only; ``instant_settle`` +
  ``pair_concentration`` still corroborate after a few rounds, and the
  shill pays real stake per commit (50% of bounty at zero rep).
* Delayed settle: defeats ``instant_settle``; ``sole_bidder`` +
  ``pair_concentration`` still corroborate.
* Bound junk deliverable: defeats ``unbound_no_acceptance``; the pair
  then relies on ``self_deal_wallet``/``pair_concentration``.  Junk
  binding is also adjudicator-visible via the stored evidence.
* Spoofed engine poster_id: useless — exemption keys on the WINNER's
  internal origin, not the poster string.
* 4x4 wash ring (red-team PLAUSIBLE): ``pair_concentration`` is per-pair;
  a wide ring dilutes it, but ``sole_bidder`` + ``instant_settle`` still
  corroborate on each wash auction, and every identity pays real stake.
  Full ring-evasion needs the W-28 cross-pair anomaly layer (out of scope).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# Settle faster than this after assignment with no work evidence reads as
# "scripted" — the red-team wash PoC submits within seconds.  Tight on
# purpose (30s): the assignment itself expires after ~2 min, so a wider
# window would have no discriminative power.  False positives here only
# cost a flag + projected (not realized) fee booking, never a blocked payout.
INSTANT_SETTLE_MS = 30_000

# (poster, winner) wins inside the anti-gaming rolling window at or above
# this corroborate a wash pair.  The bid-time pair cap is 2/window, so
# reaching 3 means cap evasion or a standing collusion pair.
PAIR_CONCENTRATION_THRESHOLD = 3

# EIP-191 poster-acceptance message.  A poster clears a flag by signing:
#   SINCOR-POSTER-ACCEPT\n{task_id}\n{proof_id}\n{receipt_hash}
POSTER_ACCEPT_DOMAIN = "SINCOR-POSTER-ACCEPT"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_internal_winner(agent: Optional[Mapping[str, Any]]) -> bool:
    """True when the winner is an operator-internal agent (protected path)."""
    if not agent:
        return False
    try:
        from sincor2.registration_velocity import classify_origin
        return classify_origin(agent) == "internal"
    except Exception:
        return str(agent.get("origin") or "").strip().lower() == "internal"


def _distinct_bidders(fabric: Any, task_id: str) -> List[str]:
    """Distinct bidder agent_ids across plaintext bids + sealed commits."""
    bidders = set()
    try:
        for b in (fabric.bids or {}).values():
            if b.get("task_id") == task_id and b.get("agent_id"):
                bidders.add(str(b["agent_id"]))
    except Exception:
        pass
    try:
        prefix = f"{task_id}\x00"
        for key, c in (fabric.commits or {}).items():
            if key.startswith(prefix) and c.get("agent_id"):
                bidders.add(str(c["agent_id"]))
    except Exception:
        pass
    return sorted(bidders)


def _norm_wallet(value: Any) -> str:
    return str(value or "").strip().lower()


def verify_poster_acceptance(task: Mapping[str, Any],
                             proof_id: str,
                             receipt_hash: str,
                             signature: str,
                             fabric: Any = None) -> bool:
    """Verify a poster's EIP-191 acceptance of a settlement.

    Returns True only when the signature recovers to the poster's registered
    wallet.  Never raises: any failure is a False (acceptance unavailable).
    """
    try:
        poster_id = task.get("poster_id")
        if not poster_id or not signature:
            return False
        wallet = ""
        if fabric is not None:
            poster = (fabric.agents or {}).get(str(poster_id)) or {}
            wallet = _norm_wallet(poster.get("wallet"))
        if not wallet:
            return False
        # Lazy import: a2a_inbound_market imports this module at call time.
        from sincor2.a2a_inbound_market import _recover_dispute_signer
        message = "\n".join([
            POSTER_ACCEPT_DOMAIN,
            str(task.get("task_id") or ""),
            str(proof_id or ""),
            str(receipt_hash or ""),
        ])
        signer = _recover_dispute_signer(message, str(signature))
        return _norm_wallet(signer) == wallet
    except Exception:
        return False


def _poster_wallet(task: Mapping[str, Any], fabric: Any) -> str:
    """The poster's registered wallet ("" when unknown)."""
    try:
        poster_id = task.get("poster_id")
        if fabric is not None and poster_id:
            poster = (fabric.agents or {}).get(str(poster_id)) or {}
            return _norm_wallet(poster.get("wallet"))
    except Exception:
        pass
    return ""


def _clearance_record(signals: List[Dict[str, Any]],
                      task: Mapping[str, Any],
                      fabric: Any,
                      now_ms: int) -> Dict[str, Any]:
    """Build the machine-readable wash_history entry for a clearance.

    Captures the original signals plus the clearance record (who cleared,
    when, by what mechanism) so downstream consumers can distinguish
    "flagged-then-poster-accepted" from "never flagged" even though both
    read anomalous=False.
    """
    return {
        "original_signals": [
            {"name": str(s.get("name")), "detail": str(s.get("detail"))}
            for s in (signals or [])
        ],
        "clearance": {
            "cleared_by": str(task.get("poster_id") or ""),
            "cleared_by_wallet": _poster_wallet(task, fabric),
            "cleared_at": int(now_ms),
            "mechanism": "poster_eip191_acceptance",
        },
    }


def assess_settlement(task: Mapping[str, Any],
                      winner_id: str,
                      *,
                      content_bound: bool,
                      receipt_hash: str = "",
                      poster_acceptance_sig: Optional[str] = None,
                      proof_id: str = "",
                      fabric: Any = None,
                      now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Assess one settlement for wash-anomaly.  Never raises.

    Returns a verdict dict (see module docstring).  ``anomalous=True`` means
    "do not book this as genuine realized activity" — it never means "block
    the settlement".
    """
    now_ms = int(now_ms) if now_ms is not None else _now_ms()
    verdict: Dict[str, Any] = {
        "anomalous": False,
        "signals": [],
        "exempt": False,
        "exemption_reason": None,
        "cleared_by_acceptance": False,
        "acceptance_refused": False,
        "acceptance_refused_reason": None,
        # Ternary state: when a flag is cleared by poster acceptance the
        # original signals + clearance record land here, so downstream
        # consumers can distinguish "clean" from "flagged-then-cleared".
        "wash_history": [],
        "assessed_at": now_ms,
    }

    def _signal(name: str, detail: str) -> None:
        verdict["signals"].append({"name": name, "detail": detail})

    try:
        winner_id = str(winner_id or "")
        agent = None
        if fabric is not None:
            try:
                agent = (fabric.agents or {}).get(winner_id)
            except Exception:
                agent = None

        # Exemption: internal cohort winners are the operator's own agents.
        if _is_internal_winner(agent):
            verdict["exempt"] = True
            verdict["exemption_reason"] = "internal winner (operator cohort)"
            return verdict

        task_id = str(task.get("task_id") or "")
        poster_id = task.get("poster_id")

        # Poster acceptance is verified first but APPLIED last.  Signals are
        # always computed, and a self_deal_wallet flag can never be cleared
        # by the poster's own attestation (C1: a colluding poster clearing
        # its own self-deal proves nothing).  Verifying before the signals
        # keeps the (expensive, stateless) signature recovery out of the
        # decision logic; the verdict below decides what the verification
        # is allowed to do.
        accepted = False
        if poster_acceptance_sig:
            accepted = verify_poster_acceptance(
                task, proof_id, receipt_hash, poster_acceptance_sig,
                fabric=fabric)

        # Signal: unbound receipt with no poster acceptance.
        if not content_bound:
            _signal("unbound_no_acceptance",
                    "bare receipt_hash, no deliverable binding, "
                    "no poster EIP-191 acceptance")

        # Signal: winner was the sole bidder.
        bidders: List[str] = []
        if fabric is not None:
            bidders = _distinct_bidders(fabric, task_id)
        if bidders and len(bidders) == 1 and bidders[0] == winner_id:
            _signal("sole_bidder",
                    f"winner {winner_id} was the only bidder")

        # Signal: proof within INSTANT_SETTLE_MS of assignment.
        try:
            assigned_at = int(task.get("assigned_at") or 0)
        except (TypeError, ValueError):
            assigned_at = 0
        if assigned_at and 0 <= now_ms - assigned_at < INSTANT_SETTLE_MS:
            _signal("instant_settle",
                    f"settled {(now_ms - assigned_at) // 1000}s after assignment")

        # Signal: (poster, winner) pair concentration in rolling window.
        try:
            from sincor2 import axm_antigaming as _ag
            if poster_id:
                wins = _ag.antigaming_store().pair_wins_in_window(
                    str(poster_id), winner_id, now_ms)
                if wins >= PAIR_CONCENTRATION_THRESHOLD:
                    _signal("pair_concentration",
                            f"pair ({poster_id},{winner_id}) won {wins}x "
                            f"in rolling window")
        except Exception:
            pass

        # Signal: poster and winner share a wallet (self-deal smoking gun).
        try:
            winner_wallet = _norm_wallet((agent or {}).get("wallet"))
            poster_wallet = ""
            if fabric is not None and poster_id:
                poster = (fabric.agents or {}).get(str(poster_id)) or {}
                poster_wallet = _norm_wallet(poster.get("wallet"))
            if winner_wallet and poster_wallet and winner_wallet == poster_wallet:
                _signal("self_deal_wallet",
                        "poster and winner share payout wallet")
        except Exception:
            pass

        # Decision.  Signal precedence: self-deal first (never clearable),
        # then valid acceptance, then the corroboration rule.
        names = {s["name"] for s in verdict["signals"]}
        corroborating = ({"sole_bidder", "instant_settle",
                           "pair_concentration"} & names)
        if "self_deal_wallet" in names:
            # Self-deal is wash by definition and can NEVER be cleared by
            # the poster's own acceptance: self-attestation of self-dealing
            # proves nothing (C1).  A valid acceptance is refused here;
            # note it so the refusal is audit-visible.
            verdict["anomalous"] = True
            if accepted:
                verdict["acceptance_refused"] = True
                verdict["acceptance_refused_reason"] = (
                    "self-deal not clearable by poster")
        elif accepted:
            # Non-self-deal flag cleared by a valid poster acceptance.
            # Ternary state: anomalous=False, but the wash_history marker
            # retains the original signals + the clearance record (who,
            # when, by what mechanism) so downstream consumers can tell
            # "flagged-then-poster-accepted" apart from "never flagged".
            verdict["cleared_by_acceptance"] = True
            verdict["wash_history"] = [
                _clearance_record(verdict["signals"], task, fabric, now_ms),
            ]
        elif "unbound_no_acceptance" in names and len(corroborating) >= 2:
            verdict["anomalous"] = True
        return verdict
    except Exception as err:  # never brick the caller on assessment failure
        logger.warning("[WASH-GUARD] assessment failed: %s", err)
        verdict["signals"].append({"name": "assessment_error",
                                   "detail": str(err)[:200]})
        return verdict


def clear_wash_anomaly(task_id: str, proof_id: str,
                       poster_acceptance_sig: str) -> Dict[str, Any]:
    """Poster-initiated clearance of a W-89 wash-anomaly flag.

    The poster signs the canonical acceptance message
    (``SINCOR-POSTER-ACCEPT\\n{task_id}\\n{proof_id}\\n{receipt_hash}``) with
    their REGISTERED wallet; the signature is verified via
    :func:`verify_poster_acceptance`.  Binding ``task_id`` (and ``proof_id``)
    in the signed message makes an acceptance unreplayable across tasks.

    On valid acceptance: the flag is cleared (signals kept as history, and
    a wash_history entry records the original signals + who/when/how), and
    a fee previously booked *projected* for this task is re-booked as
    realized with an audit note linking the clearance.  On invalid
    acceptance: the flag stays, loudly logged.  Refusals: a flag whose
    signals include ``self_deal_wallet`` is NEVER clearable by poster
    acceptance (C1 — self-attestation of self-dealing proves nothing);
    clearance is refused with
    ``{"cleared": False, "reason": "self-deal not clearable by poster"}``
    and the flag stays anomalous=True.  Never raises: any failure
    returns ``{"cleared": False, ...}`` — clearance must never brick
    settlement or the API.
    """
    result: Dict[str, Any] = {
        "cleared": False,
        "reason": "",
        "task_id": str(task_id or ""),
        "proof_id": str(proof_id or ""),
    }
    try:
        task_id = str(task_id or "")
        proof_id = str(proof_id or "")
        if not task_id or not proof_id or not poster_acceptance_sig:
            result["reason"] = "missing task_id, proof_id, or signature"
            return result
        from sincor2.a2a_inbound import get_fabric
        fabric = get_fabric()
        task = (fabric.tasks or {}).get(task_id) or {}
        if not task:
            result["reason"] = "unknown task"
            return result
        proof = (fabric.proofs or {}).get(proof_id) or {}
        stamp = task.get("wash_anomaly") or proof.get("wash_anomaly") or {}
        if not stamp.get("anomalous"):
            result["reason"] = "not flagged"
            return result
        if stamp.get("cleared_by_acceptance"):
            result["reason"] = "already cleared"
            return result
        # C1: a self-deal flag can NEVER be cleared by the poster's own
        # acceptance — self-attestation of self-dealing proves nothing.
        # Refuse before verifying the signature so the flag and the stamp
        # stay untouched and anomalous=True.
        names = {s.get("name") for s in (stamp.get("signals") or [])}
        if "self_deal_wallet" in names:
            logger.error(
                "[WASH-GUARD] clearance REFUSED: task=%s proof=%s — "
                "self-deal flags cannot be cleared by poster acceptance; "
                "flag retained",
                task_id, proof_id)
            result["reason"] = "self-deal not clearable by poster"
            return result
        receipt_hash = str(proof.get("receipt_hash")
                           or task.get("receipt_hash") or "")
        ok = verify_poster_acceptance(task, proof_id, receipt_hash,
                                      str(poster_acceptance_sig),
                                      fabric=fabric)
        if not ok:
            logger.error(
                "[WASH-GUARD] clearance REJECTED: task=%s proof=%s — "
                "poster acceptance did not verify against the poster's "
                "registered wallet; flag retained",
                task_id, proof_id)
            result["reason"] = "invalid acceptance"
            return result
        # Valid acceptance: clear the flag, keep signals as history.
        # The idempotency re-check, fee re-booking, and stamp persist all
        # run under fabric.lock so two simultaneous clearances cannot
        # double-book the realized fee.  treasury_inflow takes no fabric
        # lock, so the fabric->treasury ordering is one-directional and
        # deadlock-free.  The (expensive, stateless) signature verification
        # above stays outside the lock.
        now_ms = _now_ms()
        new_stamp = dict(stamp)
        # Ternary state: keep the original signals and append a
        # wash_history entry (original signals + clearance record: who
        # cleared, when, by what mechanism) so downstream consumers can
        # tell "flagged-then-poster-accepted" apart from "never flagged".
        history = list(stamp.get("wash_history") or [])
        history.append(_clearance_record(
            stamp.get("signals") or [], task, fabric, now_ms))
        new_stamp.update({
            "anomalous": False,
            "cleared_by_acceptance": True,
            "cleared_at": now_ms,
            "wash_history": history,
        })
        # Re-book a quarantined (projected) fee as realized, exactly once.
        fee_realized = None
        try:
            with fabric.lock:
                _t = fabric.tasks.get(task_id) or {}
                _cur = _t.get("wash_anomaly") or {}
                if _cur.get("fee_realized_on_clearance"):
                    # A concurrent clearance already re-booked; keep the
                    # history, skip the re-book.
                    new_stamp["fee_realized_on_clearance"] = True
                else:
                    fee_info = _t.get("wash_fee_projected") or {}
                    try:
                        amount = float(fee_info.get("fee_axm") or 0)
                    except (TypeError, ValueError):
                        amount = 0.0
                    if amount > 0:
                        try:
                            from sincor2.treasury_settlement import (
                                record_platform_fee_inflow)
                            # C2 mechanical supersession: the realized
                            # re-booking points at the original projected
                            # event's id, so ledger aggregates exclude the
                            # old row (a "supersedes" note in text is not
                            # enough — nothing reads notes).  Older stamps
                            # without an event_id leave supersedes None;
                            # the aggregate then behaves as before.
                            projected_event_id = (fee_info.get("event_id")
                                                  or None)
                            ev = record_platform_fee_inflow(
                                fee_amount=amount,
                                asset="AXM",
                                source="a2a_settlement",
                                task_id=task_id,
                                note=(f"platform fee task={task_id} [realized on "
                                      f"wash-anomaly clearance by poster EIP-191 "
                                      f"acceptance; supersedes projected booking]"),
                                projected=False,
                                supersedes=projected_event_id,
                            )
                            if ev is not None:
                                new_stamp["fee_realized_on_clearance"] = True
                                fee_realized = amount
                        except Exception as err:
                            logger.warning(
                                "[WASH-GUARD] realized re-booking failed for %s: %s",
                                task_id, err)
                # Persist the cleared stamp on task and proof.
                if _t:
                    _t["wash_anomaly"] = new_stamp
                    _t.pop("wash_fee_projected", None)
                _p = fabric.proofs.get(proof_id)
                if _p is not None:
                    _p["wash_anomaly"] = new_stamp
        except Exception as err:
            logger.warning("[WASH-GUARD] stamp persist failed for %s: %s",
                           task_id, err)
        try:
            from sincor2.a2a_inbound import _save_tasks
            _save_tasks(fabric)
        except Exception:
            pass
        logger.error(
            "[WASH-GUARD] clearance ACCEPTED: task=%s proof=%s flag cleared "
            "by poster acceptance%s",
            task_id, proof_id,
            f", fee {fee_realized} AXM re-booked realized"
            if fee_realized else "")
        result.update({
            "cleared": True,
            "reason": "poster acceptance verified",
            "fee_realized_axm": fee_realized,
        })
        return result
    except Exception as err:  # clearance must never raise
        logger.warning("[WASH-GUARD] clearance failed for %s: %s",
                       task_id, err)
        result["reason"] = "internal error"
        return result
