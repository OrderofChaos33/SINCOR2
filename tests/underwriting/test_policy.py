from datetime import timedelta

from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.policy import Policy
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.types import REASON, iso, utcnow


def _mandate(tmp_path, **kw):
    return MandateService(UnderwriteStore(tmp_path)).issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_payees=["sincor:skill:underwrite.echo"],
        **kw,
    )


def test_mandate_expired_denies_envelope(tmp_path):
    m = _mandate(tmp_path, hours=0.000001)
    m.expires_at = iso(utcnow() - timedelta(seconds=5))
    assert Policy.mandate_live(m) == REASON.DENY_MANDATE_EXPIRED


def test_kill_switch_reason(tmp_path):
    m = _mandate(tmp_path)
    m.killed = True
    assert Policy.kill(m) == REASON.DENY_KILL_SWITCH
