from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .types import (
    REASON,
    IntentMandate,
    SpendEnvelope,
    iso,
    money,
    money_str,
    new_id,
    utcnow,
)

SPEND_OBJECTIVE_WEIGHTS = {
    "spend_safety": 0.45,
    "risk": 0.25,
    "compliance": 0.15,
    "treasury_inflow": 0.10,
    "timeline": 0.05,
}


def spend_safety_score(ctx: dict[str, Any]) -> float:
    score = 1.0
    error_rate = float(ctx.get("error_rate") or 0.0)
    requested = float(ctx.get("requested_usd") or 0.0)
    if error_rate > 0.25:
        score -= 0.4
    if ctx.get("new_counterparty") and requested > 10:
        score -= 0.3
    if int(ctx.get("recent_denies") or 0) >= 3:
        score -= 0.2
    if ctx.get("kill_switch"):
        score -= 0.5
    return max(0.0, min(1.0, score))


@dataclass
class ToaResult:
    run_id: str
    paths_considered: int
    paths_viable: int
    scenario_id: str | None
    composite: float | None
    safety: float
    rationale: str
    action_plan: list[dict[str, Any]]


class ToaPort:
    def run(self, context: dict[str, Any], objectives: dict[str, float] | None = None) -> dict[str, Any]:
        raise NotImplementedError


class FormulaToa(ToaPort):
    def run(self, context: dict[str, Any], objectives: dict[str, float] | None = None) -> dict[str, Any]:
        safety = spend_safety_score(context)
        plan = []
        if safety >= 0.4:
            plan = [{
                "rank": 1,
                "scenario_id": "formula-baseline",
                "composite_score": safety,
                "utility_score": safety,
                "probability": 1.0,
                "objective_breakdown": {"spend_safety": safety},
                "rationale": f"formula spend_safety={safety:.3f}",
            }]
        return {
            "run_id": new_id(),
            "forecast_paths": 3,
            "evaluated_paths": 3 if plan else 0,
            "action_plan": plan,
        }


class OrchestratorToa(ToaPort):
    def __init__(self, orchestrator: Any) -> None:
        self.orch = orchestrator
        try:
            self.orch.register_objective("spend_safety", lambda path, ctx=None: spend_safety_score(ctx or {}))
        except Exception:
            pass

    def run(self, context: dict[str, Any], objectives: dict[str, float] | None = None) -> dict[str, Any]:
        return self.orch.run(context=context, objectives=objectives or SPEND_OBJECTIVE_WEIGHTS)


class SpendUnderwriter:
    def __init__(self, port: ToaPort | None = None, tap_name: str = "ledger_sim") -> None:
        self.port = port or FormulaToa()
        self.tap_name = tap_name

    def propose(
        self,
        mandate: IntentMandate,
        *,
        requested_usd: str,
        skill_id: str,
        payee: str,
        extra_context: dict[str, Any] | None = None,
    ) -> SpendEnvelope:
        ctx = {
            "values": extra_context.get("values") if extra_context else [0.9, 0.88, 0.91],
            "horizon": 6,
            "agent_id": mandate.agent_id,
            "requested_usd": requested_usd,
            "skill_id": skill_id,
            "payee": payee,
            "recent_denies": (extra_context or {}).get("recent_denies", 0),
            "recent_settles": (extra_context or {}).get("recent_settles", 0),
            "error_rate": (extra_context or {}).get("error_rate", 0.0),
            "new_counterparty": (extra_context or {}).get("new_counterparty", False),
            "kill_switch": mandate.killed,
        }
        if extra_context:
            ctx.update({k: v for k, v in extra_context.items() if k not in ctx or k == "values"})

        try:
            raw = self.port.run(ctx, SPEND_OBJECTIVE_WEIGHTS)
        except Exception as exc:
            return self._denied(mandate, requested_usd, skill_id, payee, REASON.DENY_TOA_NO_VIABLE_PATH, str(exc))

        plan = raw.get("action_plan") or []
        toa = ToaResult(
            run_id=str(raw.get("run_id") or new_id()),
            paths_considered=int(raw.get("forecast_paths") or 0),
            paths_viable=len(plan),
            scenario_id=plan[0].get("scenario_id") if plan else None,
            composite=plan[0].get("composite_score") if plan else None,
            safety=float((plan[0].get("objective_breakdown") or {}).get("spend_safety") or spend_safety_score(ctx)) if plan else spend_safety_score(ctx),
            rationale=plan[0].get("rationale", "") if plan else "no viable path",
            action_plan=plan,
        )
        if not plan:
            return self._denied(mandate, requested_usd, skill_id, payee, REASON.DENY_TOA_NO_VIABLE_PATH, toa.rationale, toa)

        safety = toa.safety
        if safety < 0.4:
            return self._denied(mandate, requested_usd, skill_id, payee, REASON.DENY_PATH_UNSAFE, toa.rationale, toa)

        requested = money(requested_usd)
        cap = money(mandate.max_single_tx_usd)
        notional = money(mandate.max_notional_usd)
        if safety < 0.7:
            amount = min(requested, cap, money(notional) * money("0.4"))
            reason = REASON.OK_REDUCED_COUNTERPARTY if ctx.get("new_counterparty") else REASON.OK_REDUCED_DRIFT
            ttl = 20 * 60
            if float(ctx.get("error_rate") or 0) > 0.2:
                ttl = 15 * 60
        else:
            amount = min(requested, cap)
            reason = REASON.OK_BASELINE
            ttl = 45 * 60

        now = utcnow()
        return SpendEnvelope(
            envelope_id=new_id(),
            mandate_id=mandate.mandate_id,
            agent_id=mandate.agent_id,
            issued_at=iso(now),
            expires_at=iso(now + timedelta(seconds=ttl)),
            status="active",
            asset=mandate.asset,
            chain_id=mandate.chain_id,
            amount_usd=money_str(amount),
            remaining_usd=money_str(amount),
            ttl_seconds=ttl,
            reason_codes=[reason],
            tap=self.tap_name,
            allowlist_payees=list(mandate.allowed_payees),
            allowlist_skills=list(mandate.allowed_skills) or [skill_id],
            max_tx_usd=mandate.max_single_tx_usd,
            toa_run_id=toa.run_id,
            toa_paths_considered=toa.paths_considered,
            toa_paths_viable=toa.paths_viable,
            toa_scenario_id=toa.scenario_id,
            toa_composite=toa.composite,
            toa_risk=1.0 - safety,
            toa_rationale=toa.rationale,
            denied=False,
        )

    def _denied(
        self,
        mandate: IntentMandate,
        requested_usd: str,
        skill_id: str,
        payee: str,
        reason: str,
        rationale: str,
        toa: ToaResult | None = None,
    ) -> SpendEnvelope:
        now = utcnow()
        return SpendEnvelope(
            envelope_id=new_id(),
            mandate_id=mandate.mandate_id,
            agent_id=mandate.agent_id,
            issued_at=iso(now),
            expires_at=iso(now),
            status="denied",
            asset=mandate.asset,
            chain_id=mandate.chain_id,
            amount_usd="0",
            remaining_usd="0",
            ttl_seconds=0,
            reason_codes=[reason],
            tap=self.tap_name,
            allowlist_payees=list(mandate.allowed_payees),
            allowlist_skills=list(mandate.allowed_skills) or [skill_id],
            max_tx_usd=mandate.max_single_tx_usd,
            toa_run_id=toa.run_id if toa else None,
            toa_paths_considered=toa.paths_considered if toa else 0,
            toa_paths_viable=toa.paths_viable if toa else 0,
            toa_rationale=rationale,
            denied=True,
        )
