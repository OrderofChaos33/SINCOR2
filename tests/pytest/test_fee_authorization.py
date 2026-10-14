"""Tests for the canonical fee-conversion authorization interface.

All offline: synthetic approver keys (deterministic, in-memory only),
no RPC, no real owner key material anywhere.
"""

import time

import pytest

from sincor2.onchain.fee_authorization import (
    FEE_CONVERSION_CAPABILITY,
    STATUS_AUTHORIZED,
    AuthorizationError,
    AuthorizationRecord,
    authorized_dry_run,
    check_plan_scope,
    demo_record,
    sign_record,
    verify_authorization,
)
from fee_auth_test_helpers import synthetic_approvers

CHAIN = 84532
TOKEN_IN = "0x4200000000000000000000000000000000000006"   # WETH (Sepolia)
TOKEN_OUT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # USDC (Sepolia)
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
NOW = 1_800_000_000


def _approvers(n=3):
    return synthetic_approvers(n, seed=0xA07)


def _record(approvers, **kw):
    params = dict(chain_id=CHAIN, token_in=TOKEN_IN, token_out=TOKEN_OUT,
                  treasury=TREASURY, approvers=approvers, quorum=2,
                  nonce="test-nonce-1", now=NOW)
    params.update(kw)
    return demo_record(**params)


def _sigs(record, approvers):
    return [sign_record(record, a) for a in approvers]


# --- verification ----------------------------------------------------------

def test_valid_quorum_verifies():
    ap = _approvers()
    rec = _record(ap)
    out = verify_authorization(rec, _sigs(rec, ap[:2]), now=NOW)
    assert out is rec


def test_more_sigs_than_quorum_ok():
    ap = _approvers()
    rec = _record(ap)
    assert verify_authorization(rec, _sigs(rec, ap), now=NOW) is rec


def test_expired_rejected():
    ap = _approvers()
    rec = _record(ap, ttl_secs=10)
    with pytest.raises(AuthorizationError, match="expired"):
        verify_authorization(rec, _sigs(rec, ap[:2]), now=NOW + 3600)


def test_not_yet_valid_rejected():
    ap = _approvers()
    rec = demo_record(chain_id=CHAIN, token_in=TOKEN_IN, token_out=TOKEN_OUT,
                      treasury=TREASURY, approvers=ap, quorum=2,
                      nonce="future", now=NOW + 10_000)
    with pytest.raises(AuthorizationError, match="not yet valid"):
        verify_authorization(rec, _sigs(rec, ap[:2]), now=NOW)


def test_wrong_capability_rejected():
    ap = _approvers()
    rec = _record(ap)
    bad = AuthorizationRecord(**{**rec.canonical_dict(),
                                 "capability": "sincor.evil.v1"})
    with pytest.raises(AuthorizationError, match="capability"):
        verify_authorization(bad, _sigs(bad, ap[:2]), now=NOW)


def test_non_approver_sig_does_not_count():
    ap = _approvers(3)
    outsider = _approvers(1)[0]
    rec = _record(ap)
    sigs = [sign_record(rec, ap[0]), sign_record(rec, outsider)]
    with pytest.raises(AuthorizationError, match="quorum not met"):
        verify_authorization(rec, sigs, now=NOW)


def test_duplicate_approver_counts_once():
    ap = _approvers()
    rec = _record(ap)
    sigs = [sign_record(rec, ap[0]), sign_record(rec, ap[0])]
    with pytest.raises(AuthorizationError, match="quorum not met"):
        verify_authorization(rec, sigs, now=NOW)


def test_tampered_record_rejected():
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])
    tampered = AuthorizationRecord(
        **{**rec.canonical_dict(), "treasury": "0x" + "ab" * 20})
    with pytest.raises(AuthorizationError, match="quorum not met"):
        verify_authorization(tampered, sigs, now=NOW)


