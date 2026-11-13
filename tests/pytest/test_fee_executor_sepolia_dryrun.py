"""Tests for Sepolia dry-run plumbing and the full authorized loop.

Everything is offline: _eth_call is stubbed, quotes are canned, approver
keys are synthetic.  The live Sepolia run is scripts/fee_executor_sepolia_dryrun.py.
"""

import json
import os

import pytest

from sincor2.onchain import fee_authorization as fauth
from fee_auth_test_helpers import synthetic_approvers
from sincor2.onchain.fee_conversion_executor import (
    ConversionLedger,
    ExecutorError,
    FeeConversionConfig,
    FeeConversionExecutor,
    PoolKey,
    SEPOLIA_CHAIN_ID,
    SEPOLIA_PERMIT2,
    SEPOLIA_POOL_MANAGER,
    SEPOLIA_RPC_URL,
    SEPOLIA_TOKEN_ADDRESSES,
    SEPOLIA_UNIVERSAL_ROUTER,
    SEPOLIA_USDC,
    SEPOLIA_V4_QUOTER,
    SEPOLIA_WETH,
    record_pending_conversion,
)

FORWARDER = "0x" + "0" * 34 + "fee00f"
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


def _sepolia_config(tmp_path, **kw):
    cfg = FeeConversionConfig.sepolia_defaults(
        forwarder=FORWARDER, treasury=TREASURY)
    cfg.ledger_path = os.path.join(str(tmp_path), "ledger.json")
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _sepolia_pool():
    return PoolKey.for_pair(SEPOLIA_WETH, SEPOLIA_USDC, 3000, 60)


# --- config plumbing --------------------------------------------------------

def test_sepolia_defaults_point_at_sepolia(tmp_path):
    cfg = _sepolia_config(tmp_path)
    assert cfg.chain_id == SEPOLIA_CHAIN_ID == 84532
    assert cfg.rpc_url == SEPOLIA_RPC_URL
    assert cfg.quoter == SEPOLIA_V4_QUOTER
    assert cfg.universal_router == SEPOLIA_UNIVERSAL_ROUTER
    assert cfg.permit2 == SEPOLIA_PERMIT2
    assert cfg.token_address("WETH") == SEPOLIA_WETH
    assert cfg.token_address("USDC") == SEPOLIA_USDC


def test_token_address_unknown_raises(tmp_path):
    cfg = _sepolia_config(tmp_path)
    with pytest.raises(ExecutorError, match="unknown token"):
        cfg.token_address("AXM")


def test_default_config_still_base_mainnet(tmp_path):
    cfg = FeeConversionConfig(
        rpc_url="https://example.invalid", forwarder=FORWARDER,
        ledger_path=os.path.join(str(tmp_path), "ledger.json"))
    assert cfg.chain_id == 8453
    assert cfg.token_address("AXM")  # mainnet mapping intact


def test_build_approve_tx_sepolia_override():
    tx = FeeConversionExecutor.build_approve_tx(
        "WETH", FORWARDER, 123, chain_id=84532,
        token_addresses=SEPOLIA_TOKEN_ADDRESSES)
    assert tx["to"] == SEPOLIA_WETH
    assert tx["chainId"] == 84532


def test_build_approve_tx_default_still_mainnet():
    from sincor2.onchain.fee_conversion_executor import TOKEN_ADDRESSES
    tx = FeeConversionExecutor.build_approve_tx("WETH", FORWARDER, 123)
    assert tx["to"] == TOKEN_ADDRESSES["WETH"]
    assert tx["chainId"] == 8453


# --- full loop with stubbed network -----------------------------------------

def _stub_eth_call(raw_quote):
    def _call(self, to, data, from_addr=None):
        return raw_quote
    return _call


def _executor(tmp_path, monkeypatch):
    cfg = _sepolia_config(tmp_path)
    cfg.pool_keys[("WETH", "USDC")] = _sepolia_pool()
    ex = FeeConversionExecutor(cfg)
    # Canned quote: 0.01 WETH -> 25 USDC, gas 300k.  Allowances zero so the
    # plan includes both approval steps.
    raw_quote = (25_000000).to_bytes(32, "big") + (300_000).to_bytes(32, "big")
    monkeypatch.setattr(FeeConversionExecutor, "_eth_call",
                        _stub_eth_call(raw_quote))
    return ex


