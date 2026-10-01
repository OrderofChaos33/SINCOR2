"""Tests for the fee conversion executor — all offline, no RPC, no keys."""

import json
import os

import pytest
from eth_abi import decode as abi_decode
from web3 import Web3

from sincor2.onchain.fee_conversion_executor import (
    ACT_SETTLE_ALL,
    ACT_SWAP_EXACT_IN_SINGLE,
    ACT_TAKE_ALL,
    CMD_SWEEP,
    CMD_V4_SWAP,
    SEL_APPROVE,
    SEL_TRANSFER,
    SEL_UR_EXECUTE,
    ConversionLedger,
    FeeConversionConfig,
    FeeConversionExecutor,
    MissingPoolKey,
    ExecutorError,
    PoolKey,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_PENDING,
    ZERO_HOOKS,
    PERMIT2,
    SEL_P2_APPROVE,
    UNIVERSAL_ROUTER_V4,
    record_pending_conversion,
    zero_for_one,
)

AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH = "0x4200000000000000000000000000000000000006"
FORWARDER = "0x1111111111111111111111111111111111111111"
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


def _pool():
    return PoolKey.for_pair(AXM, USDC, 3000, 60)


def _config(**kw):
    cfg = FeeConversionConfig(
        rpc_url="https://example.invalid",
        forwarder=FORWARDER,
        treasury=TREASURY,
        ledger_path=os.path.join(str(kw.pop("tmp_path", "/tmp")),
                                 "ledger.json"),
        pool_keys={("AXM", "USDC"): _pool()},
        armed=False,  # tests never arm; live broadcast is out of scope
    )
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


# --- PoolKey ---------------------------------------------------------------

def test_pool_key_orders_currencies():
    pool = PoolKey.for_pair(USDC, AXM, 3000, 60)
    assert pool.currency0 < pool.currency1
    # USDC (0x8335...) > AXM (0x4c3f...) so AXM must be currency0
    assert pool.currency0 == AXM.lower()
    assert pool.currency1 == USDC.lower()


def test_zero_for_one():
    pool = _pool()
    assert zero_for_one(pool, AXM) is True
    assert zero_for_one(pool, USDC) is False


# --- ERC-20 builders --------------------------------------------------------

def test_build_approve_tx_encoding():
    tx = FeeConversionExecutor.build_approve_tx("AXM", FORWARDER, 12345)
    # EIP-55 normalized for web3.py v8; same address, canonical casing.
    assert tx["to"].lower() == AXM.lower()
    assert tx["to"] == Web3.to_checksum_address(AXM)
    assert tx["value"] == 0 and tx["chainId"] == 8453
    data = bytes.fromhex(tx["data"][2:])
    assert data[:4] == SEL_APPROVE
    to, amount = abi_decode(["address", "uint256"], data[4:])
    assert to.lower() == FORWARDER.lower()
    assert amount == 12345


def test_build_transfer_tx_encoding():
    tx = FeeConversionExecutor.build_transfer_tx("USDC", TREASURY, 999)
    data = bytes.fromhex(tx["data"][2:])
    assert data[:4] == SEL_TRANSFER
    to, amount = abi_decode(["address", "uint256"], data[4:])
    assert to.lower() == TREASURY.lower()
    assert amount == 999


# --- Universal Router swap builder ------------------------------------------

