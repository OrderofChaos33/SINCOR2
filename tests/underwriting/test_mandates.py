from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.store import UnderwriteStore


def test_mandate_issue_unsigned(tmp_path):
    store = UnderwriteStore(tmp_path)
    svc = MandateService(store)
    m = svc.issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    got = store.get_mandate(m.mandate_id)
    assert got is not None
    assert got.agent_id == "E-altair-04"
    assert got.max_notional_usd == "25.00"
    assert svc.active_for("E-altair-04").mandate_id == m.mandate_id
