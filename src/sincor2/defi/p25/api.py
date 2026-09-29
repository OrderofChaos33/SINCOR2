"""P25 read API: sealed-safe, user_id-scoped portfolio reads.

A caller can only ever read their own user_id's portfolio; cross-user read
attempts are rejected (403-style). Responses are plain dicts validated
against the schema in ``response_schema()`` by the tests.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .allocator import TargetPortfolio
from .fees import FeeLedger
from .ingestor import IngestResult


class ForbiddenRead(Exception):
    """Cross-user portfolio read attempt."""

    def __init__(self, requester: str, target: str):
        super().__init__(f"forbidden: {requester!r} may not read {target!r}")
        self.requester = requester
        self.target = target


def response_schema() -> Dict[str, List[str]]:
    return {
        "required": [
            "user_id", "weights", "cash_pct", "blended_risk", "as_of",
            "feed_hash", "drift", "rebalance_status", "fee_denomination",
            "fee_accrued_wei", "feed_status",
        ]
    }


class PortfolioAPI:
    """Read-only view over one user's portfolio state."""

    def __init__(self) -> None:
        self._portfolios: Dict[str, TargetPortfolio] = {}
        self._ingest: Dict[str, IngestResult] = {}
        self._drift: Dict[str, Dict[str, float]] = {}
        self._rebalance_status: Dict[str, str] = {}
        self._fee_ledger = FeeLedger()
        self._fee_denomination: Dict[str, str] = {}

    @property
    def fee_ledger(self) -> FeeLedger:
        return self._fee_ledger

    def publish(
        self,
        user_id: str,
        portfolio: TargetPortfolio,
        ingest: IngestResult,
        drift: Optional[Dict[str, float]] = None,
        rebalance_status: str = "NOOP",
        fee_denomination: str = "AXM",
    ) -> None:
        self._portfolios[user_id] = portfolio
        self._ingest[user_id] = ingest
        self._drift[user_id] = dict(drift or {})
        self._rebalance_status[user_id] = rebalance_status
        self._fee_denomination[user_id] = fee_denomination

    def read(self, user_id: str, requester_id: str) -> Dict[str, Any]:
        if requester_id != user_id:
            raise ForbiddenRead(requester_id, user_id)
        portfolio = self._portfolios.get(user_id)
        if portfolio is None:
            return {"user_id": user_id, "exists": False}
        ingest = self._ingest.get(user_id)
        body: Dict[str, Any] = {
            "user_id": user_id,
            "exists": True,
            "weights": dict(portfolio.weights),
            "cash_pct": portfolio.cash_pct,
            "blended_risk": portfolio.blended_risk,
            "as_of": portfolio.as_of,
            "feed_hash": portfolio.feed_hash,
            "drift": dict(self._drift.get(user_id, {})),
            "rebalance_status": self._rebalance_status.get(user_id, "NOOP"),
            "fee_denomination": self._fee_denomination.get(user_id, "AXM"),
            "fee_accrued_wei": self._fee_ledger.accrued(user_id),
            "feed_status": ingest.status if ingest else "UNKNOWN",
        }
        missing = [k for k in response_schema()["required"] if k not in body]
        if missing:
            raise AssertionError(f"response missing schema keys: {missing}")
        return body
