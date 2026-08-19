"""Agent-callable escrow + dispute skeleton for micro-tasks.

Keeps settlement path intact. Escrow is an optional layer for higher-trust or
higher-value tasks. Automatic release on successful outcome attestation;
dispute path is agent-callable and time-bounded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import uuid4


@dataclass
class EscrowHold:
    escrow_id: str
    task_id: str
    payer: str
    payee: str
    amount: str
    token_symbol: str = "AXM"
    status: str = "held"  # held | released | refunded | disputed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    release_after: Optional[str] = None
    dispute_reason: Optional[str] = None


class EscrowCoordinator:
    """Minimal in-memory escrow. Production will bind to settlement + on-chain holds."""

    def __init__(self, auto_release_hours: int = 24) -> None:
        self.holds: Dict[str, EscrowHold] = {}
        self.auto_release_hours = auto_release_hours

    def create_hold(
        self,
        task_id: str,
        payer: str,
        payee: str,
        amount: Decimal,
        token_symbol: str = "AXM",
    ) -> EscrowHold:
        hold = EscrowHold(
            escrow_id=f"esc-{uuid4().hex[:12]}",
            task_id=task_id,
            payer=payer,
            payee=payee,
            amount=str(amount),
            token_symbol=token_symbol.upper(),
            release_after=(datetime.now(timezone.utc) + timedelta(hours=self.auto_release_hours)).isoformat(),
        )
        self.holds[hold.escrow_id] = hold
        return hold

    def release(self, escrow_id: str) -> EscrowHold:
        hold = self.holds[escrow_id]
        if hold.status != "held":
            raise ValueError(f"Escrow {escrow_id} is {hold.status}")
        hold.status = "released"
        return hold

    def refund(self, escrow_id: str) -> EscrowHold:
        hold = self.holds[escrow_id]
        if hold.status != "held":
            raise ValueError(f"Escrow {escrow_id} is {hold.status}")
        hold.status = "refunded"
        return hold

    def open_dispute(self, escrow_id: str, reason: str) -> EscrowHold:
        hold = self.holds[escrow_id]
        if hold.status != "held":
            raise ValueError(f"Escrow {escrow_id} is {hold.status}")
        hold.status = "disputed"
        hold.dispute_reason = reason
        return hold

    def list_for_agent(self, agent_wallet: str) -> List[Dict]:
        return [
            asdict(h)
            for h in self.holds.values()
            if h.payer == agent_wallet or h.payee == agent_wallet
        ]