def test_tampered_amount_limit_rejected():
    ap = _approvers()
    rec = _record(ap)
    sigs = _sigs(rec, ap[:2])
    tampered = AuthorizationRecord(
        **{**rec.canonical_dict(), "max_wei_per_run": 10**30})
    with pytest.raises(AuthorizationError):
        verify_authorization(tampered, sigs, now=NOW)


def test_malformed_signature_rejected():
    ap = _approvers()
    rec = _record(ap)
    with pytest.raises(AuthorizationError):
        verify_authorization(rec, ["0xdeadbeef"], now=NOW)


def test_garbage_signature_rejected():
    ap = _approvers()
    rec = _record(ap)
    with pytest.raises(AuthorizationError):
        verify_authorization(rec, ["0x" + "11" * 65], now=NOW)


def test_quorum_above_approvers_rejected_at_construction():
    ap = _approvers(2)
    with pytest.raises(AuthorizationError, match="quorum exceeds"):
        _record(ap, quorum=3)


def test_expiry_before_start_rejected_at_construction():
    ap = _approvers()
    with pytest.raises(AuthorizationError, match="expires_at"):
        AuthorizationRecord(
            capability=FEE_CONVERSION_CAPABILITY, chain_ids=(CHAIN,),
            token_in_allowlist=(TOKEN_IN,), token_out_allowlist=(TOKEN_OUT,),
            treasury=TREASURY, max_wei_per_run=100, max_slippage_bps=100,
            not_before=NOW + 100, expires_at=NOW, nonce="x",
            approvers=tuple(a.address for a in ap), quorum=1,
            receipt_owner=TREASURY)


def test_auth_hash_deterministic_and_nonce_sensitive():
    ap = _approvers()
    r1 = _record(ap, nonce="n1")
    r2 = _record(ap, nonce="n1")
    r3 = _record(ap, nonce="n2")
    assert r1.auth_hash() == r2.auth_hash()
    assert r1.auth_hash() != r3.auth_hash()


def test_address_normalization_case_insensitive():
    ap = _approvers()
    rec = _record(ap, treasury=TREASURY.upper())
    assert rec.treasury == TREASURY.lower()


# --- scope checks ----------------------------------------------------------

def _scope_kwargs(**kw):
    base = dict(chain_id=CHAIN, token_in=TOKEN_IN, token_out=TOKEN_OUT,
                treasury=TREASURY, amount_wei=10**18, slippage_bps=100)
    base.update(kw)
    return base


def test_scope_valid():
    ap = _approvers()
    rec = _record(ap)
    check_plan_scope(rec, **_scope_kwargs())  # must not raise


def test_scope_wrong_chain():
    ap = _approvers()
    rec = _record(ap)
    with pytest.raises(AuthorizationError, match="chain"):
        check_plan_scope(rec, **_scope_kwargs(chain_id=8453))


def test_scope_token_in_not_allowlisted():
    ap = _approvers()
    rec = _record(ap)
    with pytest.raises(AuthorizationError, match="token_in"):
        check_plan_scope(rec, **_scope_kwargs(
            token_in="0x" + "cc" * 20))


def test_scope_token_out_not_allowlisted():
    ap = _approvers()
    rec = _record(ap)
    with pytest.raises(AuthorizationError, match="token_out"):
        check_plan_scope(rec, **_scope_kwargs(
            token_out="0x" + "dd" * 20))


def test_scope_treasury_mismatch():
    ap = _approvers()
    rec = _record(ap)
    with pytest.raises(AuthorizationError, match="treasury"):
        check_plan_scope(rec, **_scope_kwargs(
            treasury="0x" + "ee" * 20))


def test_scope_amount_over_limit():
    ap = _approvers()
    rec = _record(ap, max_wei_per_run=10**18)
    with pytest.raises(AuthorizationError, match="exceeds"):
        check_plan_scope(rec, **_scope_kwargs(amount_wei=10**18 + 1))


