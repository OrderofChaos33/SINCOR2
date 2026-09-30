"""Python stake enforcement for the sealed-bid task market.

Ratified parameters (2026-09-25, docs/ops/AUCTION_SECURITY_DECISIONS.md):
  * minStakeBps = 5000 — a bidder must have >= 50 % of the bid value staked.
  * challengerBond = 0.02 ETH — bond required to open a quality dispute.
  * Ghosting (commit without reveal): 100 % slash of the locked stake.
  * Upheld quality failure: 50 % slash of the locked stake.
  * Slashed proceeds become non-withdrawable re-auction credit, EXCEPT the
    platform's unrecouped sponsored front, which is clawed back to the
    platform treasury first (senior creditor). Only the remainder becomes
    poster re-auction credit. This is the subsidy-extraction invariant
    (2026-09-27): platform-fronted capital can never be converted into a
    poster's reusable re-auction balance via sybil ghosting. Clawback is
    not a slash — it is the platform reclaiming its own fronted capital.
    Every clawback SETTLES the sponsored claim as it is collected, so
    repeated slashes can never claw back more in total than was fronted
    and a later earnings recoup cannot double-recover (2026-09-28).
  * Exact stake deposits; adjudicator-only slashing (poster fast-path
    deferred).

Sealed-bid subtlety: the bid VALUE is hidden at commit time, so the commit
locks stake against the public task bounty (``bounty * minStakeBps``).  At
reveal the lock is topped up if ``bid * minStakeBps`` exceeds it; a reveal
that cannot cover its own stake is rejected.

Lifecycle wired in ``a2a_inbound_market``:
  commit_bid    -> lock_for_commit (insufficient stake => commit REJECTED)
  reveal_bid    -> top_up_for_reveal (shortfall => reveal REJECTED)
  close_auction -> losers released; ghosts slashed 100 %; winner stays locked
  submit_proof  -> winner released on settlement + record_completion
  adjudicate()  -> adjudicator-only quality slash (50 %)

Reputation-weighted collateral (2026-09-28):
  B_stake,i = B_base x (1 - 0.8 x R_i).  R_i is built here from dispute-free
  completions (trailing 180d, distinct posters, 90-day tenure gate, 0.5^U
  for upheld disputes); ghosting resets it to zero.  Vickrey selection is
  untouched — reputation buys bidding capacity, never wins.

This is the Python accounting layer.  On-chain stake deposits / slashing
are wired via StakeSlashManager (contracts/StakeSlashManager.sol) through
src/sincor2/onchain/stake_bridge.py — see the "onchain wiring" section at
the bottom of this module.  Until the founder-authorized deployment, the
wiring is dry-run only (eth_call): no funds move.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.market.stake")

# --- Ratified parameters ------------------------------------------------------
MIN_STAKE_BPS = 5000          # 50 % of bid value
BPS_DENOM = 10_000
CHALLENGER_BOND_WEI = int(0.02 * 10**18)   # 0.02 ETH
GHOST_SLASH_BPS = 10_000      # ghosting: 100 %
QUALITY_SLASH_BPS = 5_000     # upheld quality failure: 50 %

# Adjudicator identity.  Single-key today (decentralization is still open);
# the operator sets ADJUDICATOR_AGENT_ID to the adjudicator's agent id.
ADJUDICATOR_ENV = "SINCOR_ADJUDICATOR_ID"

# --- Reputation-weighted collateral (ratified 2026-09-28) -----------------------
# B_stake,i = B_base x (1 - COLLATERAL_ALPHA x R_i),  R_i in [0, 1].
#
# A newborn (R_i = 0) locks 100 % of the base requirement; a proven agent
# (R_i = 1) locks only 20 %.  The discount factor is bid-INDEPENDENT, so it
# scales the capital cost of bidding without touching the Vickrey payment
# rule: selection stays strictly lowest-revealed-bid, and the truthfulness
# argument holds relative to the current design (bidding already carries a
# bid-scaled capital cost today; the discount multiplies it by a constant).
# Reputation buys bidding CAPACITY (5x concurrent bids on the same
# collateral pool), never wins.  That capacity gap is the switching cost:
# leaving SINCOR resets R_i to 0 and the operator must immediately lock 5x
# more capital for the same execution volume.
#
# R_i = min(1, D/50) x min(1, P/3) x min(1, tenure/90d) x 0.5^U, where
#   D = dispute-free completions in the trailing 180 days,
#   P = distinct posters served in the trailing 180 days (wash-trading
#       against your own sock-puppet poster cannot inflate this),
#   tenure = days since first recorded completion (an account cannot be
#       farmed to veteran in an afternoon: farm-then-burn is blunted),
#   U = upheld quality disputes in the trailing 180 days.
# Ghosting clears the whole record (R_i -> 0): the true punishment is not
# the slash, it is the collateral regime change — every concurrent and
# future bid suddenly needs 5x capital.
#
# COLLATERAL_ALPHA is FIXED at launch.  It is not adaptive, not tuned, not
# voted per-epoch — there is deliberately no env override.  Changing it is
# a governance action with the same ceremony as changing MIN_STAKE_BPS.
COLLATERAL_ALPHA = 0.8
# Integer form of the alpha, used for exact wei arithmetic.  Float math on
# 1e18-scale wei silently loses hundreds of wei (float64 has ~9e15 exact
# integers); the discount is therefore computed in basis points throughout.
COLLATERAL_ALPHA_BPS = 8000
REP_WINDOW_DAYS = 180
REP_COMPLETIONS_FOR_FULL = 50
REP_POSTERS_FOR_FULL = 3
REP_TENURE_DAYS_FOR_FULL = 90
_REP_WINDOW_SEC = REP_WINDOW_DAYS * 86400
_REP_MAX_COMPLETIONS = 1000  # bound per-agent history growth


class InsufficientStake(PermissionError):
    pass


class UnauthorizedSlashing(PermissionError):
    pass


def required_stake_wei(bid_value_wei: int) -> int:
    """Minimum stake for a bid value (50 % of bid, ratified)."""
    return int(bid_value_wei) * MIN_STAKE_BPS // BPS_DENOM


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_ledger_path() -> str:
    """Volume-aware default: Railway /data when mounted, else local data dir.

    The old default (~/workspace/ops/stake_ledger.json) lived on Railway's
    ephemeral filesystem, so every redeploy wiped all stake balances.
    """
    explicit = os.environ.get("SINCOR_STAKE_LEDGER_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "stake_ledger.json")
    except Exception:
        return os.path.expanduser("~/workspace/ops/stake_ledger.json")


def _now_ts() -> int:
    return int(time.time())


class StakeLedger:
    """Persistent stake accounting: deposits, per-task locks, slashes,
    challenger bonds, and poster re-auction credits.  Atomic JSON writes."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_ledger_path()
        self._data: Dict[str, Any] = {"agents": {}, "credits": {},
                                      "events": []}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    self._data.update(raw)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("stake ledger load failed (%s); starting empty", exc)

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)

    def _agent(self, agent_id: str) -> Dict[str, Any]:
        agents = self._data["agents"]
        rec = agents.get(agent_id)
        if rec is None:
            rec = {"deposited_wei": "0", "locks": {}, "bonds": {},
                   "slashed_wei": "0"}
            agents[agent_id] = rec
        return rec

    def _event(self, kind: str, **detail: Any) -> None:
        self._data["events"].append({"kind": kind, "at": _now(), **detail})
        # Keep the event log bounded; the ledger is accounting, not a journal.
        if len(self._data["events"]) > 5000:
            self._data["events"] = self._data["events"][-5000:]

    # -- reputation-weighted collateral ------------------------------------
    def _rep(self, agent_id: str) -> Dict[str, Any]:
        """Lazy per-agent reputation record for collateral scoring.

        This is the LEDGER's collateral score input.  It is deliberately
        separate from the marketplace agent card's display ``reputation``
        scalar: that scalar prices bids, this one prices capital.
        """
        rec = self._agent(agent_id)
        rep = rec.get("rep")
        if not isinstance(rep, dict):
            rep = {"completions": [], "upheld": [], "first_seen": None}
            rec["rep"] = rep
        rep.setdefault("completions", [])
        rep.setdefault("upheld", [])
        rep.setdefault("first_seen", None)
        return rep

    @staticmethod
    def _prune_rep(rep: Dict[str, Any], now: int) -> None:
        cutoff = now - _REP_WINDOW_SEC
        rep["completions"] = [c for c in rep["completions"]
                              if isinstance(c, dict)
                              and int(c.get("ts", 0)) >= cutoff]
        rep["upheld"] = [ts for ts in rep["upheld"] if int(ts) >= cutoff]
        # Bound history growth; only the trailing window matters.
        if len(rep["completions"]) > _REP_MAX_COMPLETIONS:
            rep["completions"] = rep["completions"][-_REP_MAX_COMPLETIONS:]

    def reputation_score(self, agent_id: str, now: Optional[int] = None
                         ) -> float:
        """R_i in [0, 1]: the agent's verified execution reputation.

        min(1, D/50) x min(1, P/3) x min(1, tenure/90d) x 0.5^U over the
        trailing 180-day window.  Pure read (prunes stale entries)."""
        now = int(now) if now is not None else _now_ts()
        rep = self._rep(agent_id)
        self._prune_rep(rep, now)
        completions = rep["completions"]
        d = len(completions)
        posters = {str(c.get("poster")) for c in completions
                   if c.get("poster")}
        p = len(posters)
        u = len(rep["upheld"])
        first_seen = rep.get("first_seen")
        tenure_days = max(0.0, (now - int(first_seen)) / 86400.0) \
            if first_seen else 0.0
        r = (min(1.0, d / REP_COMPLETIONS_FOR_FULL)
             * min(1.0, p / REP_POSTERS_FOR_FULL)
             * min(1.0, tenure_days / REP_TENURE_DAYS_FOR_FULL)
             * (0.5 ** u))
        return max(0.0, min(1.0, r))

    def _discounted(self, base_wei: int, agent_id: str) -> tuple[int, float]:
        # Exact integer math: keep_bps = 10000 - 8000*R_bps/10000, so a
        # veteran (R=1) keeps exactly base*2000//10000 = base//5 wei.
        r = self.reputation_score(agent_id)
        r_bps = int(round(r * BPS_DENOM))
        keep_bps = BPS_DENOM - (COLLATERAL_ALPHA_BPS * r_bps) // BPS_DENOM
        return int(base_wei) * keep_bps // BPS_DENOM, r

    def stake_required_wei(self, bid_value_wei: int, agent_id: str) -> int:
        """Reputation-weighted stake requirement for a bid value.

        B_stake,i = B_base x (1 - 0.8 x R_i).  Newborns lock the full base
        requirement; proven agents lock as little as 20 %.  This is the
        amount fronting paths (sponsored stake, recovery track) should size
        against — a given front buys a veteran 5x the bidding capacity.
        """
        need, _ = self._discounted(required_stake_wei(int(bid_value_wei)),
                                  agent_id)
        return need

    def record_completion(self, agent_id: str, task_id: str,
                          poster_id: Optional[str] = None,
                          at: Optional[int] = None) -> float:
        """Record a dispute-free execution (settlement, or a dispute the
        adjudicator did NOT uphold).  Idempotent on (agent, task): a
        retried settlement cannot double-count.  Returns the new R_i."""
        now = int(at) if at is not None else _now_ts()
        rep = self._rep(agent_id)
        self._prune_rep(rep, now)
        if not any(c.get("task_id") == task_id for c in rep["completions"]):
            rep["completions"].append({
                "task_id": str(task_id),
                "poster": str(poster_id) if poster_id else None,
                "ts": now,
            })
            if rep.get("first_seen") is None:
                rep["first_seen"] = now
            self._event("rep_completion", agent_id=agent_id, task_id=task_id,
                        poster_id=str(poster_id) if poster_id else None)
            self._save()
        return self.reputation_score(agent_id, now=now)

    def _record_upheld_dispute(self, agent_id: str) -> None:
        rep = self._rep(agent_id)
        now = _now_ts()
        self._prune_rep(rep, now)
        rep["upheld"].append(now)
        self._event("rep_upheld_dispute", agent_id=agent_id)
        self._save()

    def _reset_reputation_score(self, agent_id: str) -> None:
        """Ghosting: clear the execution record.  R_i -> 0 immediately —
        the collateral regime change is the real punishment."""
        rec = self._agent(agent_id)
        rec["rep"] = {"completions": [], "upheld": [], "first_seen": None}
        self._event("rep_reset", agent_id=agent_id, reason="ghosting")
        self._save()

    # -- deposits -----------------------------------------------------------
    def deposit(self, agent_id: str, amount_wei: int,
                reference: Optional[str] = None) -> Dict[str, Any]:
        """Record an exact stake deposit (on-chain movement is separate).

        ``reference`` is an optional opaque external reference (e.g. the
        0x tx hash of an on-chain AXM transfer) stored on the deposit
        event for future reconciliation.  The ledger itself is offchain
        accounting; a reference never moves funds.
        """
        if amount_wei <= 0:
            raise ValueError("deposit must be positive")
        rec = self._agent(agent_id)
        rec["deposited_wei"] = str(int(rec["deposited_wei"]) + int(amount_wei))
        detail: Dict[str, Any] = {"agent_id": agent_id,
                                  "amount_wei": str(amount_wei)}
        if reference:
            detail["reference"] = str(reference)
        self._event("deposit", **detail)
        self._save()
        return self.balance_of(agent_id)

    def balance_of(self, agent_id: str) -> Dict[str, Any]:
        rec = self._agent(agent_id)
        deposited = int(rec["deposited_wei"])
        locked = sum(int(v) for v in rec["locks"].values())
        bonded = sum(int(v) for v in rec["bonds"].values())
        score = self.reputation_score(agent_id)
        return {"agent_id": agent_id, "deposited_wei": str(deposited),
                "locked_wei": str(locked), "bonded_wei": str(bonded),
                "available_wei": str(deposited - locked - bonded),
                "slashed_wei": rec["slashed_wei"],
                "reputation_score": f"{score:.4f}",
                "stake_discount_bps": str(int(COLLATERAL_ALPHA * score
                                             * BPS_DENOM))}

    # -- commit / reveal locks ------------------------------------------------
    def lock_for_commit(self, agent_id: str, task_id: str,
                        bounty_wei: int) -> int:
        """Lock stake at commit.  The requirement is reputation-weighted:
        B_stake,i = bounty*50 % x (1 - 0.8 x R_i).  Raises InsufficientStake
        => the commit itself must be rejected."""
        base = required_stake_wei(bounty_wei)
        need, score = self._discounted(base, agent_id)
        bal = self.balance_of(agent_id)
        if int(bal["available_wei"]) < need:
            raise InsufficientStake(
                f"agent {agent_id} needs {need} wei staked for this auction "
                f"(has {bal['available_wei']} available)")
        rec = self._agent(agent_id)
        rec["locks"][task_id] = str(int(rec["locks"].get(task_id, "0")) + need)
        self._event("lock", agent_id=agent_id, task_id=task_id,
                    amount_wei=str(need), basis="bounty_at_commit",
                    base_stake_wei=str(base),
                    reputation_score=f"{score:.4f}")
        self._save()
        return need

    def top_up_for_reveal(self, agent_id: str, task_id: str,
                          bid_wei: int) -> int:
        """After reveal the true bid value is known; top the lock up to the
        reputation-weighted bid*50 %.  Raises InsufficientStake => the
        reveal must be rejected."""
        base = required_stake_wei(bid_wei)
        need, score = self._discounted(base, agent_id)
        rec = self._agent(agent_id)
        locked = int(rec["locks"].get(task_id, "0"))
        if locked >= need:
            return 0
        extra = need - locked
        if int(self.balance_of(agent_id)["available_wei"]) < extra:
            raise InsufficientStake(
                f"agent {agent_id} cannot cover stake for revealed bid "
                f"(needs {need}, locked {locked})")
        rec["locks"][task_id] = str(need)
        self._event("top_up", agent_id=agent_id, task_id=task_id,
                    amount_wei=str(extra), basis="bid_at_reveal",
                    base_stake_wei=str(base),
                    reputation_score=f"{score:.4f}")
        self._save()
        return extra

    def release(self, agent_id: str, task_id: str) -> int:
        """Unlock a task lock (losers at close, winner at settlement)."""
        rec = self._agent(agent_id)
        amount = int(rec["locks"].pop(task_id, "0"))
        if amount:
            self._event("release", agent_id=agent_id, task_id=task_id,
                        amount_wei=str(amount))
            self._save()
        return amount

    # -- slashing (adjudicator only) -------------------------------------------
    def _check_adjudicator(self, adjudicator: Optional[str]) -> str:
        expected = os.environ.get(ADJUDICATOR_ENV, "").strip()
        caller = str(adjudicator or "").strip()
        if not expected or caller != expected:
            raise UnauthorizedSlashing(
                "slashing is adjudicator-only; poster fast-path is deferred")
        return caller

    def _apply_sponsored_clawback(self, agent_id: str, slashed_wei: int,
                                  task_id: str) -> int:
        """Settle the platform's senior claim against slashed proceeds.

        One call both caps the clawback at the outstanding front AND
        records it in the sponsored ledger (shrinking the claim), so
        repeated slashes can never cumulatively claw back more than the
        platform fronted, and a later earnings-recoup cannot
        double-recover.  Returns the amount actually applied.

        Fail-open with a loud log: if the sponsored ledger cannot be
        written, auction close must not break (liveness outranks the narrow
        race — an attacker cannot cause this write to fail remotely, it is
        a local file). The invariant is enforced on the normal path.
        """
        try:
            from sincor2.sponsored_stake import sponsored_ledger
            return int(sponsored_ledger().apply_clawback(
                agent_id, slashed_wei, task_id))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("clawback: sponsored apply failed for %s "
                         "(%s); treating as zero", agent_id, exc)
            return 0

    def _split_slash(self, agent_id: str, slashed_wei: int, task_id: str
                     ) -> tuple[int, int]:
        """Split slashed proceeds into (clawback_wei, poster_credit_wei).

        Subsidy-extraction invariant: the platform's unrecouped fronted
        capital is senior. It is clawed back to the platform treasury
        first — settling the sponsored claim as it is collected — and only
        the remainder becomes poster re-auction credit. This makes sybil
        ghost-farming unprofitable: self-funded stake can become
        re-auction credit, sponsored money always returns home, and never
        more than once.
        """
        slashed_wei = int(slashed_wei)
        if slashed_wei <= 0:
            return 0, 0
        clawback = self._apply_sponsored_clawback(agent_id, slashed_wei,
                                                 task_id)
        return clawback, slashed_wei - clawback

    def slash(self, agent_id: str, task_id: str, slash_bps: int, reason: str,
              adjudicator: Optional[str] = None,
              poster_id: Optional[str] = None) -> Dict[str, Any]:
        """Slash ``slash_bps`` of the agent's locked stake for ``task_id``.

        Proceeds split by the subsidy-extraction invariant: the platform's
        unrecouped sponsored front is clawed back to the treasury first;
        only the remainder becomes poster re-auction credit."""
        caller = self._check_adjudicator(adjudicator)
        rec = self._agent(agent_id)
        locked = int(rec["locks"].pop(task_id, "0"))
        slashed = locked * slash_bps // BPS_DENOM
        remainder = locked - slashed
        rec["deposited_wei"] = str(int(rec["deposited_wei"]) - slashed)
        rec["slashed_wei"] = str(int(rec["slashed_wei"]) + slashed)
        if remainder:
            # Unslashed remainder is freed (not re-locked).
            pass
        if reason == "quality_failure_upheld":
            # Reputation-weighted collateral: an upheld dispute halves the
            # execution record's weight (0.5^U).  The slash takes the locked
            # stake; the R_i hit raises every future lock toward full.
            self._record_upheld_dispute(agent_id)
        credit_to = poster_id or "__platform__"
        clawback_wei, poster_wei = self._split_slash(agent_id, slashed,
                                                     task_id)
        if clawback_wei:
            self.credit_reauction("__platform__", clawback_wei,
                                  f"clawback:sponsored:{task_id}")
        if poster_wei:
            self.credit_reauction(credit_to, poster_wei,
                                  f"slash:{reason}:{task_id}")
        self._event("slash", agent_id=agent_id, task_id=task_id,
                    slash_bps=slash_bps, slashed_wei=str(slashed),
                    reason=reason, adjudicator=caller,
                    clawback_wei=str(clawback_wei),
                    poster_credit_wei=str(poster_wei),
                    credited_to=credit_to)
        self._save()
        return {"agent_id": agent_id, "task_id": task_id,
                "slashed_wei": str(slashed), "reason": reason,
                "clawback_wei": str(clawback_wei),
                "poster_credit_wei": str(poster_wei),
                "reauction_credited_to": credit_to}

    def slash_ghost(self, agent_id: str, task_id: str,
                    poster_id: Optional[str] = None) -> Dict[str, Any]:
        """Ghosting: committed but never revealed => 100 % slash.
        Called by close_auction (system path); the adjudicator gate is
        satisfied by the protocol rule itself.

        Subsidy-extraction invariant: the platform's unrecouped sponsored
        front is clawed back to the treasury first; only the remainder
        becomes poster re-auction credit.

        Collateral regime change: ghosting clears the agent's execution
        record (R_i -> 0), so every concurrent and future bid immediately
        requires up to 5x the collateral.  The slash takes the stake; the
        reset takes the capital efficiency."""
        self._reset_reputation_score(agent_id)
        rec = self._agent(agent_id)
        locked = int(rec["locks"].pop(task_id, "0"))
        if locked <= 0:
            return {"agent_id": agent_id, "task_id": task_id,
                    "slashed_wei": "0", "reason": "ghosting_no_lock"}
        rec["deposited_wei"] = str(int(rec["deposited_wei"]) - locked)
        rec["slashed_wei"] = str(int(rec["slashed_wei"]) + locked)
        credit_to = poster_id or "__platform__"
        clawback_wei, poster_wei = self._split_slash(agent_id, locked,
                                                     task_id)
        if clawback_wei:
            self.credit_reauction("__platform__", clawback_wei,
                                  f"clawback:sponsored:ghosting:{task_id}")
        if poster_wei:
            self.credit_reauction(credit_to, poster_wei,
                                  f"slash:ghosting:{task_id}")
        self._event("slash", agent_id=agent_id, task_id=task_id,
                    slash_bps=GHOST_SLASH_BPS, slashed_wei=str(locked),
                    reason="ghosting", adjudicator="protocol",
                    clawback_wei=str(clawback_wei),
                    poster_credit_wei=str(poster_wei),
                    credited_to=credit_to)
        self._save()
        return {"agent_id": agent_id, "task_id": task_id,
                "slashed_wei": str(locked), "reason": "ghosting",
                "clawback_wei": str(clawback_wei),
                "poster_credit_wei": str(poster_wei),
                "reauction_credited_to": credit_to}

    # -- challenger bonds -------------------------------------------------------
    def post_challenger_bond(self, agent_id: str, task_id: str) -> int:
        """Lock the 0.02 ETH challenger bond for a quality dispute."""
        bal = self.balance_of(agent_id)
        if int(bal["available_wei"]) < CHALLENGER_BOND_WEI:
            raise InsufficientStake(
                f"challenger bond is {CHALLENGER_BOND_WEI} wei; "
                f"{agent_id} has {bal['available_wei']} available")
        rec = self._agent(agent_id)
        rec["bonds"][task_id] = str(
            int(rec["bonds"].get(task_id, "0")) + CHALLENGER_BOND_WEI)
        self._event("challenger_bond", agent_id=agent_id, task_id=task_id,
                    amount_wei=str(CHALLENGER_BOND_WEI))
        self._save()
        return CHALLENGER_BOND_WEI

    def release_bond(self, agent_id: str, task_id: str) -> int:
        rec = self._agent(agent_id)
        amount = int(rec["bonds"].pop(task_id, "0"))
        if amount:
            self._event("bond_released", agent_id=agent_id, task_id=task_id,
                        amount_wei=str(amount))
            self._save()
        return amount

    # -- poster re-auction credits ------------------------------------------------
    def credit_reauction(self, poster_id: str, amount_wei: int,
                         reason: str) -> None:
        if amount_wei <= 0:
            return
        credits = self._data["credits"]
        credits[poster_id] = str(int(credits.get(poster_id, "0")) + amount_wei)
        self._event("reauction_credit", poster_id=poster_id,
                    amount_wei=str(amount_wei), reason=reason)

    def reauction_credit(self, poster_id: str) -> int:
        return int(self._data["credits"].get(poster_id, "0"))

    # -- adjudication entry point -------------------------------------------------
    def adjudicate(self, agent_id: str, task_id: str, upheld: bool,
                   adjudicator: Optional[str] = None,
                   poster_id: Optional[str] = None) -> Dict[str, Any]:
        """Adjudicator ruling on a quality dispute (challenger bond required
        from the challenger separately via post_challenger_bond).

        upheld=True  -> 50 % slash of the winner's locked stake.
        upheld=False -> stake released, no slash.  A dispute the adjudicator
        does NOT uphold leaves the work standing, so it counts as a
        dispute-free completion for reputation-weighted collateral."""
        if upheld:
            return self.slash(agent_id, task_id, QUALITY_SLASH_BPS,
                              "quality_failure_upheld",
                              adjudicator=adjudicator, poster_id=poster_id)
        released = self.release(agent_id, task_id)
        self.record_completion(agent_id, task_id, poster_id=poster_id)
        self._event("adjudicated", agent_id=agent_id, task_id=task_id,
                    upheld=False, released_wei=str(released),
                    adjudicator=str(adjudicator or ""))
        self._save()
        return {"agent_id": agent_id, "task_id": task_id, "upheld": False,
                "released_wei": str(released)}

    def task_was_adjudicated(self, task_id: str) -> bool:
        """True if this task has an adjudication/slash record (pure read).

        Used by the settlement-proof route: adjudicated tasks may only settle
        with an adjudicator-signed ruling.
        """
        for event in self._data.get("events", []):
            if not isinstance(event, dict):
                continue
            if event.get("kind") in ("adjudicated", "slash") \
                    and str(event.get("task_id") or "") == str(task_id):
                return True
        return False


    # -- onchain wiring (StakeSlashManager) -----------------------------------
    # Dry-run only until the founder-authorized deployment (see
    # docs/ops/STAKE_SLASH_DEPLOY_RUNBOOK.md).  The bridge module never
    # imports eth_account and never sees keys: every builder returns an
    # unsigned tx dict; the caller signs and broadcasts with their own
    # signer.  Slash rulings are adjudicator-signed EIP-191 digests over a
    # domain-bound struct hash (contract address + chainid); the contract
    # enforces the signature, the per-agent nonce, and the expiry.
    def onchain_bridge(self):
        """Return a configured StakeSlashBridge, or raise if undeployed."""
        from sincor2.onchain.stake_bridge import (
            StakeSlashBridge,
            StakeSlashNotConfiguredError,
        )
        bridge = StakeSlashBridge()
        try:
            bridge._require_configured()
        except StakeSlashNotConfiguredError:
            raise StakeSlashNotConfiguredError(
                "StakeSlashManager is not deployed/configured: set "
                "STAKE_SLASH_ADDRESS and STAKE_RPC_URL after the "
                "founder-authorized deploy ceremony"
            )
        return bridge

    def build_onchain_deposit(self, agent_wallet: str, amount_wei: int,
                              sender: Optional[str] = None) -> Dict[str, Any]:
        """Unsigned stake() tx dict for an exact on-chain deposit.

        The agent must approve the manager for ``amount_wei`` first
        (bridge.build_approve_tx).  After the caller signs and broadcasts,
        record it with record_onchain_deposit(agent_id, amount_wei, tx_hash).
        """
        if int(amount_wei) <= 0:
            raise ValueError("amount_wei must be positive")
        bridge = self.onchain_bridge()
        return bridge.build_stake_tx(agent_wallet, int(amount_wei),
                                     sender or agent_wallet)

    def dry_run_onchain_deposit(self, agent_wallet: str, amount_wei: int,
                                sender: Optional[str] = None) -> Dict[str, Any]:
        """eth_call dry-run of the deposit tx. Green means the calldata is
        exact and the deposit would succeed on the configured chain."""
        bridge = self.onchain_bridge()
        tx = self.build_onchain_deposit(agent_wallet, amount_wei, sender)
        return bridge.dry_run(tx)

    def record_onchain_deposit(self, agent_id: str, amount_wei: int,
                               tx_hash: str) -> Dict[str, Any]:
        """Reconcile a broadcast on-chain deposit into the offchain ledger.

        The tx hash is stored as the deposit's reference; the ledger never
        moves funds itself.
        """
        if not str(tx_hash or "").startswith("0x"):
            raise ValueError("tx_hash must be a 0x transaction hash")
        return self.deposit(agent_id, amount_wei, reference=str(tx_hash))

    def quote_slash_ruling(self, agent_id: str, agent_wallet: str,
                           poster_wallet: str, task_id: str, slash_bps: int,
                           reason: str, nonce: int, expiry: int) -> Dict[str, Any]:
        """Build the ruling fields for an adjudicator-signed slash.

        Read-only: computes the slash amount from the agent's locked stake
        for ``task_id`` and the senior treasury cut from the sponsored
        ledger's outstanding front (min(slashed, outstanding)).  Nothing is
        mutated; the actual accounting happens in slash()/slash_ghost()
        after the ruling executes on-chain.  The returned dict feeds
        ``stake_bridge.SlashRuling``; the adjudicator signs
        ``slash_struct_hash(...)`` with their own tooling (canonicalized
        low-S via ``stake_bridge.canonicalize_signature``).
        """
        if reason not in ("ghost", "quality"):
            raise ValueError("reason must be 'ghost' or 'quality'")
        if not 0 < int(slash_bps) <= BPS_DENOM:
            raise ValueError("slash_bps must be within (0, 10000]")
        rec = self._agent(agent_id)
        locked = int(rec["locks"].get(task_id, "0"))
        if locked <= 0:
            raise InsufficientStake(f"no locked stake for {agent_id}/{task_id}")
        slashed = locked * int(slash_bps) // BPS_DENOM
        try:
            from sincor2.sponsored_stake import sponsored_ledger
            outstanding = int(sponsored_ledger().outstanding_wei(agent_id))
        except Exception:
            outstanding = 0
        treasury_cut = min(slashed, outstanding)
        return {"agent": agent_wallet, "poster": poster_wallet,
                "amount_wei": slashed, "treasury_cut_wei": treasury_cut,
                "nonce": int(nonce), "expiry": int(expiry), "reason": reason}

    def build_onchain_slash_tx(self, ruling: Dict[str, Any],
                               signature: tuple,
                               sender: str) -> Dict[str, Any]:
        """Unsigned slash() tx dict for an adjudicator-signed ruling.

        ``ruling`` is the dict from quote_slash_ruling (or equivalent);
        ``signature`` is the (v, r, s) triple over
        ``slash_struct_hash(...)``.  Anyone may submit the tx — the contract
        enforces the adjudicator signature, nonce, and expiry on-chain.
        """
        from sincor2.onchain.stake_bridge import SlashRuling
        bridge = self.onchain_bridge()
        return bridge.build_slash_tx(SlashRuling(**ruling), signature, sender)

    def note_onchain_tx(self, kind: str, tx_hash: str, **detail: Any) -> None:
        """Append an on-chain broadcast reference to the ledger event log."""
        self._event("onchain_tx", kind=kind, tx_hash=str(tx_hash), **detail)
        self._save()


# --- process-wide singleton (overridable in tests) -----------------------------

_LEDGER: Optional[StakeLedger] = None


def stake_ledger(path: Optional[str] = None) -> StakeLedger:
    global _LEDGER
    if _LEDGER is None or path is not None:
        _LEDGER = StakeLedger(path=path)
    return _LEDGER


def reset_stake_ledger(path: Optional[str] = None) -> StakeLedger:
    global _LEDGER
    _LEDGER = StakeLedger(path=path)
    return _LEDGER
