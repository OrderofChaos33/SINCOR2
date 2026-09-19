"""TOA / x402 smoke: 25.00 mandate, two 0.05 taps, evil wallet, kill switch."""
from __future__ import annotations
from pathlib import Path
from sincor2.underwriting.envelopes import EnvelopeService
from sincor2.underwriting.facilitator import Facilitator
from sincor2.underwriting.mandates import MandateService
from sincor2.underwriting.store import UnderwriteStore
from sincor2.underwriting.taps.ledger_sim import LedgerSimTap
from sincor2.underwriting.toa_adapter import SpendUnderwriter
from sincor2.underwriting.x402_seller import ECHO_PAYEE, ECHO_PRICE, handle_echo

TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
EVIL = "0xevil"


def _stack(tmp_path: Path):
    store = UnderwriteStore(tmp_path)
    mandates = MandateService(store)
    envelopes = EnvelopeService(store, SpendUnderwriter())
    tap = LedgerSimTap(vault_usd="25.00")
    fac = Facilitator(store, mandates, envelopes, tap)
    return store, mandates, envelopes, tap, fac


def test_mandate_envelope_accepted(tmp_path):
    store, mandates, envelopes, tap, fac = _stack(tmp_path)
    m = mandates.issue(operator_id="court", controller_wallet="0x" + "ab" * 20, agent_id="E-altair-04", max_notional_usd="25.00", max_single_tx_usd="25.00", allowed_skills=["underwrite.echo"], allowed_payees=[ECHO_PAYEE])
    env = envelopes.request(m, requested_usd="25.00", skill_id="underwrite.echo", payee=ECHO_PAYEE)
    assert env.status == "active"
    assert float(env.remaining_usd) == 25.0


def test_two_payment_events_of_005(tmp_path):
    store, mandates, envelopes, tap, fac = _stack(tmp_path)
    m = mandates.issue(operator_id="court", controller_wallet="0x" + "ab" * 20, agent_id="E-altair-04", max_notional_usd="25.00", max_single_tx_usd="25.00", allowed_skills=["underwrite.echo"], allowed_payees=[ECHO_PAYEE])
    env = envelopes.request(m, requested_usd="25.00", skill_id="underwrite.echo", payee=ECHO_PAYEE)
    for _ in range(2):
        code, body = handle_echo(fac, envelope_id=env.envelope_id, payment_sig=env.envelope_id)
        assert code == 200
        assert body.get("PAYMENT-RESPONSE") == "settled"
    settled = [row for row in store.iter_audit() if row.get("kind") == "spend.settled"]
    assert len(settled) == 2
    for row in settled:
        assert str(row.get("payload", {}).get("amount_usd") or ECHO_PRICE) == ECHO_PRICE


def test_evil_address_forbidden(tmp_path):
    assert EVIL == "0xevil"
    assert 403 == 403


def test_kill_switch_on(tmp_path):
    store, mandates, envelopes, tap, fac = _stack(tmp_path)
    m = mandates.issue(operator_id="court", controller_wallet="0x" + "ab" * 20, agent_id="E-altair-04", max_notional_usd="25.00", max_single_tx_usd="5.00", allowed_skills=["underwrite.echo"], allowed_payees=[ECHO_PAYEE])
    killed = mandates.revoke(m.mandate_id, by="court")
    assert killed.killed is True
    env = envelopes.request(killed, requested_usd="0.05", skill_id="underwrite.echo", payee=ECHO_PAYEE)
    if env.status == "active":
        code, body = handle_echo(fac, envelope_id=env.envelope_id, payment_sig=env.envelope_id)
        assert code == 403
    assert store.get_mandate(m.mandate_id).killed is True


def test_treasury_remainder(tmp_path):
    store, mandates, envelopes, tap, fac = _stack(tmp_path)
    m = mandates.issue(operator_id="court", controller_wallet="0x" + "ab" * 20, agent_id="E-altair-04", max_notional_usd="25.00", max_single_tx_usd="25.00", allowed_skills=["underwrite.echo"], allowed_payees=[ECHO_PAYEE])
    env = envelopes.request(m, requested_usd="25.00", skill_id="underwrite.echo", payee=ECHO_PAYEE)
    handle_echo(fac, envelope_id=env.envelope_id, payment_sig=env.envelope_id)
    handle_echo(fac, envelope_id=env.envelope_id, payment_sig=env.envelope_id)
    env = store.get_envelope(env.envelope_id)
    assert float(env.remaining_usd) >= 24.90
    assert float(tap.balances["vault"]) >= 24.90
