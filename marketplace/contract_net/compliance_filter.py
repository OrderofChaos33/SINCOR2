"""Mandatory compliance pre-filter for Contract-Net task allocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Set

from .types import AgentProfile, TaskSpec


@dataclass(frozen=True)
class ComplianceDecision:
    agent_id: str
    allowed: bool
    reason: str


class ComplianceAttestationFilter:
    """Bitmap-like filter for region policy, zk identity, and sanctions denylist."""

    def __init__(self, sanctioned_wallets: Iterable[str] = ()) -> None:
        self._sanctioned_wallets: Set[str] = {w.lower() for w in sanctioned_wallets if w}

    def set_sanctioned_wallets(self, wallets: Iterable[str]) -> None:
        self._sanctioned_wallets = {w.lower() for w in wallets if w}

    def is_wallet_sanctioned(self, wallet: str) -> bool:
        return wallet.lower() in self._sanctioned_wallets

    def evaluate(self, task: TaskSpec, agent: AgentProfile) -> ComplianceDecision:
        if self.is_wallet_sanctioned(agent.wallet):
            return ComplianceDecision(agent_id=agent.agent_id, allowed=False, reason="sanctioned_wallet")

        allowed_regions = {x.strip().lower() for x in task.allowed_regions if x}
        region = (agent.region or "global").strip().lower()
        if allowed_regions and region not in allowed_regions and "global" not in allowed_regions:
            return ComplianceDecision(agent_id=agent.agent_id, allowed=False, reason="region_restricted")

        if task.require_zk_identity and not (agent.zk_identity_proof or "").strip():
            return ComplianceDecision(agent_id=agent.agent_id, allowed=False, reason="missing_zk_identity")

        return ComplianceDecision(agent_id=agent.agent_id, allowed=True, reason="ok")

    def prefilter(self, task: TaskSpec, agents: Sequence[AgentProfile]) -> List[AgentProfile]:
        approved: List[AgentProfile] = []
        for agent in agents:
            decision = self.evaluate(task, agent)
            if decision.allowed:
                approved.append(agent)
        return approved
