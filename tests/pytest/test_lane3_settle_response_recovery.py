"""Lane 3 W-155 regression: settle-response-loss recovery.

Hunt 49 finding W-155 (CONFIRMED, money path): in
``_HttpFacilitator.verify_and_hold`` a /settle that EXECUTES on-chain
but whose HTTP response is lost was reported as a permanent payment
failure — attempt 1 returned ``facilitator_settle_unreachable`` and
every retry was rejected ``authorization_already_settled``, so the
buyer had paid (EIP-3009 nonce consumed, funds to treasury) yet the
endpoint never served, and the only remedy was a second payment.

The fix resolves the authorization's on-chain state before declaring
failure (USDC ``authorizationState`` + the ``AuthorizationUsed``
event + the exact ``Transfer`` in the same transaction) and returns
success with the SAME stable payment_ref, so the platform's W-146
consumption still opens exactly one escrow. These tests drive the
real client code with the facilitator stubbed at the ``_post``
boundary (the repo mock's nonce semantics, as in the hunt PoC) and
chain state stubbed at the ``_base_rpc`` seam. Payments are signed
with a THROWAWAY key that exists only in this test process. No
network, no real keys.
"""
from __future__ import annotations

import json
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from sincor2 import lane3

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TREASURY = lane3.TREASURY_WALLET
BUYER_KEY = "0x" + "22" * 32
BUYER = Account.from_key(BUYER_KEY).address

sx = lane3._load_sincor_x402()


# ── payment construction (real EIP-3009 signatures, throwaway key) ─────────

_NONCE_COUNTER = [1000]


def _typed_data(auth: dict) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": "USD Coin", "version": "2", "chainId": 8453,
                   "verifyingContract": USDC},
        "message": {
            "from": auth["from"], "to": auth["to"],
            "value": int(auth["value"]),
            "validAfter": int(auth["validAfter"]),
            "validBefore": int(auth["validBefore"]),
            "nonce": auth["nonce"],
        },
    }


def _requirement() -> object:
    return sx.PaymentRequirement(
        scheme="exact", network="base", max_amount_required="10000",
        asset=USDC, pay_to=TREASURY, max_timeout_seconds=120,
        resource="/x402-live/quick-scan", description="w155-test")


def _payment_payload(value: int = 10000, to: str = TREASURY):
    _NONCE_COUNTER[0] += 1
    nonce = "0x" + _NONCE_COUNTER[0].to_bytes(32, "big").hex()
    auth = {"from": BUYER, "to": to, "value": value,
            "validAfter": 0, "validBefore": int(time.time()) + 3600,
            "nonce": nonce}
    signed = Account.sign_message(
        encode_typed_data(full_message=_typed_data(auth)), BUYER_KEY)
    wire_auth = dict(auth)
    wire_auth["value"] = str(value)
    payload = {
        "x402Version": 1, "scheme": "exact", "network": "base",
        "payload": {"signature": "0x" + signed.signature.hex(),
                    "authorization": wire_auth},
    }
    return payload, nonce


def _expected_ref(payload) -> str:
    from sincor_x402.facilitator import (
        _normalize_authorization, _normalize_signature,
        _stable_payment_ref)

    inner = payload["payload"]
    return _stable_payment_ref(
        _normalize_authorization(inner["authorization"]),
        _normalize_signature(inner["signature"]))


# ── fake chain at the _base_rpc seam ────────────────────────────────────────

