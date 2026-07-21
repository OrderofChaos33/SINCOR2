#!/usr/bin/env python3
"""
Tests for the vault event listener (log decoding + restart-safe polling).

Run:  python -m pytest verticals/trading/polyclaw/test_vault_event_listener.py -q
"""

from __future__ import annotations

from verticals.trading.polyclaw.vault_event_listener import (
    BASE_USDC,
    TOPICS,
    VaultEventListener,
    decode_log,
)

LP = "0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a"


def _topic_addr(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def _topic_uint(v: int) -> str:
    return "0x" + v.to_bytes(32, "big").hex()


def _data(*vals: int) -> str:
    return "0x" + "".join(v.to_bytes(32, "big").hex() for v in vals)


def _log(name: str, topics, data: str, block: int = 100) -> dict:
    return {
        "topics": [TOPICS[name], *topics],
        "data": data,
        "transactionHash": "0xdeadbeef",
        "blockNumber": hex(block),
        "logIndex": "0x0",
    }


# ----------------------------------------------------------------------
# decode_log
# ----------------------------------------------------------------------
def test_decode_settled():
    log = _log(
        "Settled",
        [_topic_addr(LP), _topic_uint(0), _topic_addr(BASE_USDC)],
        _data(2_960_000_000, 266_400_000),  # 2960 USDC principal, 266.4 fee
    )
    e = decode_log(log)
    assert e["event"] == "Settled"
    assert e["lp"].lower() == LP.lower()
    assert e["strategyId"] == 0
    assert e["principal"] == 2960.0
    assert e["fee"] == 266.4
    assert e["blockNumber"] == 100


def test_decode_drawdown():
    log = _log(
        "DrawDown",
        [_topic_addr(LP), _topic_uint(1), _topic_addr(BASE_USDC)],
        _data(500_000_000),
    )
    e = decode_log(log)
    assert e["event"] == "DrawDown"
    assert e["strategyId"] == 1
    assert e["amount"] == 500.0


def test_decode_deposit_withdraw():
    for name in ("Deposited", "Withdrawn"):
        log = _log(name, [_topic_addr(LP), _topic_addr(BASE_USDC)],
                   _data(1_000_000_000))
        e = decode_log(log)
        assert e["event"] == name and e["amount"] == 1000.0


def test_decode_fees_harvested():
    log = _log(
        "FeesHarvested",
        [_topic_addr(LP), _topic_addr(BASE_USDC)],
        _data(42_000_000, int(LP, 16)),
    )
    e = decode_log(log)
    assert e["event"] == "FeesHarvested"
    assert e["amount"] == 42.0
    assert e["to"].lower() == LP.lower()


def test_decode_unknown_topic_returns_none():
    assert decode_log({"topics": ["0x" + "00" * 32], "data": "0x"}) is None
    assert decode_log({"topics": [], "data": "0x"}) is None


# ----------------------------------------------------------------------
# poll_once: restart-safe, confirmation-aware, feeds ingest
# ----------------------------------------------------------------------
class FakeListener(VaultEventListener):
    def __init__(self, ingest, head, logs, last=None, tmp_path=None):
        super().__init__(ingest=ingest, state_path=str(tmp_path / "state.json"),
                         confirmations=3)
        self._head = head
        self._logs = logs
        self._last = last

    def _head_block(self):
        return self._head

    def _get_logs(self, from_block, to_block):
        return [l for l in self._logs
                if from_block <= int(l["blockNumber"], 16) <= to_block]

    def _load_last_block(self, head):
        # honour persisted state once written; only seed from self._last
        if self.state_path.exists():
            return super()._load_last_block(head)
        return self._last if self._last is not None else super()._load_last_block(head)


def _settled_log(block: int) -> dict:
    return _log("Settled",
                [_topic_addr(LP), _topic_uint(0), _topic_addr(BASE_USDC)],
                _data(1_000_000_000, 50_000_000), block=block)


def test_poll_once_ingests_confirmed_events_only(tmp_path):
    seen = []
    logs = [_settled_log(90), _settled_log(96), _settled_log(99)]
    lst = FakeListener(seen.append, head=100, logs=logs, last=89, tmp_path=tmp_path)
    n = lst.poll_once()
    # safe_head = 97 -> blocks 90 and 96 in range, 99 excluded (unconfirmed)
    assert n == 2
    assert [e["blockNumber"] for e in seen] == [90, 96]


def test_poll_once_persists_progress(tmp_path):
    seen = []
    lst = FakeListener(seen.append, head=100, logs=[_settled_log(90)],
                       last=89, tmp_path=tmp_path)
    assert lst.poll_once() == 1
    # second poll with no new blocks: nothing re-ingested
    assert lst.poll_once() == 0
    assert len(seen) == 1


def test_poll_once_survives_ingest_failure(tmp_path):
    calls = []

    def flaky(event):
        calls.append(event)
        if len(calls) == 1:
            raise RuntimeError("boom")

    logs = [_settled_log(90), _settled_log(91)]
    lst = FakeListener(flaky, head=100, logs=logs, last=89, tmp_path=tmp_path)
    n = lst.poll_once()
    assert n == 1  # second event still ingested despite first failing
    assert len(calls) == 2
