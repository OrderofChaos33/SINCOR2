"""Anti-gaming guards for the AXM Activity Engine's continuous auction loop.

LEGAL REQUIREMENT: wash trading must be impossible by construction. The
activity loop manufactures real marketplace volume; these guards are what
keep it legal. Every check below FAILS CLOSED: a guard that cannot verify
eligibility rejects the bid, and every rejection is logged.

Four guard families:

1. Self-bid rejection — an agent can never bid on a task it posted
   (``poster_id`` is server-bound at task creation, so the bidder cannot
   spoof it away). Tasks with NO poster are anonymous: the guard cannot
   prove the bidder is not the poster, so any anonymous task carrying a
   bounty (>0) is un-biddable — bids are rejected fail-closed. Unsigned
   POSTING itself is unchanged (P3-14); only the bid side is rejected.
   Enforced in ``place_bid`` / ``commit_bid`` / ``reveal_bid`` and
   re-checked at winner selection in ``close_auction``.

2. Participation rate limits — rolling-window caps on how many auctions a
   single agent may ENTER (bids) and WIN, so no agent can dominate the loop.

3. Ghost slash wiring — a winner that never performs is slashed under the
   EXISTING stake_ledger ghost rule (100 % of locked stake, protocol path).
   See ``expire_stale_assignments`` in ``a2a_inbound_market``; no new slash
   math is invented here.

4. Anti-collusion rotation — no fixed agent, and no fixed poster/winner
   pair, may win more than K consecutive auctions, and no fixed pair may
   win more than W auctions per rolling window (non-consecutive: this is
   what breaks deterministic alternation, where consecutive streaks stay
   at 1). Enforced at bid time, so a would-be win-trading loop is broken
   BEFORE the win happens, and RE-VALIDATED at winner selection in
   ``close_auction`` — a bid committed before a cap tripped must not
   still win. Ineligible bids are excluded from selection (fail closed,
   logged); if none remain eligible the auction expires.
   * ``win_streak[agent]`` — consecutive auctions won in the global close
     sequence. Reaching the cap rejects the agent's next bid.
   * ``pair_streak[(poster, winner)]`` — consecutive auctions posted by
     ``poster`` and won by ``winner``. Reaching the cap rejects that
     bidder on that poster's tasks.
   * ``pair_win_events[(poster, winner)]`` — rolling-window list of pair
     wins (any spacing). Reaching the wins-per-window cap rejects that
     bidder on that poster's tasks.
   A close that produces NO winner resets the streaks: this is the
   anti-brick property — a lone agent cannot be locked out of the loop
   forever, and a blocked colluder regains eligibility only after sitting
   out (the wash loop is punctuated by dead auctions, which is exactly
   what makes manufactured volume economically pointless). Windowed
   events are time-pruned only; an empty close does NOT erase them.

State is a small JSON store (thread-safe, atomic writes, pruned to the
rolling windows) so caps survive restarts — a restart must not be a
gaming window. Bounds: per-agent event lists are pruned to the window on
every write; the rejection audit log keeps the last 500 entries.

Tuning knobs (env, read per call so tests can monkeypatch):
  AXM_AG_MAX_BIDS_PER_HOUR   default 50   — auctions one agent may enter
  AXM_AG_MAX_WINS_PER_HOUR    default 20   — auctions one agent may win
  AXM_AG_PARTICIPATION_WINDOW_S default 3600 — rolling window for both
  AXM_AG_MAX_CONSECUTIVE_WINS default 3    — per-agent rotation cap
  AXM_AG_MAX_PAIR_CONSECUTIVE default 2    — per poster/winner-pair cap
  AXM_AG_MAX_PAIR_WINS_PER_WINDOW default 2 — per-pair wins per rolling
     window (non-consecutive). Default 2 breaks deterministic 2-agent
     alternation (each pair is capped at 2 wins/hour); raise for
     high-throughput honest single-poster loops.
  SINCOR_ANTIGAMING_PATH      default <data_dir>/axm_antigaming.json
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.market.antigaming")


class SelfBidError(PermissionError):
    """An agent attempted to bid on a task it posted."""


class AnonymousBountyError(SelfBidError):
    """A bid targeted an anonymous (poster_id=None) task carrying a bounty.

    Subclasses SelfBidError so existing self-bid handling (HTTP 403,
    fail-closed paths) applies unchanged; the distinct type lets forensics
    tell "bidder == poster" apart from "poster unknowable"."""


class ParticipationCapError(PermissionError):
    """An agent exceeded a rolling-window participation cap."""


class CollusionCapError(PermissionError):
    """A bid would extend a consecutive-win streak past the rotation cap."""


class GuardUnavailableError(PermissionError):
    """Fail-closed: the guard store errored, so the bid is rejected."""


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def max_bids_per_window() -> int:
    return _env_int("AXM_AG_MAX_BIDS_PER_HOUR", 50)


def max_wins_per_window() -> int:
    return _env_int("AXM_AG_MAX_WINS_PER_HOUR", 20)


def participation_window_s() -> int:
    return _env_int("AXM_AG_PARTICIPATION_WINDOW_S", 3600)


def max_consecutive_wins() -> int:
    return _env_int("AXM_AG_MAX_CONSECUTIVE_WINS", 3)


def max_pair_consecutive() -> int:
    return _env_int("AXM_AG_MAX_PAIR_CONSECUTIVE", 2)


def max_pair_wins_per_window() -> int:
    return _env_int("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", 2)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_store_path() -> str:
    explicit = os.environ.get("SINCOR_ANTIGAMING_PATH", "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "axm_antigaming.json")
    except Exception:
        return os.path.expanduser("~/workspace/ops/axm_antigaming.json")


_PAIR_SEP = "\x00"
_MAX_REJECTIONS = 500


class AntigamingStore:
    """Persistent anti-gaming state: rolling participation events, win
    streaks, and a bounded rejection audit log. Thread-safe; atomic JSON
    writes. A corrupt file starts empty (loud warning) rather than bricking
    the market — but every *check-time* failure fails closed."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _default_store_path()
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "bid_events": {},   # agent_id -> [ts_ms, ...] (entered auctions)
            "win_events": {},   # agent_id -> [ts_ms, ...] (won auctions)
            "win_streak": {},   # agent_id -> consecutive wins (global order)
            "pair_streak": {},  # "poster\\x00winner" -> consecutive wins
            "pair_win_events": {},  # "poster\\x00winner" -> [ts_ms, ...]
            "rejections": [],   # bounded audit log of rejected bids
        }
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    for key in self._data:
                        if isinstance(raw.get(key), (dict, list)):
                            self._data[key] = raw[key]
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("antigaming store load failed (%s); "
                               "starting empty", exc)

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)

    # -- internals (call with self._lock held) ------------------------------
    def _prune(self, agent_id: str, now_ms: int) -> None:
        cutoff = now_ms - participation_window_s() * 1000
        for bucket in ("bid_events", "win_events"):
            events = self._data[bucket].get(agent_id)
            if events:
                kept = [t for t in events if int(t) >= cutoff]
                if kept:
                    self._data[bucket][agent_id] = kept
                else:
                    self._data[bucket].pop(agent_id, None)

    def _prune_pairs(self, now_ms: int) -> None:
        """Prune every pair's win events to the rolling window (call with
        self._lock held). The store is small; a full sweep is cheap."""
        cutoff = now_ms - participation_window_s() * 1000
        for key, events in list(self._data["pair_win_events"].items()):
            kept = [t for t in events if int(t) >= cutoff]
            if kept:
                self._data["pair_win_events"][key] = kept
            else:
                self._data["pair_win_events"].pop(key, None)

    def _reject(self, agent_id: str, task_id: str, reason: str,
                detail: str = "") -> None:
        entry = {"at": _now_ms(), "agent_id": str(agent_id),
                 "task_id": str(task_id), "reason": reason}
        if detail:
            entry["detail"] = detail
        self._data["rejections"].append(entry)
        if len(self._data["rejections"]) > _MAX_REJECTIONS:
            self._data["rejections"] = self._data["rejections"][-_MAX_REJECTIONS:]
        logger.warning("antigaming rejection agent=%s task=%s reason=%s %s",
                       agent_id, task_id, reason, detail)

    @staticmethod
    def _pair_key(poster_id: str, winner_id: str) -> str:
        return f"{poster_id}{_PAIR_SEP}{winner_id}"

    # -- read API (pure-ish; used by guards and tests) ----------------------
    def bids_in_window(self, agent_id: str, now_ms: Optional[int] = None) -> int:
        now_ms = _now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            self._prune(agent_id, now_ms)
            return len(self._data["bid_events"].get(agent_id, []))

    def wins_in_window(self, agent_id: str, now_ms: Optional[int] = None) -> int:
        now_ms = _now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            self._prune(agent_id, now_ms)
            return len(self._data["win_events"].get(agent_id, []))

    def win_streak_of(self, agent_id: str) -> int:
        with self._lock:
            return int(self._data["win_streak"].get(agent_id, 0))

    def pair_streak_of(self, poster_id: str, winner_id: str) -> int:
        with self._lock:
            return int(self._data["pair_streak"].get(
                self._pair_key(poster_id, winner_id), 0))

    def pair_wins_in_window(self, poster_id: str, winner_id: str,
                            now_ms: Optional[int] = None) -> int:
        """Non-consecutive pair wins inside the rolling window — the
        alternation-breaker. Unlike pair_streak_of, rotation by a third
        party does NOT reset this count; only time prunes it."""
        now_ms = _now_ms() if now_ms is None else int(now_ms)
        with self._lock:
            self._prune_pairs(now_ms)
            return len(self._data["pair_win_events"].get(
                self._pair_key(str(poster_id), str(winner_id)), []))

    def rejection_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._data["rejections"][-limit:]]


