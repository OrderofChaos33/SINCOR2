"""First-class operational metrics for external agent inflow.

Surfaces the numbers that matter for network growth:
- Agents onboarded this week
- Tasks completed
- Treasury inflow attributed to external agents
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


@dataclass
class InflowSnapshot:
    period_start: str
    period_end: str
    agents_onboarded: int = 0
    tasks_completed: int = 0
    external_settlements: int = 0
    treasury_inflow_axm: float = 0.0
    activation_rate: float = 0.0  # fraction of new agents that completed ≥1 paid task
    notes: List[str] = field(default_factory=list)


class OpsMetrics:
    """Lightweight collector. Wire to real registry / settlement / revenue ledger in production."""

    def __init__(self) -> None:
        self._onboard_events: List[Dict] = []
        self._task_completions: List[Dict] = []
        self._external_settlements: List[Dict] = []

    def record_onboard(self, agent_id: str, source: str = "external") -> None:
        self._onboard_events.append({
            "agent_id": agent_id,
            "source": source,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def record_task_completion(self, task_id: str, agent_id: str, external: bool = True) -> None:
        self._task_completions.append({
            "task_id": task_id,
            "agent_id": agent_id,
            "external": external,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def record_settlement(self, amount_axm: float, agent_id: str, platform_fee: float) -> None:
        self._external_settlements.append({
            "agent_id": agent_id,
            "amount_axm": amount_axm,
            "platform_fee": platform_fee,
            "at": datetime.now(timezone.utc).isoformat(),
        })

    def snapshot(self, days: int = 7) -> InflowSnapshot:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        start_iso = start.isoformat()

        onboarded = [e for e in self._onboard_events if e["at"] >= start_iso]
        completed = [e for e in self._task_completions if e["at"] >= start_iso and e.get("external")]
        settlements = [e for e in self._external_settlements if e["at"] >= start_iso]

        agents_with_work = {e["agent_id"] for e in completed}
        new_agent_ids = {e["agent_id"] for e in onboarded}
        activated = len(new_agent_ids & agents_with_work)
        activation_rate = (activated / len(new_agent_ids)) if new_agent_ids else 0.0

        inflow = sum(float(e.get("platform_fee", 0.0)) for e in settlements)

        return InflowSnapshot(
            period_start=start_iso,
            period_end=now.isoformat(),
            agents_onboarded=len(onboarded),
            tasks_completed=len(completed),
            external_settlements=len(settlements),
            treasury_inflow_axm=round(inflow, 4),
            activation_rate=round(activation_rate, 4),
            notes=[
                "Wire this collector to live registry, settlement, and revenue ledger for production numbers.",
                "Expose under /api/marketplace/metrics/inflow for dashboards and CEO briefs.",
            ],
        )