def test_build_swap_tx_encoding():
    cfg = _config(tmp_path="/tmp")
    ex = FeeConversionExecutor(cfg)
    tx = ex.build_swap_tx("AXM", "USDC", 10**18, 900_000, FORWARDER,
                          deadline=1_800_000_000)
    # EIP-55 normalized for web3.py v8; same address, canonical casing.
    assert tx["to"].lower() == cfg.universal_router.lower()
    assert tx["to"] == Web3.to_checksum_address(cfg.universal_router)
    data = bytes.fromhex(tx["data"][2:])
    assert data[:4] == SEL_UR_EXECUTE
    commands, inputs, deadline = abi_decode(
        ["bytes", "bytes[]", "uint256"], data[4:])
    assert commands == bytes([CMD_V4_SWAP])  # no SWEEP: TAKE_ALL pays msgSender
    assert deadline == 1_800_000_000
    assert len(inputs) == 1

    # V4_SWAP input: (actions, params)
    actions, params = abi_decode(["bytes", "bytes[]"], inputs[0])
    assert actions == bytes([ACT_SWAP_EXACT_IN_SINGLE, ACT_SETTLE_ALL,
                             ACT_TAKE_ALL])
    assert len(params) == 3
    # params[0] is a SINGLE abi-encoded ExactInputSingleParams struct
    # (v4-periphery CalldataDecoder follows the first word as an offset).
    (pool_tup, zfo, amount_in, amount_out_min, min_hop_price,
     hook_data), = abi_decode(
        ["((address,address,uint24,int24,address),bool,uint128,uint128,"
         "uint256,bytes)"],
        params[0])
    assert min_hop_price == 0  # per-hop price guard disabled
    assert tuple(x.lower() if isinstance(x, str) else x for x in pool_tup) == \
        (AXM.lower(), USDC.lower(), 3000, 60, ZERO_HOOKS)
    assert zfo is True            # AXM (0x4c..) < USDC (0x83..)
    assert amount_in == 10**18
    assert amount_out_min == 900_000
    assert hook_data == b""

    settle_currency, settle_amount = abi_decode(
        ["address", "uint256"], params[1])
    assert settle_currency.lower() == AXM.lower()
    assert settle_amount == 10**18

    take_currency, take_min = abi_decode(["address", "uint256"], params[2])
    assert take_currency.lower() == USDC.lower()
    assert take_min == 900_000
    # No SWEEP: the deployed V4Router's TAKE_ALL does
    # _take(currency, msgSender(), amount), so output lands directly on the
    # forwarder (the execute() sender). A SWEEP would revert.


def test_build_swap_tx_rejects_unknown_pool():
    cfg = _config(tmp_path="/tmp")
    cfg.pool_keys = {}
    ex = FeeConversionExecutor(cfg)
    with pytest.raises(MissingPoolKey):
        ex.build_swap_tx("AXM", "USDC", 10**18, 1, FORWARDER)


# --- Ledger ------------------------------------------------------------------

def test_ledger_record_is_idempotent(tmp_path):
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    a = ledger.record("ob-1", "AXM", 10**18, {"tx_hash": "0xabc"})
    b = ledger.record("ob-1", "AXM", 10**18, {"tx_hash": "0xabc"})
    assert a is b
    assert a["status"] == STATUS_PENDING
    assert a["history"][0]["status"] == STATUS_PENDING


def test_ledger_transitions_and_txs(tmp_path):
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    ledger.record("ob-1", "SINC", 500)
    ledger.transition("ob-1", "quoted", {"note": "q"})
    ledger.add_tx("ob-1", "swap", "0xdead")
    ob = ledger.get("ob-1")
    assert ob["status"] == "quoted"
    assert [h["status"] for h in ob["history"]] == ["pending", "quoted"]
    assert ob["txs"] == [{"kind": "swap", "tx_hash": "0xdead",
                          "at": ob["txs"][0]["at"]}]
    assert ledger.pending() and ledger.pending()[0]["id"] == "ob-1"


def test_ledger_survives_reload(tmp_path):
    path = str(tmp_path / "ledger.json")
    ledger = ConversionLedger(path)
    ledger.record("ob-9", "AXM", 42)
    ledger2 = ConversionLedger(path)
    assert ledger2.get("ob-9")["amount_wei"] == "42"


def test_record_pending_conversion_idempotent(tmp_path):
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    a = record_pending_conversion(ledger, "AXM", 10**18,
                                  {"tx_hash": "0x" + "ab" * 32})
    b = record_pending_conversion(ledger, "AXM", 10**18,
                                  {"tx_hash": "0x" + "ab" * 32})
    assert a["id"] == b["id"]
    assert a["id"].startswith("feeconv-AXM-")


# --- Permit2 internal allowance ------------------------------------------------

def test_build_permit2_approve_tx_encoding():
    tx = FeeConversionExecutor.build_permit2_approve_tx(
        "AXM", UNIVERSAL_ROUTER_V4, 10**18, 1_800_000_000)
    assert tx["to"] == Web3.to_checksum_address(PERMIT2)
    assert tx["value"] == 0 and tx["chainId"] == 8453
    data = bytes.fromhex(tx["data"][2:])
    assert data[:4] == SEL_P2_APPROVE
    token, spender, amount, expiration = abi_decode(
        # wire-identical widths; narrow uints trip a strict-decoder quirk
        ["address", "address", "uint256", "uint256"], data[4:])
    assert token.lower() == AXM.lower()
    assert spender.lower() == UNIVERSAL_ROUTER_V4.lower()
    assert amount == 10**18
    assert expiration == 1_800_000_000


