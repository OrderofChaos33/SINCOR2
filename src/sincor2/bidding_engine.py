"""
SINCOR2 Bidding Engine — Isolated Contract-Net Auction Logic

Separates the contract-net protocol from general agent execution so that
individual bid failures are contained and never propagate to the market loop.

Architecture:
  - BiddingEngine wraps every bid-lifecycle operation in isolated try/except
  - Each failure logs a structured error event and returns a safe sentinel
  - The market loop continues uninterrupted even if an agent crashes mid-bid
  - Token budget enforcement is checked before any bid is scored
"""

from __future__ import annotations

import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sincor.bidding")


# ---------------------------------------------------------------------------
# Sentinel / result types
# ---------------------------------------------------------------------------

class BidResult:
    """Immutable result of a single bid operation."""

    __slots__ = ("ok", "bid_id", "agent_id", "score", "error", "detail")

    def __init__(
        self,
        *,
        ok: bool,
        bid_id: str = "",
        agent_id: str = "",
        score: float = 0.0,
        error: str = "",
        detail: str = "",
    ) -> None:
        self.ok = ok
        self.bid_id = bid_id
        self.agent_id = agent_id
        self.score = score
        self.error = error
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"BidResult(ok={self.ok}, agent={self.agent_id!r}, "
            f"score={self.score:.3f}, error={self.error!r})"
        )


class AuctionResult:
    """Outcome of a full contract-net auction round."""

    __slots__ = ("task_id", "winner_agent_id", "winner_bid_id", "winner_score",
                 "total_bids", "failed_bids", "skipped_bids", "ok", "error")

    def __init__(
        self,
        *,
        task_id: str,
        winner_agent_id: str = "",
        winner_bid_id: str = "",
        winner_score: float = 0.0,
        total_bids: int = 0,
        failed_bids: int = 0,
        skipped_bids: int = 0,
        ok: bool = False,
        error: str = "",
    ) -> None:
        self.task_id = task_id
        self.winner_agent_id = winner_agent_id
        self.winner_bid_id = winner_bid_id
        self.winner_score = winner_score
        self.total_bids = total_bids
        self.failed_bids = failed_bids
        self.skipped_bids = skipped_bids
        self.ok = ok
        self.error = error


# ---------------------------------------------------------------------------
# Scoring helpers (pure functions — no I/O, safe to isolate)
# ---------------------------------------------------------------------------

