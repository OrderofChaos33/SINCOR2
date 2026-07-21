#!/usr/bin/env python3
"""
Yield Optimizer Agent — DeFi vertical for SINCOR2.

Bridges the Polyclaw core agent and the SharedLiquidityVault:
surfaces high-EV drawdown opportunities (Polyclaw positions, lending
loops), validates every proposal through an Auditor gate before anything
is returned, and routes settlements back into calibration + observation.

Task types (TaskInput.task_type):
    evaluate_yield    — score a market/loop against a vault lane
    propose_drawdown  — Auditor-gated drawdown proposal with calldata
    settle_position   — settleUp callback: calldata + observer feed
    vault_status      — lane accounting snapshot + invariant check
    loop_scan         — rank lending-loop spreads across lanes

Agency Kernel integration: standard VerticalAgent surface (run/execute/
get_agent_card); register alongside TradingAgent in the kernel registry.
A2A: see yield_optimizer_card.json.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

from ..agent import VerticalAgent
from .polyclaw.core_agent import PolyclawCoreAgent, YieldOpportunity
from .polyclaw.observer_improver import ObserverImproverAgent
from .polyclaw.vault_client import VaultClient, VaultState
from .schemas import TaskInput, TaskOutput

logger = logging.getLogger(__name__)

BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
ALLOWED_TOKENS = {BASE_USDC.lower()}


class AuditorRejection(Exception):
    """Raised when the Auditor gate refuses a proposal."""


def default_auditor(opp: YieldOpportunity, state: VaultState) -> None:
    """
    Built-in Auditor validation gate. Every drawdown proposal passes here
    before it can leave the agent. Swap in the Auditor swarm via the
    `auditor` constructor arg for multi-source review.
    """
    available = state.available_draw()
    if opp.expected_value <= 0:
        raise AuditorRejection(f"non-positive EV: {opp.expected_value}")
    if opp.drawdown_amount <= 0:
        raise AuditorRejection("zero/negative drawdown")
    if opp.drawdown_amount > available:
        raise AuditorRejection(
            f"drawdown {opp.drawdown_amount} exceeds availableDraw {available}"
        )
    if opp.token.lower() not in ALLOWED_TOKENS:
        raise AuditorRejection(f"token not allowlisted: {opp.token}")
    if not opp.drawdown_calldata.startswith("0x"):
        raise AuditorRejection("malformed calldata")


class YieldOptimizerAgent(VerticalAgent):
    name = "yield_optimizer_agent"
    version = "0.1.0"
    description = (
        "DeFi yield optimization: SharedLiquidityVault drawdown sizing for "
        "high-EV Polyclaw positions and lending loops, Auditor-gated."
    )
    capabilities = [
        "yield_evaluation",
        "drawdown_proposal",
        "settlement_processing",
        "vault_status",
        "loop_scan",
    ]
    tags = ["defi", "yield", "vault", "polymarket", "base"]

    def __init__(
        self,
        vault_client: Optional[VaultClient] = None,
        auditor: Optional[Callable[[YieldOpportunity, VaultState], None]] = None,
    ):
        super().__init__()
        self.vault = vault_client or VaultClient()
        self.core = PolyclawCoreAgent(name="yield-core", vault_client=self.vault)
        self.observer = ObserverImproverAgent()
        self.auditor = auditor or default_auditor

    # ------------------------------------------------------------------
    # VerticalAgent interface
    # ------------------------------------------------------------------
    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_input = TaskInput.model_validate(task)
        t = task_input.task_type
        p = task_input.payload
        cid = task_input.correlation_id

        if t == "evaluate_yield":
            return self._ok(self._evaluate(p, gated=False), cid)
        if t == "propose_drawdown":
            return self._ok(self._evaluate(p, gated=True), cid)
        if t == "settle_position":
            return self._ok(self._settle(p), cid)
        if t == "vault_status":
            return self._ok(self._status(p), cid)
        if t == "loop_scan":
            return self._ok(self._loop_scan(p), cid)

        return TaskOutput(
            status="error", result={},
            error=f"Unsupported yield task: {t}", correlation_id=cid,
        ).model_dump()

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------
    def _evaluate(self, p: Dict[str, Any], gated: bool) -> Dict[str, Any]:
        state = self._state_from_payload(p)
        opp = self.core.evaluate_yield_opportunity(
            market=p.get("market"),
            model_probability=p.get("model_probability"),
            vault_state=state,
            bankroll=float(p.get("bankroll", 0.0)),
            lending_loop_apr=float(p.get("lending_loop_apr", 0.0)),
            funding_cost_apr=float(p.get("funding_cost_apr", 0.0)),
        )
        if opp is None:
            return {"opportunity": None, "reason": "no path clears the EV bar"}

        result = asdict(opp)
        if gated:
            try:
                self.auditor(opp, state)
            except AuditorRejection as exc:
                logger.warning("[AUDITOR] rejected: %s", exc)
                return {"opportunity": None, "reason": f"auditor_rejected: {exc}"}
            result["auditor"] = "approved"
        return {"opportunity": result}

    def _settle(self, p: Dict[str, Any]) -> Dict[str, Any]:
        state = self._state_from_payload(p)
        rec = self.core.settle_up(
            state,
            principal=float(p["principal"]),
            fee=float(p.get("fee", 0.0)),
            was_correct=bool(p.get("was_correct", True)),
            observer=self.observer,
        )
        return {
            "settlement": asdict(rec),
            "win_rate_ema": self.core.win_rate_ema,
            "observer": self.observer.get_health_report(),
        }

    def _status(self, p: Dict[str, Any]) -> Dict[str, Any]:
        state = self._state_from_payload(p)
        invariant = self.vault.check_invariant()  # None in sim mode
        return {
            "lane": asdict(state),
            "available_draw": state.available_draw(),
            "onchain_invariant": invariant,
            "agent": self.core.get_status(),
        }

    def _loop_scan(self, p: Dict[str, Any]) -> Dict[str, Any]:
        lanes: List[Dict[str, Any]] = p.get("lanes", [])
        funding = float(p.get("funding_cost_apr", 0.0))
        ranked = []
        for lane in lanes:
            state = self._state_from_payload(lane)
            opp = self.core.evaluate_yield_opportunity(
                market=None, model_probability=None, vault_state=state,
                bankroll=0.0,
                lending_loop_apr=float(lane.get("lending_loop_apr", 0.0)),
                funding_cost_apr=funding,
            )
            if opp is not None:
                try:
                    self.auditor(opp, state)
                except AuditorRejection:
                    continue
                ranked.append(asdict(opp))
        ranked.sort(key=lambda o: o["expected_value"], reverse=True)
        return {"loops": ranked, "count": len(ranked)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _state_from_payload(self, p: Dict[str, Any]) -> VaultState:
        lane = p.get("lane", p)
        return VaultState(
            strategy_id=int(lane.get("strategy_id", 0)),
            lp=lane["lp"],
            token=lane.get("token", BASE_USDC),
            virtual_alloc=float(lane.get("virtual_alloc", 0.0)),
            outstanding=float(lane.get("outstanding", 0.0)),
            strategy_outstanding=float(lane.get("strategy_outstanding", 0.0)),
            cap=float(lane.get("cap", 0.0)),
            real_balance=float(lane.get("real_balance", 0.0)),
            lp_outstanding_total=float(lane.get("lp_outstanding_total", 0.0)),
            token_decimals=int(lane.get("token_decimals", 6)),
        )

    @staticmethod
    def _ok(result: Dict[str, Any], cid: Optional[str]) -> Dict[str, Any]:
        return TaskOutput(status="success", result=result, correlation_id=cid).model_dump()