# --- process-wide singleton (overridable in tests) ---------------------------
_STORE: Optional[AntigamingStore] = None
_STORE_LOCK = threading.RLock()


def antigaming_store() -> AntigamingStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = AntigamingStore()
        return _STORE


def reset_antigaming_store(path: Optional[str] = None) -> AntigamingStore:
    """Test hook: swap the singleton for an isolated store."""
    global _STORE
    with _STORE_LOCK:
        _STORE = AntigamingStore(path=path)
        return _STORE


# --- guard 1: self-bid rejection ---------------------------------------------
def _audit_rejection(agent_id: str, task_id: str, reason: str,
                     detail: str = "") -> None:
    """Append to the durable rejection audit log. Never raises: audit
    accounting must not brick the guard path."""
    try:
        store = antigaming_store()
        with store._lock:
            store._reject(str(agent_id), str(task_id or ""), reason, detail)
            store._save()
    except Exception as exc:  # pragma: no cover - accounting only
        logger.warning("antigaming rejection audit failed: %s", exc)


def check_self_bid(task: Dict[str, Any], agent_id: str) -> None:
    """Reject an agent bidding on its own task. Fail closed, logged.

    ``poster_id`` is server-bound at task creation (EIP-191 signature), so
    the bidder cannot spoof it away. Tasks with NO poster are anonymous:
    the guard cannot prove the bidder is not the poster, so any anonymous
    task carrying a bounty (>0) is un-biddable — the bid is rejected
    (AnonymousBountyError, a SelfBidError subclass) and the attempt is
    written to the rejection audit log. Unsigned POSTING itself is
    unchanged (P3-14); only the bid side is rejected."""
    poster_id = task.get("poster_id")
    task_id = str(task.get("task_id") or "")
    if poster_id and str(poster_id) == str(agent_id):
        logger.warning("self-bid rejected agent=%s task=%s",
                       agent_id, task_id)
        raise SelfBidError(
            f"agent {agent_id} cannot bid on its own task {task_id}")
    if not poster_id:
        try:
            bounty = float(task.get("bounty_axm") or 0)
        except (TypeError, ValueError):
            bounty = 0.0
        if bounty > 0:
            logger.warning("anonymous-bounty bid rejected agent=%s task=%s "
                           "bounty_axm=%s", agent_id, task_id, bounty)
            _audit_rejection(agent_id, task_id, "anonymous_bounty_task",
                             f"bounty_axm={bounty}")
            raise AnonymousBountyError(
                f"agent {agent_id} cannot bid on anonymous task {task_id} "
                f"carrying a bounty ({bounty} AXM): poster_id is absent so "
                f"self-dealing cannot be ruled out")


