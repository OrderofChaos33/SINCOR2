"""Regression tests for W-156: the XChainEnvelope consumed-nonce ledger
must be durable — a process restart must not reopen a spent nonce.

Before the fix, ``EnvelopeValidator`` kept spent nonces in a plain
in-process set, so a fresh validator instance (simulated restart)
accepted the identical signed envelope a second time
(PoC: poc50_xchain_restart_replay.py).
"""

import json

import pytest

from sincor2.xchain_envelope import (
    NONCE_LEDGER_ENV_VAR,
    EnvelopeValidator,
    NonceLedgerError,
    NonceReuseError,
    XChainEnvelope,
    sign_envelope,
)

try:
    from eth_account import Account

    HAVE_ETH_ACCOUNT = True
except Exception:  # pragma: no cover
    HAVE_ETH_ACCOUNT = False

pytestmark = pytest.mark.skipif(not HAVE_ETH_ACCOUNT, reason="eth-account required")

BASE = 8453
SEPOLIA = 11155111
FAR_FUTURE = 4_000_000_000

# Deterministic test key (never used anywhere real).
_PRIVKEY = "0x" + "42" * 32
SIGNER = Account.from_key(_PRIVKEY).address


def make_envelope(**overrides):
    params = {
        "payload": {"task": "pay 100", "amount": 100},
        "origin_chain_id": SEPOLIA,
        "dest_chain_id": BASE,
        "signer": SIGNER,
        "nonce": 42,
        "deadline": FAR_FUTURE,
    }
    params.update(overrides)
    return XChainEnvelope(**params)


def test_replay_after_restart_rejected(tmp_path):
    """The W-156 PoC shape: validate on instance 1, replay the identical
    envelope on a fresh instance 2 over the same ledger file (restart)."""
    ledger = str(tmp_path / "nonces.json")
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)

    v1 = EnvelopeValidator(BASE, ledger_path=ledger)
    assert v1.validate(env, sig).lower() == SIGNER.lower()

    v2 = EnvelopeValidator(BASE, ledger_path=ledger)  # simulated restart
    assert v2.is_nonce_consumed(SIGNER, 42)
    with pytest.raises(NonceReuseError):
        v2.validate(env, sig)


def test_replay_after_restart_rejected_via_env_var(tmp_path, monkeypatch):
    """Production wiring via SINCOR_XCHAIN_NONCE_LEDGER_PATH: two bare
    validators in sequence share the durable ledger."""
    ledger = str(tmp_path / "nonces.json")
    monkeypatch.setenv(NONCE_LEDGER_ENV_VAR, ledger)
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)

    EnvelopeValidator(BASE).validate(env, sig)
    with pytest.raises(NonceReuseError):
        EnvelopeValidator(BASE).validate(env, sig)


def test_nonce_is_on_disk_before_validate_returns(tmp_path):
    """Crash-ordering: there is no flush/close step, so the spent nonce
    must already be durable at the moment validate() returns — a crash
    immediately afterwards cannot reopen it."""
    ledger = tmp_path / "nonces.json"
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)

    EnvelopeValidator(BASE, ledger_path=str(ledger)).validate(env, sig)

    raw = json.loads(ledger.read_text(encoding="utf-8"))
    assert raw["chain_id"] == BASE
    assert raw["nonces"][SIGNER.lower()] == [42]
    assert raw["envelope_ids"] == [env.envelope_id]


def test_peer_instance_sees_spent_nonce_without_restart(tmp_path):
    """A second live validator over the same file rejects a nonce spent
    by the first after the second was constructed (cross-process
    freshness via re-read before the freshness check)."""
    ledger = str(tmp_path / "nonces.json")
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)

    v1 = EnvelopeValidator(BASE, ledger_path=ledger)
    v2 = EnvelopeValidator(BASE, ledger_path=ledger)
    v1.validate(env, sig)
    with pytest.raises(NonceReuseError):
        v2.validate(env, sig)


def test_in_process_replay_still_rejected_with_ledger(tmp_path):
    ledger = str(tmp_path / "nonces.json")
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)
    v = EnvelopeValidator(BASE, ledger_path=ledger)
    v.validate(env, sig)
    with pytest.raises(NonceReuseError):
        v.validate(env, sig)
    assert v.consumed_count() == 1


def test_mark_consumed_false_still_leaves_nonce_reusable(tmp_path):
    """Documented pre-check semantics are unchanged by durability."""
    ledger = str(tmp_path / "nonces.json")
    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)
    v = EnvelopeValidator(BASE, ledger_path=ledger)
    v.validate(env, sig, mark_consumed=False)
    assert not v.is_nonce_consumed(SIGNER, 42)
    v.validate(env, sig)  # the real consume still works afterwards


def test_corrupt_ledger_fails_closed(tmp_path):
    """An unreadable/corrupt ledger must never be treated as empty."""
    ledger = tmp_path / "nonces.json"
    ledger.write_text("{not json", encoding="utf-8")
    with pytest.raises(NonceLedgerError):
        EnvelopeValidator(BASE, ledger_path=str(ledger))


def test_wrong_chain_ledger_fails_closed(tmp_path):
    ledger = tmp_path / "nonces.json"
    ledger.write_text(
        json.dumps(
            {"version": 1, "chain_id": 1, "nonces": {}, "envelope_ids": []}
        ),
        encoding="utf-8",
    )
    with pytest.raises(NonceLedgerError):
        EnvelopeValidator(BASE, ledger_path=str(ledger))


def test_unwritable_ledger_fails_closed_and_rolls_back(tmp_path):
    """If the spent nonce cannot be persisted, validate() must raise and
    the nonce must NOT be left marked consumed in memory — the envelope
    was never safely validated, so nothing may disburse against it."""
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    ledger = str(blocker / "nonces.json")

    env = make_envelope()
    sig = sign_envelope(env, _PRIVKEY)
    v = EnvelopeValidator(BASE, ledger_path=ledger)
    with pytest.raises(NonceLedgerError):
        v.validate(env, sig)
    assert not v.is_nonce_consumed(SIGNER, 42)


def test_fresh_ledger_file_is_created_on_first_use(tmp_path):
    ledger = tmp_path / "nested" / "nonces.json"
    v = EnvelopeValidator(BASE, ledger_path=str(ledger))
    assert v.consumed_count() == 0
    env = make_envelope()
    v.validate(env, sign_envelope(env, _PRIVKEY))
    assert ledger.exists()
    assert v.consumed_count() == 1
