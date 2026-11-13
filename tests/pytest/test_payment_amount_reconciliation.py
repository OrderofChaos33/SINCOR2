"""G2.5 — payment amounts come from the chain, never from caller claims.

Covers:
- `_sum_transfer_value` log parsing on both PaymentVerifier classes
- `_validate_transfer_log` still delegates correctly
- `verified_amount_wei` control flow (dev bypass, bad hash, simulated,
  failed receipt, RPC outage)
- `_reconcile_axm_paid` claim-vs-chain correction on message/send:
  inflated claims corrected down, under-claims corrected up,
  unverifiable amounts keep the claim, RPC outage fails closed (-32001)
"""

import json
import time
import uuid

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

import sincor2.a2a_integration as ai
from sincor2.payment_verifier import PaymentVerifier as BoundVerifier

AXM = ai.AXIOM_CONTRACT
TREASURY = ai.TREASURY_WALLET
TOPIC = ai.PaymentVerifier._TRANSFER_TOPIC
OTHER_TOKEN = "0x" + "ab" * 20
OTHER_WALLET = "0x" + "cd" * 20


def _pad_addr(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:].lower()


def _transfer_log(to_addr: str, value_wei: int, contract: str = AXM) -> dict:
    return {
        "address": contract,
        "topics": [TOPIC, _pad_addr("0x" + "11" * 20), _pad_addr(to_addr)],
        "data": hex(value_wei),
    }


def _non_transfer_log() -> dict:
    return {
        "address": AXM,
        "topics": ["0x" + "ee" * 32, _pad_addr("0x" + "11" * 20), _pad_addr(TREASURY)],
        "data": hex(10 ** 18),
    }


# ── _sum_transfer_value (both verifier classes) ──────────────────────────────

@pytest.mark.parametrize("verifier", [ai.PaymentVerifier, BoundVerifier])
def test_sum_transfer_value_sums_qualifying_logs(verifier):
    logs = [
        _transfer_log(TREASURY, 3 * 10**17),
        _transfer_log(TREASURY, 2 * 10**17),
        _transfer_log(OTHER_WALLET, 9 * 10**18),   # wrong `to` — ignored
        _transfer_log(TREASURY, 7 * 10**18, contract=OTHER_TOKEN),  # wrong token
        _non_transfer_log(),                        # wrong topic
        {"address": AXM, "topics": [TOPIC], "data": hex(10**18)},  # <3 topics
        {"address": AXM, "topics": [TOPIC, _pad_addr("0x" + "11" * 20),
                                    _pad_addr(TREASURY)], "data": "not-hex"},
    ]
    assert verifier._sum_transfer_value(logs, TREASURY) == 5 * 10**17


@pytest.mark.parametrize("verifier", [ai.PaymentVerifier, BoundVerifier])
def test_sum_transfer_value_empty_is_zero(verifier):
    assert verifier._sum_transfer_value([], TREASURY) == 0


@pytest.mark.parametrize("verifier", [ai.PaymentVerifier, BoundVerifier])
def test_validate_transfer_log_delegates(verifier):
    logs = [_transfer_log(TREASURY, 10**18)]
    assert verifier._validate_transfer_log(logs, TREASURY, 10**18) is True
    assert verifier._validate_transfer_log(logs, TREASURY, 10**18 + 1) is False
    assert verifier._validate_transfer_log([], TREASURY, 1) is False


# ── verified_amount_wei control flow ─────────────────────────────────────────

def test_bound_verifier_dev_bypass_returns_none(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "test")
    assert BoundVerifier.verified_amount_wei("0x" + "aa" * 32) is None


