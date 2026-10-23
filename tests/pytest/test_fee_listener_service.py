"""Fee-listener service tests (PRE-5) — scripts/run_fee_listener.py entry point.

All offline with a fake web3 provider: no mainnet, no real keys, nothing
broadcasts.  Covers the service wiring (env config, --once mode, fail-closed
startup) plus the money-path full loop:

    mock RPC emits Transfer into the fee recipient
      -> 12 confirmations elapse
      -> pending obligation recorded at exactly 5 %
      -> executor still disarmed (nothing converts, nothing moves)
"""

import importlib.util
import json
import os

import pytest

from src.sincor2.onchain.constants import TREASURY
from src.sincor2.onchain.fee_conversion_executor import (
    STATUS_PENDING,
    ExecutorError,
    FeeConversionConfig,
    FeeConversionExecutor,
)
from src.sincor2.onchain.fee_event_listener import (
    TRANSFER_TOPIC,
    FeeListenerConfig,
    _topic_address,
)

SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..",
    "scripts",
    "run_fee_listener.py",
)

TOKEN = "0x4200000000000000000000000000000000000042"
PAYER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
AXM = 10**18


def _load_script():
    spec = importlib.util.spec_from_file_location("run_fee_listener", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _log(tx_hash, block, log_index, value_wei, to, token=TOKEN):
    return {
        "blockNumber": block,
        "blockHash": f"0xhash{block:04d}",
        "transactionHash": tx_hash,
        "logIndex": log_index,
        "address": token,
        "topics": [TRANSFER_TOPIC, _topic_address(PAYER), _topic_address(to)],
        "data": hex(value_wei),
    }


class _FakeEth:
    def __init__(self):
        self.blocks = {}
        self.logs = []

    def get_block_number(self):
        return max(self.blocks)

    def get_block(self, n):
        return {"hash": self.blocks[n], "number": n}

    def get_logs(self, f):
        return [
            l
            for l in self.logs
            if f["fromBlock"] <= l["blockNumber"] <= f["toBlock"]
        ]


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()


def _chain(w3, latest):
    for n in range(latest + 1):
        w3.eth.blocks[n] = f"0xhash{n:04d}"


@pytest.fixture()
def script_env(tmp_path, monkeypatch):
    """Base env for service tests; returns (tmp_path, script module)."""
    monkeypatch.setenv("BASE_RPC_URL", "https://example.invalid")
    monkeypatch.setenv("FEE_LISTENER_CURSOR_PATH", str(tmp_path / "cursor.json"))
    monkeypatch.setenv("SINCOR_FEE_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.delenv("FEE_LISTENER_RPC_URL", raising=False)
    monkeypatch.delenv("SINCOR_FEE_RECIPIENT", raising=False)
    monkeypatch.delenv("FEE_LISTENER_START_BLOCK", raising=False)
    monkeypatch.delenv("FEE_LISTENER_BACKFILL_BLOCKS", raising=False)
    monkeypatch.delenv("FEE_LISTENER_CONFIRMATIONS", raising=False)
    mod = _load_script()
    return tmp_path, mod


def _inject_fake(mod, monkeypatch, w3, **cfg_kw):
    """Replace the script's build_listener with one using the fake provider."""

    def fake_build():
        cfg = FeeListenerConfig(
            rpc_url="https://example.invalid",
token=TOKEN,
            fee_recipient=cfg_kw.get("fee_recipient", TREASURY),
            confirmations=cfg_kw.get("confirmations", 12),
            poll_interval_secs=1,
            cursor_path=cfg_kw.get("cursor_path"),
        )
        from src.sincor2.onchain.fee_conversion_executor import ConversionLedger

        ledger = ConversionLedger(cfg_kw.get("ledger_path"))
        from src.sincor2.onchain.fee_event_listener import FeeEventListener

        return FeeEventListener(config=cfg, ledger=ledger,
                                w3_factory=lambda: w3)

    monkeypatch.setattr(mod, "build_listener", fake_build)
    return fake_build


# --- service wiring ----------------------------------------------------------


def test_main_once_records_confirmed_fee_obligation(script_env, monkeypatch, capsys):
    tmp_path, mod = script_env
    w3 = _FakeW3()
    _chain(w3, 30)
    w3.eth.logs = [_log("0xsvc01", 15, 0, 1_000 * AXM, TREASURY)]
    monkeypatch.setenv("FEE_LISTENER_START_BLOCK", "10")
    _inject_fake(
        mod, monkeypatch, w3,
        cursor_path=str(tmp_path / "cursor.json"),
        ledger_path=str(tmp_path / "ledger.json"),
    )
    rc = mod.main(["--once"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["recorded_count"] == 1


def test_main_once_waits_twelve_confirmations(script_env, monkeypatch):
    tmp_path, mod = script_env
    w3 = _FakeW3()
    _chain(w3, 20)  # event at block 15 is only 5 deep — unconfirmed
    w3.eth.logs = [_log("0xsvc02", 15, 0, 1_000 * AXM, TREASURY)]
    monkeypatch.setenv("FEE_LISTENER_START_BLOCK", "10")
    fake_build = _inject_fake(
        mod, monkeypatch, w3,
        cursor_path=str(tmp_path / "cursor.json"),
        ledger_path=str(tmp_path / "ledger.json"),
    )
    assert mod.main(["--once"]) == 0
    assert fake_build().ledger.pending() == []

    _chain(w3, 30)  # event now 15 deep — past the 12-confirmation bar
    assert mod.main(["--once"]) == 0
    pending = fake_build().ledger.pending()
    assert len(pending) == 1
    assert int(pending[0]["amount_wei"]) == 50 * AXM  # exactly 5 %


def test_main_fails_closed_without_rpc_url(script_env, monkeypatch, caplog):
    _, mod = script_env
    monkeypatch.delenv("BASE_RPC_URL", raising=False)
    with caplog.at_level("ERROR", logger="sincor.treasury.fee_listener.service"):
        rc = mod.main(["--once"])
    assert rc == 2
    assert any("BASE_RPC_URL" in r.message for r in caplog.records)


def test_main_honors_env_fee_recipient_and_defaults_to_treasury(
    script_env, monkeypatch
):
    _, mod = script_env
    custom = "0xcccccccccccccccccccccccccccccccccccccccc"
    monkeypatch.setenv("SINCOR_FEE_RECIPIENT", custom)
    # The script builds its listener purely from env — the module-level
    # config must pick up the override…
    assert FeeListenerConfig().fee_recipient == custom
    monkeypatch.delenv("SINCOR_FEE_RECIPIENT", raising=False)
    # …and fall back to the canonical treasury otherwise (no treasury
    # config is touched by the service).
    assert FeeListenerConfig().fee_recipient == TREASURY


# --- money-path full loop ----------------------------------------------------


def test_full_loop_transfer_to_pending_obligation_executor_disarmed(
    tmp_path, monkeypatch
):
    """Full loop on the fee path with a mock RPC:

    Transfer into the fee recipient -> 12 confirmations elapse ->
    pending obligation recorded at exactly 5 % -> executor still disarmed,
    nothing converts, nothing moves.
    """
    from src.sincor2.onchain.fee_conversion_executor import ConversionLedger
    from src.sincor2.onchain.fee_event_listener import FeeEventListener

    w3 = _FakeW3()
    _chain(w3, 30)
    inflow_wei = 2_000 * AXM
    w3.eth.logs = [_log("0xloop01", 15, 0, inflow_wei, TREASURY)]

    cursor_path = str(tmp_path / "cursor.json")
    ledger_path = str(tmp_path / "ledger.json")
    monkeypatch.setenv("FEE_LISTENER_CURSOR_PATH", cursor_path)
    monkeypatch.setenv("SINCOR_FEE_LEDGER_PATH", ledger_path)

    cfg = FeeListenerConfig(
        rpc_url="https://example.invalid",
token=TOKEN,
        fee_recipient=TREASURY,
        confirmations=12,
        poll_interval_secs=1,
        cursor_path=cursor_path,
        start_block=10,
    )
    ledger = ConversionLedger(ledger_path)
    listener = FeeEventListener(config=cfg, ledger=ledger,
                                w3_factory=lambda: w3)

    # Handoff 1: confirmed Transfer observed and recorded.
    out = listener.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 1

    pending = ledger.pending()
    assert len(pending) == 1
    ob = pending[0]
    # Handoff 2: obligation is exactly 5 % of the inflow…
    assert int(ob["amount_wei"]) == (inflow_wei * 500) // 10_000
    # …queued as pending only…
    assert ob["status"] == STATUS_PENDING
    assert ob["source"]["tx_hash"] == "0xloop01"
    assert ob["source"]["fee_bps"] == 500
    assert all(o["status"] == STATUS_PENDING for o in ledger.pending())

    # Handoff 3: the executor is DISARMED and cannot convert it.
    exec_cfg = FeeConversionConfig(
        rpc_url="https://example.invalid", forwarder=PAYER
    )
    assert exec_cfg.armed is False
    executor = FeeConversionExecutor(exec_cfg, ledger=ledger)

    signed = []

    def _sign(unsigned, kind):  # must never be reached
        signed.append(kind)
        return "0xdeadbeef"

    def _wait(tx_hash):
        return {"status": 1}

    plan_stub = {
        "obligation_id": ob["id"],
        "quote": {"token_out": "USDC"},
        "txs": [],
    }
    with pytest.raises(ExecutorError, match="live conversion is blocked"):
        executor.execute_plan(plan_stub, _sign, _wait, dry_run=False)
    assert signed == []  # no signer was ever touched

    # …and the obligation is still pending afterwards: nothing moved.
    assert ledger.get(ob["id"])["status"] == STATUS_PENDING