class _FakeChain:
    """Just enough Base/USDC state for the W-155 proof:
    authorizationState, AuthorizationUsed logs, receipts.

    ``visible=False`` models RPC lag: settlements exist server-side
    but chain reads do not see them yet. ``transfer_override`` lets a
    test corrupt the Transfer evidence (wrong recipient / amount).
    """

    HEAD = 1_000_000

    def __init__(self) -> None:
        self.settled: dict[str, dict] = {}   # nonce -> transfer record
        self.cancelled: set[str] = set()     # nonce consumed, no payment
        self.calls: list[str] = []
        self.visible = True
        self.broken = False
        self.transfer_override: dict | None = None

    # facilitator-side effects ------------------------------------------------
    def execute_settle(self, auth: dict) -> str:
        nonce = str(auth["nonce"]).lower()
        tx = "0x" + format(len(self.settled) + 41, "064x")
        self.settled[nonce] = {
            "from": str(auth["from"]), "to": str(auth["to"]),
            "value": int(auth["value"]), "tx": tx,
        }
        return tx

    def cancel(self, nonce: str) -> None:
        self.cancelled.add(nonce.lower())

    # _base_rpc interface ------------------------------------------------------
    def __call__(self, method, params):
        self.calls.append(method)
        if self.broken:
            raise RuntimeError("rpc down")
        if method == "eth_blockNumber":
            return hex(self.HEAD)
        if method == "eth_call":
            data = params[0]["data"]
            nonce = ("0x" + data[74:138]).lower()
            consumed = self.visible and (
                nonce in self.settled or nonce in self.cancelled)
            return "0x" + format(1 if consumed else 0, "064x")
        if method == "eth_getLogs":
            if not self.visible:
                return []
            flt = params[0]
            nonce = str(flt["topics"][2]).lower()
            record = self.settled.get(nonce)
            if record is None:
                return []
            if not (int(flt["fromBlock"], 16) <= self.HEAD
                    <= int(flt["toBlock"], 16)):
                return []
            return [{
                "address": USDC,
                "topics": [
                    lane3._AUTHORIZATION_USED_TOPIC,
                    lane3._topic_address(record["from"]),
                    nonce,
                ],
                "transactionHash": record["tx"],
                "blockNumber": hex(self.HEAD),
            }]
        if method == "eth_getTransactionReceipt":
            tx = params[0]
            for nonce, record in self.settled.items():
                if record["tx"] != tx:
                    continue
                transfer = dict(record)
                if self.transfer_override:
                    transfer.update(self.transfer_override)
                return {"logs": [
                    {
                        "address": USDC,
                        "topics": [
                            lane3._AUTHORIZATION_USED_TOPIC,
                            lane3._topic_address(record["from"]),
                            nonce,
                        ],
                        "data": "0x",
                    },
                    {
                        "address": USDC,
                        "topics": [
                            lane3._TRANSFER_TOPIC,
                            lane3._topic_address(transfer["from"]),
                            lane3._topic_address(transfer["to"]),
                        ],
                        "data": "0x" + format(transfer["value"], "064x"),
                    },
                ]}
            return {"logs": []}
        raise AssertionError(f"unexpected rpc method {method}")


# ── fake facilitator at the _post boundary ──────────────────────────────────

class _FakeFacilitator:
    """The repo mock's nonce semantics: /verify validates and does
    not consume; /settle consumes. ``settle_mode`` scripts the W-155
    failure shapes."""

    def __init__(self, chain: _FakeChain) -> None:
        self.chain = chain
        self.settle_mode = "execute"
        self.settle_calls = 0
        fac = lane3._HttpFacilitator("https://facilitator.example")
        fac._post = self._post
        self.client = fac

    def _post(self, url, body):
        payload = (body.get("paymentPayload") or {}).get("payload") or {}
        auth = payload.get("authorization") or {}
        nonce = str(auth.get("nonce") or "").lower()
        if url.endswith("/verify"):
            return {"isValid": True, "invalidReason": "", "payer": BUYER}
        if url.endswith("/settle"):
            self.settle_calls += 1
            if self.settle_mode == "forced_fail":
                return {"success": False,
                        "errorReason": "forced_settle_failure_for_test",
                        "payer": BUYER, "transaction": "",
                        "network": "base"}
            if self.settle_mode == "reject_consumed" and (
                    nonce in self.chain.settled
                    or nonce in self.chain.cancelled):
                return {"success": False,
                        "errorReason": "authorization_already_settled",
                        "payer": BUYER, "transaction": "",
                        "network": "base"}
            if self.settle_mode == "lose_without_execute":
                raise TimeoutError("connection reset before settle")
            tx = self.chain.execute_settle(auth)
            if self.settle_mode == "execute_then_lose":
                raise TimeoutError("response lost after on-chain settle")
            return {"success": True, "errorReason": "", "payer": BUYER,
                    "transaction": tx, "network": "base"}
        raise AssertionError(url)


@pytest.fixture
def chain(monkeypatch):
    fake = _FakeChain()
    monkeypatch.setattr(lane3, "_base_rpc", fake)
    return fake


# ── the W-155 recovery ──────────────────────────────────────────────────────

def test_chain_constants_match_keccak():
    """The hardcoded selector/topics are the real EIP-3009 ones."""
    from eth_utils import keccak

    assert lane3._AUTHORIZATION_STATE_SELECTOR == \
        "0x" + keccak(text="authorizationState(address,bytes32)")[:4].hex()
    assert lane3._AUTHORIZATION_USED_TOPIC == \
        "0x" + keccak(text="AuthorizationUsed(address,bytes32)").hex()
    assert lane3._TRANSFER_TOPIC == \
        "0x" + keccak(text="Transfer(address,address,uint256)").hex()