# --- guards 2+4: participation caps and rotation caps --------------------------
def check_bid_eligibility(task: Dict[str, Any], agent_id: str,
                          now_ms: Optional[int] = None) -> None:
    """Full bid-entry gate: self-bid, participation windows, rotation caps.

    Raises (all PermissionError subclasses -> HTTP 403 on the routes):
      SelfBidError, ParticipationCapError, CollusionCapError,
      GuardUnavailableError (fail closed on store failure).
    Does NOT record the bid — call ``record_bid_entry`` after the bid is
    stored, so failed validations elsewhere never consume quota."""
    agent_id = str(agent_id)
    task_id = str(task.get("task_id") or "")
    poster_id = task.get("poster_id")
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    try:
        store = antigaming_store()
        check_self_bid(task, agent_id)
        with store._lock:
            store._prune(agent_id, now_ms)
            bids = len(store._data["bid_events"].get(agent_id, []))
            if bids >= max_bids_per_window():
                store._reject(agent_id, task_id, "bid_rate_cap",
                              f"bids_in_window={bids}")
                store._save()
                raise ParticipationCapError(
                    f"agent {agent_id} entered {bids} auctions in the last "
                    f"{participation_window_s()}s (cap "
                    f"{max_bids_per_window()})")
            wins = len(store._data["win_events"].get(agent_id, []))
            if wins >= max_wins_per_window():
                store._reject(agent_id, task_id, "win_rate_cap",
                              f"wins_in_window={wins}")
                store._save()
                raise ParticipationCapError(
                    f"agent {agent_id} won {wins} auctions in the last "
                    f"{participation_window_s()}s (cap "
                    f"{max_wins_per_window()})")
            streak = int(store._data["win_streak"].get(agent_id, 0))
            if streak >= max_consecutive_wins():
                store._reject(agent_id, task_id, "consecutive_win_cap",
                              f"win_streak={streak}")
                store._save()
                raise CollusionCapError(
                    f"agent {agent_id} won the last {streak} auctions "
                    f"(cap {max_consecutive_wins()}); rotation required")
            if poster_id:
                pair = int(store._data["pair_streak"].get(
                    store._pair_key(str(poster_id), agent_id), 0))
                if pair >= max_pair_consecutive():
                    store._reject(agent_id, task_id, "pair_win_cap",
                                  f"pair_streak={pair} poster={poster_id}")
                    store._save()
                    raise CollusionCapError(
                        f"agent {agent_id} won the last {pair} auctions "
                        f"posted by {poster_id} (cap "
                        f"{max_pair_consecutive()}); rotation required")
                # Non-consecutive pair cap: breaks deterministic
                # alternation, where consecutive streaks stay at 1 because
                # a third party (or the alternating partner) resets them.
                # Time-pruned only — rotation does NOT reset this count.
                store._prune_pairs(now_ms)
                pwins = len(store._data["pair_win_events"].get(
                    store._pair_key(str(poster_id), agent_id), []))
                if pwins >= max_pair_wins_per_window():
                    store._reject(agent_id, task_id, "pair_win_window_cap",
                                  f"pair_wins_in_window={pwins} "
                                  f"poster={poster_id}")
                    store._save()
                    raise CollusionCapError(
                        f"agent {agent_id} won {pwins} auctions posted by "
                        f"{poster_id} in the last "
                        f"{participation_window_s()}s (cap "
                        f"{max_pair_wins_per_window()}); rotation required")
    except (SelfBidError, ParticipationCapError, CollusionCapError):
        raise
    except Exception as exc:  # fail closed: unverifiable bid is rejected
        logger.error("antigaming guard failure for agent=%s task=%s: %s",
                     agent_id, task_id, exc)
        raise GuardUnavailableError(
            "anti-gaming guard unavailable; bid rejected (fail closed)")


