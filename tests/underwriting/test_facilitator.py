from sincor2.underwriting.envelopes import EnvelopeService
from sincor2.underwriting.facilitator import Facilitator
from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.taps.bridge_mint import BridgeMintTap
from sincor2.underwriting.taps.ledger_sim import LedgerSimTap
from sincor2.underwriting.toa_adapter import SpendUnderwriter
from sincor2.underwriting.types import AuthorizeRequest, REASON


def _stack(tmp_path):
    store = UnderwriteStore(tmp_path)
    tap = LedgerSimTap("100.00")
    mandates = MandateService(store)
    envelopes = EnvelopeService(store, SpendUnderwriter(tap_name="ledger_sim"))
    fac = Facilitator(store, mandates, envelopes, tap)
    m = mandates.issue(
        operator_id="court",
        controller_wallet="0x" + "ab" * 20,
        agent_id="E-altair-04",
        max_notional_usd="25.00",
        max_single_tx_usd="5.00",
        allowed_skills=["underwrite.echo"],
        allowed_payees=["sincor:skill:underwrite.echo"],
    )
    env = envelopes.request(
        m,
        requested_usd="5.00",
        skill_id="underwrite.echo",
        payee="sincor:skill:underwrite.echo",
    )
    return store, mandates, envelopes, fac, m, env


def test_facilitator_decrements(tmp_path):
    _, _, _, fac, _, env = _stack(tmp_path)
    r = fac.authorize(AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "0.05", "underwrite.echo"))
    assert r.allowed
    assert r.remaining_usd == "4.950000"


def test_facilitator_unknown_payee(tmp_path):
    _, _, _, fac, _, env = _stack(tmp_path)
    r = fac.authorize(AuthorizeRequest(env.envelope_id, "0xevil", "0.05", "underwrite.echo"))
    assert not r.allowed
    assert r.reason == REASON.DENY_UNKNOWN_PAYEE


def test_facilitator_over_remaining(tmp_path):
    _, _, _, fac, _, env = _stack(tmp_path)
    r = fac.authorize(AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "9.00", "underwrite.echo"))
    assert not r.allowed
    assert r.reason == REASON.DENY_CAP_EXCEEDED


def test_kill_switch_blocks_authorize(tmp_path):
    _, mandates, _, fac, m, env = _stack(tmp_path)
    mandates.revoke(m.mandate_id, "operator")
    r = fac.authorize(AuthorizeRequest(env.envelope_id, "sincor:skill:underwrite.echo", "0.05", "underwrite.echo"))
    assert not r.allowed
    assert r.reason == REASON.DENY_KILL_SWITCH


def test_audit_is_append_only(tmp_path):
    store, *_ = _stack(tmp_path)
    first = list(store.iter_audit())
    assert first
    store.audit("spend.attempted", "E-altair-04", {"n": 2})
    lines = list(store.iter_audit())
    assert len(lines) == len(first) + 1
    assert lines[0]["event_id"] == first[0]["event_id"]


def test_bridge_tap_is_stub():
    tap = BridgeMintTap()
    rec = tap.transfer(from_agent="a", to_payee="b", amount_usd="1.00", asset="USDC", meta={})
    assert rec.ok is False
    assert tap.describe()["status"] == "stub"