def test_full_loop_plan_simulate_authorize(tmp_path, monkeypatch):
    ex = _executor(tmp_path, monkeypatch)
    oid = record_pending_conversion(
        ex.ledger, "WETH", 10**16,
        source={"dry_run": True})["id"]

    # pending obligation -> plan (quote + unsigned txs)
    plan = ex.plan(oid)
    kinds = [t["kind"] for t in plan["txs"]]
    assert kinds == ["approve_permit2", "permit2_approve", "swap",
                     "forward_to_treasury"]
    for t in plan["txs"]:
        u = t.get("unsigned")
        if u:
            assert u["chainId"] == 84532
    swap = next(t for t in plan["txs"] if t["kind"] == "swap")
    assert swap["unsigned"]["to"] == SEPOLIA_UNIVERSAL_ROUTER

    # dry-run simulation of every step (stubbed eth_call: no revert)
    sim = ex.execute_plan(plan, None, None, dry_run=True)
    assert sim["mode"] == "dry_run"

    # authorization with synthetic owner keys -> manifest, ledger AUTHORIZED
    approvers = synthetic_approvers(2)
    record = fauth.demo_record(
        chain_id=84532, token_in=SEPOLIA_WETH, token_out=SEPOLIA_USDC,
        treasury=TREASURY, approvers=approvers, quorum=2,
        max_wei_per_run=10**16, nonce="full-loop-1")
    sigs = [fauth.sign_record(record, a) for a in approvers]
    manifest = fauth.authorized_dry_run(
        ex, oid, record, sigs,
        manifest_dir=os.path.join(str(tmp_path), "manifests"),
        used_nonces=set())
    assert manifest["broadcast"] is False
    assert manifest["authorization_hash"] == record.auth_hash_hex()
    assert ex.ledger.get(oid)["status"] == fauth.STATUS_AUTHORIZED
    with open(manifest["manifest_path"]) as fh:
        saved = json.load(fh)
    assert saved["obligation_id"] == oid
    assert saved["quote"]["amount_out_wei"] == 25_000000


def test_execute_plan_still_denies_live(tmp_path, monkeypatch):
    ex = _executor(tmp_path, monkeypatch)
    oid = record_pending_conversion(ex.ledger, "WETH", 10**16)["id"]
    plan = ex.plan(oid)
    with pytest.raises(ExecutorError, match="live conversion is blocked"):
        ex.execute_plan(plan, lambda u, k: "0x0", lambda h: {}, dry_run=False)


def test_plan_fails_closed_below_dust(tmp_path, monkeypatch):
    cfg = _sepolia_config(tmp_path, min_swap_wei=10**18)
    cfg.pool_keys[("WETH", "USDC")] = _sepolia_pool()
    ex = FeeConversionExecutor(cfg)
    raw_quote = (1).to_bytes(32, "big") + (1).to_bytes(32, "big")
    monkeypatch.setattr(FeeConversionExecutor, "_eth_call",
                        _stub_eth_call(raw_quote))
    oid = record_pending_conversion(ex.ledger, "WETH", 10**16)["id"]
    with pytest.raises(ExecutorError, match="min_swap_wei"):
        ex.plan(oid)


def test_scope_rejects_wrong_treasury_in_full_loop(tmp_path, monkeypatch):
    ex = _executor(tmp_path, monkeypatch)
    oid = record_pending_conversion(ex.ledger, "WETH", 10**16)["id"]
    approvers = synthetic_approvers(2)
    record = fauth.demo_record(
        chain_id=84532, token_in=SEPOLIA_WETH, token_out=SEPOLIA_USDC,
        treasury="0x" + "ab" * 20,  # not the executor's treasury
        approvers=approvers, quorum=2, nonce="scope-1")
    sigs = [fauth.sign_record(record, a) for a in approvers]
    with pytest.raises(fauth.AuthorizationError, match="treasury"):
        fauth.authorized_dry_run(ex, oid, record, sigs, used_nonces=set())