def test_bound_verifier_bad_hash_and_simulated_in_prod(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    assert BoundVerifier.verified_amount_wei("not-a-hash") is None
    assert BoundVerifier.verified_amount_wei("0xSIMULATED-123") is None
    assert ai.PaymentVerifier.verified_amount_wei("not-a-hash") is None


def _receipt(logs, status="0x1"):
    return {"status": status, "logs": logs}


def test_bound_verifier_reads_amount_from_receipt(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    logs = [_transfer_log(TREASURY, 4 * 10**17), _transfer_log(TREASURY, 10**17)]
    monkeypatch.setattr(
        BoundVerifier, "_fetch_receipt",
        classmethod(lambda cls, url, tx: _receipt(logs)),
    )
    amount = BoundVerifier.verified_amount_wei("0x" + "bb" * 32)
    assert amount == 5 * 10**17


def test_bound_verifier_failed_receipt_returns_none(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setattr(
        BoundVerifier, "_fetch_receipt",
        classmethod(lambda cls, url, tx: _receipt([], status="0x0")),
    )
    assert BoundVerifier.verified_amount_wei("0x" + "cc" * 32) is None


def test_bound_verifier_rpc_outage_raises(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setenv("BASE_RPC_URL", "https://127.0.0.1:1/")
    monkeypatch.setattr(BoundVerifier, "_PUBLIC_FALLBACKS", ())
    def _boom(cls, url, tx):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(BoundVerifier, "_fetch_receipt", classmethod(_boom))
    monkeypatch.setattr(BoundVerifier, "_BACKOFF_BASE", 0)
    with pytest.raises(BoundVerifier.PaymentRpcError):
        BoundVerifier.verified_amount_wei("0x" + "dd" * 32)


def test_lightweight_verifier_dev_bypass_returns_none(monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "test")
    assert ai.PaymentVerifier.verified_amount_wei("0x" + "aa" * 32) is None


# ── _reconcile_axm_paid ──────────────────────────────────────────────────────

def test_reconcile_no_tx_keeps_claim():
    amount, err = ai._reconcile_axm_paid(None, 123, rpc_id=1)
    assert (amount, err) == (123, None)


def test_reconcile_none_chain_amount_keeps_claim(monkeypatch):
    monkeypatch.setattr(
        ai.PaymentVerifier, "verified_amount_wei",
        classmethod(lambda cls, tx, expected_to=ai.TREASURY_WALLET: None),
    )
    amount, err = ai._reconcile_axm_paid("0x" + "ee" * 32, 123, rpc_id=1)
    assert (amount, err) == (123, None)


def test_reconcile_rpc_outage_fails_closed(monkeypatch):
    def _boom(cls, tx, expected_to=ai.TREASURY_WALLET):
        raise ai.PaymentVerifier.PaymentRpcError("down")
    monkeypatch.setattr(ai.PaymentVerifier, "verified_amount_wei", classmethod(_boom))
    amount, err = ai._reconcile_axm_paid("0x" + "ff" * 32, 10**18, rpc_id=9)
    assert amount is None
    assert err["error"]["code"] == -32001


# ── end-to-end: message/send records the chain amount ────────────────────────

# ── w06 create-auth compat ─────────────────────────────────────────────────
# The integrated tree requires strict EIP-191 create-auth on message/send:
# unsigned sends are 401'd before payment-reconciliation runs. The signing
# below is the legitimate forward-compatible fix (same canonical message as
# w06's _auth_create_message); the strict auth gate itself is untouched.
# A fresh key per send keeps quota/reputation state isolated across tests.
# On branches without the auth gate these fields are ignored.

_AUTH_CREATE_DOMAIN = "SINCOR-A2A task-create"


def _auth_create_message(agent_label: str, skill_id: str, timestamp: int,
                         nonce: str) -> str:
    return (
        f"{_AUTH_CREATE_DOMAIN}\n"
        f"agent_id:{agent_label}\n"
        f"skill_id:{skill_id}\n"
        f"timestamp:{int(timestamp)}\n"
        f"nonce:{nonce}"
    )


def _signed_auth_params(caller_id: str, skill_id: str) -> dict:
    acct = Account.create()
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = _auth_create_message(caller_id, skill_id, ts, nonce)
    sig = "0x" + acct.sign_message(encode_defunct(text=message)).signature.hex()
    return {
        "ownerWallet": acct.address,
        "authSignature": sig,
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def _send(client, monkeypatch, claimed_wei, chain_wei, tx_hash="0x" + "a1" * 32,
          skill_id="compliance-sbom", caller_id="g25-amount-test"):
    """POST message/send with a patched verifier; return (payload, task_id).

    Runs under FLASK_ENV=production so the real payment gate (and the
    reconciliation under test) executes instead of the dev bypass.
    """
    from types import SimpleNamespace
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.setattr(
        ai, "record_platform_fee_inflow",
        lambda **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        ai.PaymentVerifier, "is_verified",
        classmethod(lambda cls, tx, amt, expected_to=ai.TREASURY_WALLET: True),
    )
    if isinstance(chain_wei, Exception):
        def _boom(cls, tx, expected_to=ai.TREASURY_WALLET):
            raise chain_wei
        monkeypatch.setattr(ai.PaymentVerifier, "verified_amount_wei",
                            classmethod(_boom))
    else:
        monkeypatch.setattr(
            ai.PaymentVerifier, "verified_amount_wei",
            classmethod(lambda cls, tx, expected_to=ai.TREASURY_WALLET: chain_wei),
        )
    response = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "message/send",
            "params": {
                "skillId": skill_id,
                "callerId": caller_id,
                "axmPaidWei": str(claimed_wei),
                "txHash": tx_hash,
                "message": {"parts": [{"text": "Run compliance scan"}]},
                **_signed_auth_params(caller_id, skill_id),
            },
        },
    )
    assert response.status_code == 200
    return response.get_json()


def test_inflated_claim_corrected_down(client, monkeypatch):
    payload = _send(client, monkeypatch, claimed_wei=10 * 10**18,
                    chain_wei=5 * 10**17)
    task_id = payload["result"]["id"]
    task = ai._get_task(task_id)
    assert task.axm_paid == 5 * 10**17


def test_under_claim_corrected_up(client, monkeypatch):
    payload = _send(client, monkeypatch, claimed_wei=10**17,
                    chain_wei=2 * 10**18)
    task_id = payload["result"]["id"]
    task = ai._get_task(task_id)
    assert task.axm_paid == 2 * 10**18


def test_unverifiable_amount_keeps_claim(client, monkeypatch):
    payload = _send(client, monkeypatch, claimed_wei=3 * 10**18, chain_wei=None)
    task_id = payload["result"]["id"]
    task = ai._get_task(task_id)
    assert task.axm_paid == 3 * 10**18


def test_amount_rpc_outage_rejects_send(client, monkeypatch):
    payload = _send(client, monkeypatch, claimed_wei=10**18,
                    chain_wei=ai.PaymentVerifier.PaymentRpcError("down"))
    assert payload["error"]["code"] == -32001
    assert "amount" in payload["error"]["message"]


def test_free_call_records_zero_not_claim(client, monkeypatch):
    """Free-quota calls carry no verified payment: the claim is not recorded."""
    payload = _send(client, monkeypatch, claimed_wei=10 * 10**18,
                    chain_wei=5 * 10**17, skill_id="content-blog",
                    caller_id="g25-free-claim-test")
    task_id = payload["result"]["id"]
    task = ai._get_task(task_id)
    assert task.axm_paid == 0
    assert task.metadata["free_call"] is True
