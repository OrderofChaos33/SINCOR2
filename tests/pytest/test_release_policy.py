"""Tests for sincor2.release_policy — typed fail-closed release policy.

Adversarial posture: every test tries to get a release approved WITHOUT
proper authorization — absent/stale/expired approvals, wrong chain,
non-allowlisted target, non-allowlisted token, disarmed policy, expired
requests, replayed approvals. In ALL deny cases the fake signer must
record ZERO signing callbacks.
"""

import pytest

from sincor2.release_policy import (
    LEGACY_FLAG_MAPPING,
    OperatorApproval,
    ReleasePolicy,
    ReleaseReceipt,
    ReleaseRequest,
    attempt_release,
    default_release_policy,
    evaluate_release,
    validate_receipt,
)

NOW = 1_790_000_000  # fixed "now" for determinism
OPERATOR = "0x6AF997C5ba8db8153C95dd837b5555Ef5867cE3f"
TARGET = "0x1111111111111111111111111111111111111111"


class FakeSigner:
    """Counts signing callbacks; returns a canned provider receipt."""

    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return {"tx_hash": "0xdeadbeef", "status": 1, "chain_id": request.chain_id}


def armed_policy(**overrides):
    params = dict(
        policy_id="test-policy",
        armed=True,
        allowed_targets=frozenset({TARGET}),
        required_approval_signer=OPERATOR,
        approval_max_age_s=3600,
    )
    params.update(overrides)
    return default_release_policy(**params)


def approval(request_id, signer=OPERATOR, signature="0xsig", expires_at=NOW + 600):
    return OperatorApproval(
        request_id=request_id,
        signer=signer,
        signature=signature,
        expires_at=expires_at,
    )


def valid_request(**overrides):
    req_id = "req-valid-1"
    params = dict(
        operation="release",
        chain_id=8453,
        target=TARGET,
        token="USDC",
        amount_wei=1_000_000,
        manifest_digest="ab" * 32,
        operator_approval=None,  # filled below
        expiry=NOW + 600,
        receipt_schema=("tx_hash", "status"),
        request_id=req_id,
    )
    params.update(overrides)
    if params.get("operator_approval") is None and "operator_approval" not in overrides:
        params["operator_approval"] = approval(req_id)
    return ReleaseRequest(**params)


# ---------------------------------------------------------------------------
# Deny matrix — zero signing callbacks in every case
# ---------------------------------------------------------------------------


def test_deny_absent_approval():
    req = valid_request(operator_approval=None)
    # valid_request fills a default approval; force it absent explicitly
    object.__setattr__(req, "operator_approval", None)
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert receipt.status == "denied"
    assert "operator_approval_missing" in receipt.reason_codes
    assert signer.calls == []


def test_deny_expired_approval():
    req = valid_request()
    object.__setattr__(
        req, "operator_approval", approval(req.request_id, expires_at=NOW - 1)
    )
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "operator_approval_expired" in receipt.reason_codes
    assert signer.calls == []


def test_deny_stale_approval_beyond_max_age():
    # Approval valid for 2h but policy only honors 1h: stale by policy.
    req = valid_request()
    object.__setattr__(
        req, "operator_approval", approval(req.request_id, expires_at=NOW + 7200)
    )
    signer = FakeSigner()
    receipt = attempt_release(
        req, armed_policy(approval_max_age_s=3600), NOW, signer
    )
    assert receipt.executed is False
    assert "operator_approval_stale" in receipt.reason_codes
    assert signer.calls == []


def test_deny_wrong_chain():
    req = valid_request(chain_id=1)  # mainnet not in allowlist
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "chain_not_allowlisted" in receipt.reason_codes
    assert signer.calls == []


def test_deny_non_allowlisted_target():
    req = valid_request(target="0x2222222222222222222222222222222222222222")
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "target_not_allowlisted" in receipt.reason_codes
    assert signer.calls == []


def test_deny_non_allowlisted_token():
    req = valid_request(token="DOGE")
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "token_not_allowlisted" in receipt.reason_codes
    assert signer.calls == []


def test_deny_disarmed_policy():
    signer = FakeSigner()
    receipt = attempt_release(valid_request(), armed_policy(armed=False), NOW, signer)
    assert receipt.executed is False
    assert "policy_disarmed" in receipt.reason_codes
    assert signer.calls == []


def test_deny_expired_request():
    req = valid_request(expiry=NOW - 10)
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "request_expired" in receipt.reason_codes
    assert signer.calls == []


def test_deny_empty_manifest_digest():
    req = valid_request(manifest_digest="")
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "manifest_digest_missing" in receipt.reason_codes
    assert signer.calls == []


def test_deny_approval_signer_mismatch():
    req = valid_request()
    object.__setattr__(
        req,
        "operator_approval",
        approval(req.request_id, signer="0x3333333333333333333333333333333333333333"),
    )
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "approval_signer_mismatch" in receipt.reason_codes
    assert signer.calls == []