def _score_bid_isolated(bid: Dict[str, Any], task: Dict[str, Any],
                         agent_rep: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Multi-criteria bid scoring.  Returns (total, components).

    Weights:
      confidence    40 %
      reputation    25 %
      cost_efficiency 20 %
      plan_quality  10 %
      archetype_fit  5 %
    """
    components: Dict[str, float] = {}

    # Confidence (agent-reported)
    confidence = float(bid.get("confidence", 0.5))
    components["confidence"] = min(1.0, max(0.0, confidence)) * 0.40

    # Reputation — success_rate from agent reputation store
    success_rate = float(agent_rep.get("success_rate", 0.5))
    components["reputation"] = min(1.0, max(0.0, success_rate)) * 0.25

    # Cost efficiency relative to budget
    budget_tokens = int(task.get("budget_tokens", 1)) or 1
    budget_calls = int(task.get("budget_tool_calls", 1)) or 1
    est_tokens = int(bid.get("estimated_cost_tokens", budget_tokens))
    est_calls = int(bid.get("estimated_cost_calls", budget_calls))
    token_eff = max(0.0, 1.0 - est_tokens / budget_tokens)
    call_eff = max(0.0, 1.0 - est_calls / budget_calls)
    components["efficiency"] = ((token_eff + call_eff) / 2) * 0.20

    # Plan quality — more steps → higher quality signal
    plan = bid.get("plan", [])
    plan_quality = min(1.0, len(plan) / 5.0)
    components["plan_quality"] = plan_quality * 0.10

    # Archetype-skill affinity
    archetype_affinities: Dict[str, List[str]] = {
        "Scout":       ["prospect", "scrape", "monitor", "research", "validate"],
        "Synthesizer": ["summarize", "dedup", "deconflict", "analyze", "curate"],
        "Builder":     ["develop", "automate", "deploy", "test", "debug"],
        "Negotiator":  ["outreach", "negotiate", "persuade", "present", "close"],
        "Caretaker":   ["clean", "label", "backup", "maintain", "organize"],
        "Auditor":     ["evaluate", "verify", "investigate", "report", "certify"],
        "Director":    ["prioritize", "coordinate", "decide", "allocate", "plan"],
    }
    archetype = bid.get("archetype", "")
    required_skills = set(task.get("skills_required", []))
    affinity_skills = set(archetype_affinities.get(archetype, []))
    overlap = len(affinity_skills & required_skills)
    archetype_fit = overlap / max(1, len(required_skills))
    components["archetype_fit"] = archetype_fit * 0.05

    total = sum(components.values())
    return total, components


# ---------------------------------------------------------------------------
# Isolated bid validation
# ---------------------------------------------------------------------------

def _validate_bid_isolated(bid: Dict[str, Any], task: Dict[str, Any],
                             token_controller: Any) -> Tuple[bool, str]:
    """
    Validate a bid without raising.  Returns (valid, reason).

    Checks:
      1. Required fields present
      2. Agent has not hit daily token ceiling (token_budget_controller)
      3. Task is still in BROADCAST status
      4. Agent possesses required skills (from reputation store)
    """
    for field in ("agent_id", "task_id", "confidence", "plan"):
        if field not in bid:
            return False, f"missing_field:{field}"

    # Token budget hard-stop
    if token_controller is not None:
        agent_id = bid.get("agent_id", "")
        if not token_controller.is_allowed(agent_id):
            return False, f"daily_token_ceiling_hit:{agent_id}"

    task_status = task.get("status", "")
    if isinstance(task_status, str):
        status_val = task_status
    else:
        status_val = getattr(task_status, "value", str(task_status))

    if status_val not in ("broadcast", "BROADCAST"):
        return False, f"task_not_open:{status_val}"

    return True, ""


# ---------------------------------------------------------------------------
# Core BiddingEngine
# ---------------------------------------------------------------------------

class BiddingEngine:
    """
    Isolated contract-net auction engine.

    All public methods return structured result objects instead of raising.
    The caller (market loop) can inspect .ok and .error and continue safely.

    Usage::

        engine = BiddingEngine(token_controller=controller)
        result = engine.run_auction(task, bids, agent_reputations)
        if result.ok:
            assign(result.winner_agent_id)
    """

    def __init__(self, token_controller: Any = None) -> None:
        """
        Parameters
        ----------
        token_controller:
            Optional ``TokenBudgetController`` instance.  When provided, the
            engine checks every bidder's daily token ceiling before scoring.
            Pass ``None`` to disable budget enforcement (dev/test only).
        """
        self._tc = token_controller

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_bid(
        self,
        bid: Dict[str, Any],
        task: Dict[str, Any],
        agent_rep: Optional[Dict[str, Any]] = None,
    ) -> BidResult:
        """
        Score a single bid.  Never raises — failures return BidResult(ok=False).

        Parameters
        ----------
        bid:
            Bid dict (keys: agent_id, task_id, confidence, plan, archetype, …)
        task:
            Task dict (keys: budget_tokens, budget_tool_calls, skills_required, …)
        agent_rep:
            Agent reputation dict from reputation store.  Defaults to empty.
        """
        agent_id = str(bid.get("agent_id", ""))
        bid_id = str(bid.get("bid_id", "") or f"B-{uuid.uuid4().hex[:8]}")

        # --- Validation phase (isolated) ---
        try:
            valid, reason = _validate_bid_isolated(bid, task, self._tc)
            if not valid:
                logger.info("[BIDDING] bid skipped agent=%s reason=%s", agent_id, reason)
                return BidResult(ok=False, bid_id=bid_id, agent_id=agent_id,
                                 error="validation_failed", detail=reason)
        except Exception:
            tb = traceback.format_exc()
            logger.error("[BIDDING] validation crashed agent=%s\n%s", agent_id, tb)
            return BidResult(ok=False, bid_id=bid_id, agent_id=agent_id,
                             error="validation_exception", detail=tb[:200])

        # --- Scoring phase (isolated) ---
        try:
            rep = agent_rep or {}
            score, components = _score_bid_isolated(bid, task, rep)
            logger.debug(
                "[BIDDING] bid scored agent=%s bid=%s score=%.3f components=%s",
                agent_id, bid_id, score, components,
            )
            return BidResult(ok=True, bid_id=bid_id, agent_id=agent_id, score=score)
        except Exception:
            tb = traceback.format_exc()
            logger.error("[BIDDING] scoring crashed agent=%s\n%s", agent_id, tb)
            return BidResult(ok=False, bid_id=bid_id, agent_id=agent_id,
                             error="scoring_exception", detail=tb[:200])

    def run_auction(
        self,
        task: Dict[str, Any],
        bids: List[Dict[str, Any]],
        agent_reputations: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> AuctionResult:
        """
        Run a full contract-net auction round over *bids* for *task*.

        Each bid is evaluated in isolation — one crash cannot affect others.
        Returns an AuctionResult with the winner (if any) and diagnostics.

        Parameters
        ----------
        task:
            Task dict.
        bids:
            List of bid dicts.
        agent_reputations:
            Mapping of agent_id → reputation dict.
        """
        task_id = str(task.get("task_id", "unknown"))
        reps = agent_reputations or {}

        if not bids:
            return AuctionResult(task_id=task_id, ok=False, error="no_bids")

        scored: List[Tuple[float, BidResult]] = []
        failed = 0
        skipped = 0

        for bid in bids:
            result = self.evaluate_bid(bid, task, reps.get(bid.get("agent_id", ""), {}))
            if result.ok:
                scored.append((result.score, result))
            else:
                if result.error == "validation_failed":
                    skipped += 1
                else:
                    failed += 1

        if not scored:
            return AuctionResult(
                task_id=task_id,
                ok=False,
                error="no_valid_bids",
                total_bids=len(bids),
                failed_bids=failed,
                skipped_bids=skipped,
            )

        scored.sort(key=lambda t: t[0], reverse=True)
        winner_score, winner = scored[0]

        logger.info(
            "[BIDDING] auction complete task=%s winner=%s score=%.3f "
            "total=%d failed=%d skipped=%d",
            task_id, winner.agent_id, winner_score,
            len(bids), failed, skipped,
        )

        return AuctionResult(
            task_id=task_id,
            winner_agent_id=winner.agent_id,
            winner_bid_id=winner.bid_id,
            winner_score=winner_score,
            total_bids=len(bids),
            failed_bids=failed,
            skipped_bids=skipped,
            ok=True,
        )

    def run_auction_from_market(
        self,
        task_market: Any,
        task_id: str,
    ) -> AuctionResult:
        """
        Convenience wrapper: pulls task + bids from a TaskMarket instance and
        runs the isolated auction.  Writes the winner back to the market.

        The market loop calls this instead of ``task_market.evaluate_and_award_task``
        directly, so individual bid crashes stay within this engine.
        """
        try:
            task_obj = task_market.active_tasks.get(task_id)
            if task_obj is None:
                return AuctionResult(task_id=task_id, ok=False, error="task_not_found")

            raw_bids = task_market.get_task_bids(task_id)
            bids_as_dicts = []
            for b in raw_bids:
                from dataclasses import asdict
                try:
                    d = asdict(b)
                    # Normalise enum values to plain strings
                    for k, v in d.items():
                        if hasattr(v, "value"):
                            d[k] = v.value
                    bids_as_dicts.append(d)
                except Exception:
                    pass  # Skip corrupted bid records

            from dataclasses import asdict as _asdict
            try:
                task_dict = _asdict(task_obj)
                for k, v in task_dict.items():
                    if hasattr(v, "value"):
                        task_dict[k] = v.value
            except Exception:
                task_dict = {"task_id": task_id, "status": "broadcast"}

            result = self.run_auction(
                task_dict, bids_as_dicts, task_market.agent_reputation
            )

            # Write winner back to market (isolated — failure doesn't affect result)
            if result.ok:
                try:
                    task_market.evaluate_and_award_task(task_id)
                except Exception:
                    logger.warning(
                        "[BIDDING] market award write failed for task=%s (winner=%s)",
                        task_id, result.winner_agent_id,
                    )

            return result

        except Exception:
            tb = traceback.format_exc()
            logger.error("[BIDDING] run_auction_from_market crashed task=%s\n%s", task_id, tb)
            return AuctionResult(task_id=task_id, ok=False, error="market_exception",
                                 )


# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_engine: Optional[BiddingEngine] = None


def get_bidding_engine(token_controller: Any = None) -> BiddingEngine:
    """Return the module-level BiddingEngine singleton, creating it if needed."""
    global _engine
    if _engine is None:
        _engine = BiddingEngine(token_controller=token_controller)
    return _engine
