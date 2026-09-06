from sincor2.underwriting.envelopes import EnvelopeService
from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.toa_adapter import SpendUnderwriter


def test_request_happy(tmp_path):
    store = UnderwriteStore(tmp_path)
    m = MandateService(store).issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    env = EnvelopeService(store, SpendUnderwriter()).request(
        m, requested_usd="5.00", skill_id="underwrite.echo", payee="sincor:skill:underwrite.echo"
    )
    assert env.status == "active"
    assert env.toa_run_id
    assert not env.denied