def record_bid_entry(agent_id: str, task_id: str = "",
                     now_ms: Optional[int] = None) -> None:
    """Record a successfully entered bid (call AFTER the bid is stored).

    Never raises: quota accounting must not brick the bid path. A missed
    record only ever UNDER-counts participation, and the streak caps still
    bound domination."""
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    try:
        store = antigaming_store()
        with store._lock:
            store._prune(str(agent_id), now_ms)
            store._data["bid_events"].setdefault(str(agent_id),
                                                 []).append(now_ms)
            store._save()
    except Exception as exc:
        logger.warning("antigaming bid record failed for %s: %s",
                       agent_id, exc)


def record_auction_close(poster_id: Optional[str],
                         winner_id: Optional[str],
                         task_id: str = "",
                         now_ms: Optional[int] = None) -> None:
    """Update rotation state at auction close. Call for EVERY close —
    winner or not.

    * Winner: its global win streak +1, every other streak 0; the
      (poster, winner) pair streak +1, every other pair for that poster 0;
      a win event is recorded for the rolling win cap.
    * No winner (expired / no bids): ALL streaks reset to 0. This is the
      anti-brick property — a capped agent regains eligibility after the
      loop demonstrably rotated past it, and a lone agent can never be
      locked out forever.

    Never raises: rotation accounting must not brick auction close."""
    now_ms = _now_ms() if now_ms is None else int(now_ms)
    try:
        store = antigaming_store()
        with store._lock:
            if winner_id:
                winner_id = str(winner_id)
                for agent in list(store._data["win_streak"]):
                    store._data["win_streak"][agent] = (
                        int(store._data["win_streak"][agent]) + 1
                        if agent == winner_id else 0)
                if winner_id not in store._data["win_streak"]:
                    store._data["win_streak"][winner_id] = 1
                store._prune(winner_id, now_ms)
                store._data["win_events"].setdefault(winner_id,
                                                     []).append(now_ms)
                if poster_id:
                    poster_id = str(poster_id)
                    target = store._pair_key(poster_id, winner_id)
                    for key in list(store._data["pair_streak"]):
                        p, _, _w = key.partition(_PAIR_SEP)
                        if p == poster_id:
                            store._data["pair_streak"][key] = (
                                int(store._data["pair_streak"][key]) + 1
                                if key == target else 0)
                    if target not in store._data["pair_streak"]:
                        store._data["pair_streak"][target] = 1
                    # Rolling pair-win events for the non-consecutive
                    # wins-per-window cap (Break 2). Pruned by time only —
                    # an empty close does NOT erase these.
                    store._prune_pairs(now_ms)
                    store._data["pair_win_events"].setdefault(
                        target, []).append(now_ms)
            else:
                # No winner: the loop demonstrably did not concentrate —
                # reset every streak so caps can never deadlock the market.
                for agent in list(store._data["win_streak"]):
                    store._data["win_streak"][agent] = 0
                for key in list(store._data["pair_streak"]):
                    store._data["pair_streak"][key] = 0
            store._save()
    except Exception as exc:
        logger.warning("antigaming close record failed for task %s: %s",
                       task_id, exc)