def test_settle_response_loss_recovers_from_chain_state(chain):
    """Settle executes on-chain, response lost: the SAME call
    recovers from chain state and returns success with the stable
    payment_ref — the buyer is served, not failed."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "execute_then_lose"
    payload, nonce = _payment_payload()
    result = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert nonce in chain.settled  # the buyer HAS paid
    assert result.ok is True
    assert result.payer == BUYER
    assert result.payment_ref == _expected_ref(payload)


def test_retry_after_response_loss_recovers_with_same_ref(chain):
    """Attempt 1: settle executes but the response is lost AND the
    RPC view lags, so the failure stands. Attempt 2 (identical
    payload): the facilitator rejects the settle as already-settled,
    and recovery returns success with the identical payment_ref —
    no second payment, exactly one consumable ref."""
    fac = _FakeFacilitator(chain)
    payload, nonce = _payment_payload()

    fac.settle_mode = "execute_then_lose"
    chain.visible = False  # chain reads cannot see the settle yet
    first = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert first.ok is False
    assert first.error.startswith("facilitator_settle_unreachable")
    assert nonce in chain.settled

    chain.visible = True
    fac.settle_mode = "reject_consumed"
    second = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert second.ok is True
    assert second.payment_ref == _expected_ref(payload)
    assert second.payer == BUYER


def test_endpoint_serves_exactly_once_across_loss_and_retry(chain):
    """End to end through VerifiedEndpoint: attempt 1 settles with
    the response lost and is recovered (200, work runs once);
    attempt 2 replays the identical header — recovery succeeds with
    the same ref, and the platform's W-146 consumption refuses a
    second fulfillment (409), so the work STILL ran exactly once."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "execute_then_lose"
    requirement = _requirement()
    work_calls = []

    def work(**kwargs):
        work_calls.append(kwargs)
        return {"answer": 42}

    endpoint = sx.VerifiedEndpoint(
        work, requirement=requirement,
        spec={"required_keys": ["answer"]}, facilitator=fac.client)
    payload, _ = _payment_payload()
    payload["accepted"] = requirement.to_accepts_dict()
    header = json.dumps(payload)

    first = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert first.status == 200
    assert first.body["ok"] is True
    assert len(work_calls) == 1

    fac.settle_mode = "reject_consumed"
    second = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert second.status == 409
    assert second.body["error"] == "payment_ref_consumed"
    assert len(work_calls) == 1  # served exactly once


# ── fail-closed: recovery must never manufacture a settlement ───────────────

def test_settle_transport_loss_without_settlement_fails_closed(chain):
    """The settle request never executed: transport error, empty
    chain — the original failure stands, no success is invented."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "lose_without_execute"
    payload, nonce = _payment_payload()
    result = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert nonce not in chain.settled
    assert result.ok is False
    assert result.error.startswith("facilitator_settle_unreachable")


def test_cancelled_authorization_is_not_a_settlement(chain):
    """authorizationState alone is NOT proof of payment: a buyer who
    cancelled their own authorization consumed the nonce without
    paying anyone. No AuthorizationUsed/Transfer evidence exists,
    so the already-settled rejection must NOT recover."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "reject_consumed"
    payload, nonce = _payment_payload()
    chain.cancel(nonce)
    result = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert result.ok is False
    assert result.error == "authorization_already_settled"


def test_mismatched_transfer_is_not_a_settlement(chain):
    """Chain evidence must bind the EXACT authorization: a settle
    whose Transfer went to a different recipient, or for a different
    amount, does not recover this payment."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "execute_then_lose"

    payload, _ = _payment_payload()
    chain.transfer_override = {"to": "0x" + "99" * 20}
    result = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert result.ok is False

    payload2, _ = _payment_payload()
    chain.transfer_override = {"value": 9999}
    result2 = fac.client.verify_and_hold(
        payment_payload=payload2, requirement=_requirement())
    assert result2.ok is False


def test_rpc_failure_during_recovery_fails_closed(chain):
    """If chain state cannot be resolved at all, recovery fails
    closed and the original settle failure stands."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "execute_then_lose"
    chain.broken = True
    payload, _ = _payment_payload()
    result = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert result.ok is False
    assert result.error.startswith("facilitator_settle_unreachable")


def test_unrelated_settle_failure_never_consults_chain(chain):
    """A definitive settle verdict (not a consumed-nonce shape) is
    returned as-is: no chain reads are spent on it and no recovery
    is attempted."""
    fac = _FakeFacilitator(chain)
    fac.settle_mode = "forced_fail"
    payload, _ = _payment_payload()
    result = fac.client.verify_and_hold(
        payment_payload=payload, requirement=_requirement())
    assert result.ok is False
    assert result.error == "forced_settle_failure_for_test"
    assert chain.calls == []