def test_permit2_allowance_reads_amount_word(tmp_path):
    # Permit2.allowance returns (uint160 amount, uint48 expiration, uint48 nonce)
    cfg = _config(tmp_path=tmp_path)
    ex = _StubExecutor(cfg)

    calls = {}

    def fake_eth_call(to, data, from_addr=None):
        calls["to"] = to
        assert data[:4] == bytes.fromhex("927da105")
        return (7_000).to_bytes(32, "big") + (9_999).to_bytes(32, "big") \
            + (3).to_bytes(32, "big")

    ex._eth_call = fake_eth_call
    # call the REAL implementation (the stub overrides permit2_allowance)
    assert FeeConversionExecutor.permit2_allowance(
        ex, "AXM", FORWARDER, UNIVERSAL_ROUTER_V4) == 7_000
    assert calls["to"].lower() == \
        Web3.to_checksum_address(
            "0x000000000022D473030F116dDEE9F6B43aC78BA3").lower()


# --- Executor: plan -----------------------------------------------------------

class _StubExecutor(FeeConversionExecutor):
    """Executor with RPC stubbed out."""

    def __init__(self, config, quote_out=900_000, allowance_wei=0,
                 balance_wei=0, p2_allowance_wei=0):
        super().__init__(config)
        self._quote_out = quote_out
        self._allowance = allowance_wei
        self._balance = balance_wei
        self._p2_allowance = p2_allowance_wei

    def quote(self, token_in, token_out, amount_in_wei):
        return {"token_in": token_in, "token_out": token_out,
                "amount_in_wei": amount_in_wei,
                "amount_out_wei": self._quote_out,
                "amount_out_min_wei": self._quote_out * 99 // 100,
                "gas_estimate": 200_000,
                "slippage_bps": self.config.slippage_bps}

    def allowance(self, token, owner, spender):
        return self._allowance

    def permit2_allowance(self, token, owner, spender):
        return self._p2_allowance

    def balance_of(self, token, owner):
        return self._balance

    def _simulate(self, kind, unsigned):
        return None  # simulation passes


def test_plan_builds_approve_swap_forward(tmp_path):
    cfg = _config(tmp_path=tmp_path)
    ex = _StubExecutor(cfg, allowance_wei=0)
    ob = ex.ledger.record("ob-plan", "AXM", 10**18)
    plan = ex.plan("ob-plan")
    kinds = [t["kind"] for t in plan["txs"]]
    assert kinds == ["approve_permit2", "permit2_approve", "swap",
                     "forward_to_treasury"]
    assert plan["quote"]["token_out"] == "USDC"
    assert ex.ledger.get("ob-plan")["status"] == "quoted"


def test_plan_skips_approve_when_allowance_covers(tmp_path):
    cfg = _config(tmp_path=tmp_path)
    ex = _StubExecutor(cfg, allowance_wei=10**30)
    ex.ledger.record("ob-noapprove", "AXM", 10**18)
    plan = ex.plan("ob-noapprove")
    # ERC-20 approval covered, but the Permit2-internal allowance is still
    # missing -> only the permit2_approve step remains.
    assert [t["kind"] for t in plan["txs"]] == ["permit2_approve", "swap",
                                               "forward_to_treasury"]


def test_plan_skips_both_approvals_when_covered(tmp_path):
    cfg = _config(tmp_path=tmp_path)
    ex = _StubExecutor(cfg, allowance_wei=10**30, p2_allowance_wei=10**30)
    ex.ledger.record("ob-noapprove2", "AXM", 10**18)
    plan = ex.plan("ob-noapprove2")
    assert [t["kind"] for t in plan["txs"]] == ["swap", "forward_to_treasury"]


def test_plan_rejects_dust(tmp_path):
    cfg = _config(tmp_path=tmp_path, min_swap_wei=10**18)
    ex = _StubExecutor(cfg)
    ex.ledger.record("ob-dust", "AXM", 10**17)
    with pytest.raises(ExecutorError, match="min_swap_wei"):
        ex.plan("ob-dust")


