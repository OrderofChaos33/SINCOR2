from sincor2.underwriting.envelopes import EnvelopeService
from sincor2.underwriting.facilitator import Facilitator
from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.taps.ledger_sim import LedgerSimTap
from sincor2.underwriting.toa_adapter import SpendUnderwriter
from sincor2.underwriting.x402_seller import handle_echo


def test_x402_without_header_is_402(tmp_path):
    store = UnderwriteStore(tmp_path)
    fac = Facilitator(store, MandateService(store), EnvelopeService(store, SpendUnderwriter()), LedgerSimTap())
    code, body = handle_echo(fac, envelope_id=None, payment_sig=None)
    assert code == 402
    assert body["envelope_required"] is True


def test_x402_with_envelope_is_200(tmp_path):
    store = UnderwriteStore(tmp_path)
    mandates = MandateService(store)
    envelopes = EnvelopeService(store, SpendUnderwriter())
    fac = Facilitator(store, mandates, envelopes, LedgerSimTap())
    m = mandates.issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    env = envelopes.request(m, requested_usd="5.00", skill_id="underwrite.echo", payee="sincor:skill:underwrite.echo")
    code, body = handle_echo(fac, envelope_id=env.envelope_id, payment_sig=env.envelope_id)
    assert code == 200
    assert body["remaining_usd"] == "4.950000"