def test_scope_slippage_over_limit():
    ap = _approvers()
    rec = _record(ap, max_slippage_bps=100)
    with pytest.raises(AuthorizationError, match="slippage"):
        check_plan_scope(rec, **_scope_kwargs(slippage_bps=101))


# --- authorized_dry_run (full loop, stub executor, no broadcast) -----------

class _FakeLedger:
    def __init__(self):
        self.statuses = {}
        self.details = {}

    def transition(self, oid, status, detail=None):
        self.statuses[oid] = status
        self.details[oid] = detail or {}
        return {"id": oid, "status": status}

    def get(self, oid):
        return {"id": oid, "status": self.statuses.get(oid)}


class _FakeConfig:
    chain_id = CHAIN
    treasury = TREASURY
    token_addresses = {"WETH": TOKEN_IN, "USDC": TOKEN_OUT}

    def token_address(self, symbol):
        # Duck-types FeeConversionConfig.token_address: explicit map first.
        return self.token_addresses[symbol.upper()]


class _FakeMainnetConfig(_FakeConfig):
    # token_addresses=None: the Base-mainnet default.  Resolution must fall
    # back to TOKEN_ADDRESSES via token_address(), not a raw dict lookup.
    chain_id = 8453
    token_addresses = None

    def token_address(self, symbol):
        from sincor2.onchain.fee_conversion_executor import TOKEN_ADDRESSES
        addrs = self.token_addresses or TOKEN_ADDRESSES
        return addrs[symbol.upper()]


class _FakeExecutor:
    """Stub executor: proves authorized_dry_run's orchestration without RPC."""

    def __init__(self):
        self.config = _FakeConfig()
        self.ledger = _FakeLedger()
        self.signer_called = False
        self.planned = False

    def plan(self, obligation_id, target=None):
        self.planned = True
        return {
            "obligation_id": obligation_id,
            "forwarder": "0x" + "11" * 20,
            "treasury": TREASURY,
            "quote": {
                "token_in": "WETH", "token_out": "USDC",
                "amount_in_wei": 10**18, "amount_out_wei": 2500_000000,
                "amount_out_min_wei": 2475_000000, "slippage_bps": 100,
            },
            "txs": [
                {"kind": "approve_permit2",
                 "unsigned": {"to": "0x" + "22" * 20, "chainId": CHAIN}},
                {"kind": "swap",
                 "unsigned": {"to": "0x" + "33" * 20, "chainId": CHAIN}},
                {"kind": "forward_to_treasury", "unsigned": None,
                 "note": "built post-swap"},
            ],
        }

    def execute_plan(self, plan, sign_and_broadcast, wait_for_receipt,
                     dry_run=False):
        assert dry_run is True
        # Prove no signer path is reachable: the callbacks must raise.
        try:
            sign_and_broadcast({}, "x")
        except AuthorizationError:
            pass
        else:
            raise AssertionError("signer callback did not fail closed")
        return {"obligation_id": plan["obligation_id"], "mode": "dry_run",
                "status": "simulated"}


def _auth(ap, **kw):
    rec = _record(ap, **kw)
    return rec, _sigs(rec, ap[:2])


def test_authorized_dry_run_full_loop(tmp_path):
    ap = _approvers()
    rec, sigs = _auth(ap)
    ex = _FakeExecutor()
    manifest = authorized_dry_run(
        ex, "oid-1", rec, sigs,
        manifest_dir=str(tmp_path), used_nonces=set(), now=NOW)
    assert ex.planned
    assert manifest["broadcast"] is False
    assert manifest["authorization_hash"] == rec.auth_hash_hex()
    assert manifest["chain_id"] == CHAIN
    assert len(manifest["unsigned_txs"]) == 3
    assert ex.ledger.statuses["oid-1"] == STATUS_AUTHORIZED
    assert ex.ledger.details["oid-1"]["authorization_hash"] == \
        rec.auth_hash_hex()
    import os
    assert os.path.exists(manifest["manifest_path"])