def test_plan_rejects_bad_target(tmp_path):
    cfg = _config(tmp_path=tmp_path)
    ex = _StubExecutor(cfg)
    ex.ledger.record("ob-bad", "AXM", 10**18)
    with pytest.raises(ExecutorError, match="unsupported target"):
        ex.plan("ob-bad", target="DOGE")


# --- Executor: execute_plan ----------------------------------------------------

def _harness(tmp_path, **kw):
    cfg = _config(tmp_path=tmp_path, **kw)
    ex = _StubExecutor(cfg, balance_wei=881_000)
    return ex


def test_execute_plan_dry_run_broadcasts_nothing(tmp_path):
    ex = _harness(tmp_path)
    ex.ledger.record("ob-dry", "AXM", 10**18)
    plan = ex.plan("ob-dry")

    def boom(unsigned, kind):
        raise AssertionError("must not broadcast in dry run")

    out = ex.execute_plan(plan, boom, lambda h: {"status": 1}, dry_run=True)
    assert out["mode"] == "dry_run"
    assert ex.ledger.get("ob-dry")["txs"] == []


def test_execute_plan_live_happy_path(tmp_path):
    # armed=True here is test-only: _config uses rpc_url="https://example.invalid"
    # and fake_sign never broadcasts. Exercises the live-path logic with mocks.
    ex = _harness(tmp_path, armed=True)
    ex.ledger.record("ob-live", "AXM", 10**18)
    plan = ex.plan("ob-live")
    seen = []

    def fake_sign(unsigned, kind):
        seen.append(kind)
        return "0x" + kind.encode().hex()[:16].ljust(64, "0")[:64]

    out = ex.execute_plan(plan, fake_sign, lambda h: {"status": 1})
    assert out["status"] == STATUS_EXECUTED
    kinds = [t["kind"] for t in ex.ledger.get("ob-live")["txs"]]
    assert kinds == ["approve_permit2", "permit2_approve", "swap",
                     "forward_to_treasury"]
    fwd = [t for t in ex.ledger.get("ob-live")["txs"]
           if t["kind"] == "forward_to_treasury"][0]
    assert fwd["amount_wei"] == "881000"  # verified post-swap balance
    assert seen == ["approve_permit2", "permit2_approve", "swap",
                    "forward_to_treasury"]


def test_execute_plan_refuses_when_disarmed(tmp_path):
    ex = _harness(tmp_path, armed=False)
    ex.ledger.record("ob-safe", "AXM", 10**18)
    plan = ex.plan("ob-safe")
    with pytest.raises(ExecutorError, match="not armed"):
        ex.execute_plan(plan, lambda u, k: "0x0", lambda h: {"status": 1})


def test_execute_plan_marks_failed_on_revert(tmp_path):
    # armed=True here is test-only (see test_execute_plan_live_happy_path).
    ex = _harness(tmp_path, armed=True)
    ex.ledger.record("ob-revert", "AXM", 10**18)
    plan = ex.plan("ob-revert")

    def fake_sign(unsigned, kind):
        return "0xbeef"

    with pytest.raises(ExecutorError, match="reverted"):
        ex.execute_plan(plan, fake_sign, lambda h: {"status": 0})
    ob = ex.ledger.get("ob-revert")
    assert ob["status"] == STATUS_FAILED
    assert "reverted" in ob["history"][-1]["error"]


def test_execute_plan_refuses_zero_post_swap_balance(tmp_path):
    # armed=True here is test-only (see test_execute_plan_live_happy_path).
    cfg = _config(tmp_path=tmp_path, armed=True)
    ex = _StubExecutor(cfg, balance_wei=0)
    ex.ledger.record("ob-zero", "AXM", 10**18)
    plan = ex.plan("ob-zero")
    with pytest.raises(ExecutorError, match="balance is 0"):
        ex.execute_plan(plan, lambda u, k: "0x1", lambda h: {"status": 1})


# --- quote decoding -------------------------------------------------------------

def test_decode_quote_round_trip():
    from eth_abi import encode as _enc
    raw = _enc(["uint256", "uint256"], [123456, 210000])
    out, gas = FeeConversionExecutor._decode_quote(raw)
    assert (out, gas) == (123456, 210000)
    with pytest.raises(ExecutorError):
        FeeConversionExecutor._decode_quote(b"\x01\x02")