def test_deny_approval_replay_across_requests():
    # Approval bound to request A cannot authorize request B.
    req_a = valid_request(request_id="req-A")
    req_b = valid_request(request_id="req-B")
    object.__setattr__(req_b, "operator_approval", req_a.operator_approval)
    signer = FakeSigner()
    receipt = attempt_release(req_b, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "approval_request_mismatch" in receipt.reason_codes
    assert signer.calls == []


def test_deny_approval_missing_signature():
    req = valid_request()
    object.__setattr__(req, "operator_approval", approval(req.request_id, signature=""))
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "approval_signature_missing" in receipt.reason_codes
    assert signer.calls == []


def test_deny_unknown_operation():
    req = valid_request(operation="teleport")
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is False
    assert "unknown_operation" in receipt.reason_codes
    assert signer.calls == []


# ---------------------------------------------------------------------------
# Allow path
# ---------------------------------------------------------------------------


def test_valid_request_approved_signer_called_once_receipt_validated():
    req = valid_request()
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is True
    assert receipt.status == "approved"
    assert "release_approved" in receipt.reason_codes
    assert len(signer.calls) == 1
    assert signer.calls[0].request_id == req.request_id
    # receipt schema validated: fake signer returns tx_hash + status
    assert receipt.provider_receipt["tx_hash"] == "0xdeadbeef"


def test_deploy_operation_also_gated():
    req = valid_request(operation="deploy")
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.executed is True
    assert receipt.status == "approved"
    assert len(signer.calls) == 1


def test_receipt_schema_violation_flagged():
    class BadReceiptSigner(FakeSigner):
        def __call__(self, request):
            self.calls.append(request)
            return {"tx_hash": "0xdeadbeef"}  # missing "status"

    req = valid_request()
    signer = BadReceiptSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    # The chain effect happened, but the receipt is flagged — callers must
    # not mistake an incomplete receipt for a clean one.
    assert receipt.executed is True
    assert receipt.status == "receipt_invalid"
    assert "receipt_schema_violation" in receipt.reason_codes


def test_token_case_insensitive():
    req = valid_request(token="usdc")
    assert req.token == "USDC"
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.status == "approved"
    assert signer.calls != []


def test_sepolia_chain_allowed():
    req = valid_request(chain_id=11155111)
    signer = FakeSigner()
    receipt = attempt_release(req, armed_policy(), NOW, signer)
    assert receipt.status == "approved"


# ---------------------------------------------------------------------------
# Read-only operations: always allowed, never need approval, never sign
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["plan", "quote", "dry_run", "read", "simulate"])
def test_read_only_always_allowed_no_approval_no_signing(op):
    # Minimal request: no approval, disarmed policy, expired-looking fields —
    # read-only still passes.
    req = ReleaseRequest(
        operation=op,
        chain_id=1,  # not allowlisted — irrelevant for read-only
        target="0x0",
        token="DOGE",
        amount_wei=0,
        manifest_digest="",
        operator_approval=None,
        expiry=0,
        request_id=f"req-ro-{op}",
    )
    decision = evaluate_release(req, default_release_policy(armed=False), NOW)
    assert decision.allowed is True
    assert "read_only_operation" in decision.reason_codes

    signer = FakeSigner()
    receipt = attempt_release(req, default_release_policy(armed=False), NOW, signer)
    assert receipt.status == "approved"
    assert receipt.executed is False  # nothing executed
    assert signer.calls == []  # zero signing callbacks


# ---------------------------------------------------------------------------
# Helpers / legacy mapping
# ---------------------------------------------------------------------------


def test_validate_receipt():
    assert validate_receipt({"a": 1, "b": 2}, ("a", "b")) == []
    assert validate_receipt({"a": 1}, ("a", "b")) == ["b"]
    assert validate_receipt(None, ("a",)) == ["a"]


def test_default_policy_ships_disarmed():
    policy = default_release_policy(
        policy_id="p", allowed_targets=frozenset({TARGET}),
        required_approval_signer=OPERATOR,
    )
    assert policy.armed is False
    signer = FakeSigner()
    receipt = attempt_release(valid_request(), policy, NOW, signer)
    assert receipt.status == "denied"
    assert signer.calls == []


def test_legacy_flag_mapping_documented():
    # The three known boolean gates are documented as deprecated in favor
    # of the typed policy (additive — legacy code is not removed here).
    assert "fee_conversion_executor.Config.armed" in LEGACY_FLAG_MAPPING
    assert "treasury_execution_agent.EXECUTE_LIVE" in LEGACY_FLAG_MAPPING
    assert "treasury_policy.is_conversion_executor_armed()" in LEGACY_FLAG_MAPPING


def test_evaluate_never_raises_on_bad_types():
    decision = evaluate_release("not-a-request", armed_policy(), NOW)
    assert decision.allowed is False
    decision = evaluate_release(valid_request(), "not-a-policy", NOW)
    assert decision.allowed is False