def test_authorized_dry_run_rejects_bad_auth():
    ap = _approvers()
    rec, _ = _auth(ap)
    ex = _FakeExecutor()
    with pytest.raises(AuthorizationError):
        authorized_dry_run(ex, "oid-1", rec, ["0x" + "11" * 65],
                           used_nonces=set(), now=NOW)
    assert not ex.planned  # nothing happened before auth


def test_authorized_dry_run_rejects_out_of_scope_plan():
    ap = _approvers()
    rec, sigs = _auth(ap, max_wei_per_run=10**10)  # plan needs 10**18
    ex = _FakeExecutor()
    with pytest.raises(AuthorizationError, match="exceeds"):
        authorized_dry_run(ex, "oid-1", rec, sigs,
                           used_nonces=set(), now=NOW)


def test_authorized_dry_run_nonce_reuse_rejected(tmp_path):
    ap = _approvers()
    rec, sigs = _auth(ap)
    ex = _FakeExecutor()
    nonces = set()
    authorized_dry_run(ex, "oid-1", rec, sigs,
                       manifest_dir=str(tmp_path), used_nonces=nonces,
                       now=NOW)
    with pytest.raises(AuthorizationError, match="already used"):
        authorized_dry_run(ex, "oid-2", rec, sigs,
                           manifest_dir=str(tmp_path), used_nonces=nonces,
                           now=NOW)


def test_authorized_dry_run_requires_nonce_journal(tmp_path):
    # Nonce replay protection is mandatory, not opt-in: the journal is a
    # required parameter.  Omitting it fails at the call boundary.
    ap = _approvers()
    rec, sigs = _auth(ap)
    ex = _FakeExecutor()
    with pytest.raises(TypeError):
        authorized_dry_run(ex, "oid-1", rec, sigs,
                           manifest_dir=str(tmp_path), now=NOW)


def test_authorized_dry_run_mainnet_config_resolves_tokens(tmp_path):
    # Regression for the scope-check finding: with token_addresses=None
    # (the Base-mainnet default), quote symbols must resolve through
    # config.token_address() (TOKEN_ADDRESSES fallback) — a raw dict lookup
    # would leak the bare "WETH" symbol into _norm_addr and fail.
    from sincor2.onchain.fee_conversion_executor import TOKEN_ADDRESSES
    ap = _approvers()
    rec = demo_record(chain_id=8453,
                      token_in=TOKEN_ADDRESSES["WETH"],
                      token_out=TOKEN_ADDRESSES["USDC"],
                      treasury=TREASURY, approvers=ap, quorum=2,
                      nonce="mainnet-1", now=NOW)
    sigs = _sigs(rec, ap[:2])
    ex = _FakeExecutor()
    ex.config = _FakeMainnetConfig()
    manifest = authorized_dry_run(ex, "oid-mainnet", rec, sigs,
                                  manifest_dir=str(tmp_path),
                                  used_nonces=set(), now=NOW)
    assert manifest["chain_id"] == 8453
    assert manifest["broadcast"] is False


def test_non_string_signature_raises_authorization_error():
    # A non-string signature must fail closed as AuthorizationError, not
    # leak AttributeError from .strip().
    ap = _approvers()
    rec = _record(ap)
    good = _sigs(rec, ap[:2])[0]
    with pytest.raises(AuthorizationError, match="hex string"):
        verify_authorization(rec, [None, good], now=NOW)
    with pytest.raises(AuthorizationError, match="hex string"):
        verify_authorization(rec, [b"0x" + b"11" * 65, good], now=NOW)


def test_check_plan_scope_rejects_nonpositive_amount():
    ap = _approvers()
    rec = _record(ap)
    for amount in (0, -10**18):
        with pytest.raises(AuthorizationError, match="must be > 0"):
            check_plan_scope(rec, chain_id=CHAIN, token_in=TOKEN_IN,
                             token_out=TOKEN_OUT, treasury=TREASURY,
                             amount_wei=amount, slippage_bps=100)
