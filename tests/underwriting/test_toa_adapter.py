from datetime import timedelta

from sincor2.underwriting.toa_adapter import FormulaToa, SpendUnderwriter, ToaPort
from sincor2.underwriting.types import IntentMandate, REASON, iso, new_id, utcnow


def _m(**extra):
    now = utcnow()
    d = dict(
        mandate_id=new_id(),
        issued_at=iso(now),
        expires_at=iso(now + timedelta(hours=2)),
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        asset="USDC",
        chain_id=8453,
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    d.update(extra)
    return IntentMandate(**d)


class EmptyToa(ToaPort):
    def run(self, context, objectives=None):
        return {"run_id": "empty", "forecast_paths": 4, "evaluated_paths": 4, "action_plan": []}


def test_toa_empty_paths_denies():
    env = SpendUnderwriter(port=EmptyToa()).propose(
        _m(), requested_usd="5.00", skill_id="underwrite.echo", payee="sincor:skill:underwrite.echo"
    )
    assert env.denied
    assert REASON.DENY_TOA_NO_VIABLE_PATH in env.reason_codes


def test_low_safety_reduces_amount():
    env = SpendUnderwriter(port=FormulaToa()).propose(
        _m(),
        requested_usd="5.00",
        skill_id="underwrite.echo",
        payee="sincor:skill:underwrite.echo",
        extra_context={"error_rate": 0.3, "new_counterparty": True, "values": [0.4, 0.3, 0.2]},
    )
    assert not env.denied
    assert float(env.amount_usd) < 5.00 or env.reason_codes[0].startswith("OK_REDUCED")


def test_high_safety_full_request_capped():
    env = SpendUnderwriter(port=FormulaToa()).propose(
        _m(),
        requested_usd="9.00",
        skill_id="underwrite.echo",
        payee="sincor:skill:underwrite.echo",
        extra_context={"error_rate": 0.0, "values": [0.95, 0.96, 0.97]},
    )
    assert not env.denied
    assert env.amount_usd == "5.000000"
    assert env.reason_codes[0] == REASON.OK_BASELINE
