"""Python stake enforcement for the sealed-bid task market.

Ratified parameters (2026-09-25, docs/ops/AUCTION_SECURITY_DECISIONS.md):
  * minStakeBps = 5000 — a bidder must have >= 50 % of the bid value staked.
  * challengerBond = 0.02 ETH — bond required to open a quality dispute.
  * Ghosting (commit without reveal): 100 % slash of the locked stake.
  * Upheld quality failure: 50 % slash of the locked stake.
  * 100 % of slashed proceeds become non-withdrawable poster re-auction
    credit (deferred deflationary mechanics stay deferred).
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
  submit_proof  -> winner released on settlement
  adjudicate()  -> adjudicator-only quality slash (50 %)

This is the Python accounting layer.  On-chain stake deposits / slashing via
ExecutionEscrowManager are the later wiring step once contracts deploy.
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


class InsufficientStake(PermissionError):
    pass


class UnauthorizedSlashing(PermissionError):
    pass


def required_stake_wei(bid_value_wei: int) -> int:
    """Minimum stake for a bid value (50 % of bid, ratified)."""
    return int(bid_value_wei) * MIN_STAKE_BPS // BPS_DENOM


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StakeLedger:
    """Persistent stake accounting: deposits, per-task locks, slashes,
    challenger bonds, and poster re-auction credits.  Atomic JSON writes."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.expanduser(
            "~/workspace/ops/stake_ledger.json")
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

    # -- deposits -----------------------------------------------------------
    def deposit(self, agent_id: str, amount_wei: int) -> Dict[str, Any]:
        """Record an exact stake deposit (on-chain movement is separate)."""
        if amount_wei <= 0:
            raise ValueError("deposit must be positive")
        rec = self._agent(agent_id)
        rec["deposited_wei"] = str(int(rec["deposited_wei"]) + int(amount_wei))
        self._event("deposit", agent_id=agent_id, amount_wei=str(amount_wei))
        self._save()
        return self.balance_of(agent_id)

    def balance_of(self, agent_id: str) -> Dict[str, Any]:
        rec = self._agent(agent_id)
        deposited = int(rec["deposited_wei"])
        locked = sum(int(v) for v in rec["locks"].values())
        bonded = sum(int(v) for v in rec["bonds"].values())
        return {"agent_id": agent_id, "deposited_wei": str(deposited),
                "locked_wei": str(locked), "bonded_wei": str(bonded),
                "available_wei": str(deposited - locked - bonded),
                "slashed_wei": rec["slashed_wei"]}

    # -- commit / reveal locks ------------------------------------------------
    def lock_for_commit(self, agent_id: str, task_id: str,
                        bounty_wei: int) -> int:
        """Lock bounty*50% at commit.  Raises InsufficientStake => the
        commit itself must be rejected."""
        need = required_stake_wei(bounty_wei)
        bal = self.balance_of(agent_id)
        if int(bal["available_wei"]) < need:
            raise InsufficientStake(
                f"agent {agent_id} needs {need} wei staked for this auction "
                f"(has {bal['available_wei']} available)")
        rec = self._agent(agent_id)
        rec["locks"][task_id] = str(int(rec["locks"].get(task_id, "0")) + need)
        self._event("lock", agent_id=agent_id, task_id=task_id,
                    amount_wei=str(need), basis="bounty_at_commit")
        self._save()
        return need

    def top_up_for_reveal(self, agent_id: str, task_id: str,
                          bid_wei: int) -> int:
        """After reveal the true bid value is known; top the lock up to
        bid*50 %.  Raises InsufficientStake => the reveal must be rejected."""
        need = required_stake_wei(bid_wei)
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
                    amount_wei=str(extra), basis="bid_at_reveal")
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

    def slash(self, agent_id: str, task_id: str, slash_bps: int, reason: str,
              adjudicator: Optional[str] = None,
              poster_id: Optional[str] = None) -> Dict[str, Any]:
        """Slash ``slash_bps`` of the agent's locked stake for ``task_id``.
        100 % of proceeds become non-withdrawable poster re-auction credit."""
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
        credit_to = poster_id or "__platform__"
        self.credit_reauction(credit_to, slashed,
                              f"slash:{reason}:{task_id}")
        self._event("slash", agent_id=agent_id, task_id=task_id,
                    slash_bps=slash_bps, slashed_wei=str(slashed),
                    reason=reason, adjudicator=caller, credited_to=credit_to)
        self._save()
        return {"agent_id": agent_id, "task_id": task_id,
                "slashed_wei": str(slashed), "reason": reason,
                "reauction_credited_to": credit_to}

    def slash_ghost(self, agent_id: str, task_id: str,
                    poster_id: Optional[str] = None) -> Dict[str, Any]:
        """Ghosting: committed but never revealed => 100 % slash.
        Called by close_auction (system path); the adjudicator gate is
        satisfied by the protocol rule itself."""
        rec = self._agent(agent_id)
        locked = int(rec["locks"].pop(task_id, "0"))
        if locked <= 0:
            return {"agent_id": agent_id, "task_id": task_id,
                    "slashed_wei": "0", "reason": "ghosting_no_lock"}
        rec["deposited_wei"] = str(int(rec["deposited_wei"]) - locked)
        rec["slashed_wei"] = str(int(rec["slashed_wei"]) + locked)
        credit_to = poster_id or "__platform__"
        self.credit_reauction(credit_to, locked, f"slash:ghosting:{task_id}")
        self._event("slash", agent_id=agent_id, task_id=task_id,
                    slash_bps=GHOST_SLASH_BPS, slashed_wei=str(locked),
                    reason="ghosting", adjudicator="protocol",
                    credited_to=credit_to)
        self._save()
        return {"agent_id": agent_id, "task_id": task_id,
                "slashed_wei": str(locked), "reason": "ghosting",
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
        upheld=False -> stake released, no slash."""
        if upheld:
            return self.slash(agent_id, task_id, QUALITY_SLASH_BPS,
                              "quality_failure_upheld",
                              adjudicator=adjudicator, poster_id=poster_id)
        released = self.release(agent_id, task_id)
        self._event("adjudicated", agent_id=agent_id, task_id=task_id,
                    upheld=False, released_wei=str(released),
                    adjudicator=str(adjudicator or ""))
        self._save()
        return {"agent_id": agent_id, "task_id": task_id, "upheld": False,
                "released_wei": str(released)}


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
