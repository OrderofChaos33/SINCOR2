"""Fee-event listener tests (build-out item 33) — all offline, fake web3.

Adversarial coverage:
* fake/misbehaving RPC logs (wrong token, wrong recipient, wrong topic)
  are rejected client-side and never become obligations;
* chain reorgs rewind + rescan without double-recording (idempotency);
* RPC outages fail closed: cursor never advances, no obligations recorded;
* unconfirmed events (< confirmations deep) are never processed;
* dust inflows are observed but not recorded.
"""

import pytest

from src.sincor2.onchain.fee_conversion_executor import (
    ConversionLedger,
    STATUS_PENDING,
)
from src.sincor2.onchain.fee_event_listener import (
    TRANSFER_TOPIC,
    FeeEventListener,
    FeeListenerConfig,
    _topic_address,
)

TOKEN = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
RECIPIENT = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
PAYER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _log(tx_hash, block, log_index, value_wei, to=RECIPIENT,
         token=TOKEN, topic0=TRANSFER_TOPIC):
    return {
        "blockNumber": block,
        "blockHash": f"0xhash{block:04d}",
        "transactionHash": tx_hash,
        "logIndex": log_index,
        "address": token,
        "topics": [topic0, _topic_address(PAYER), _topic_address(to)],
        "data": hex(value_wei),
    }


class _FakeEth:
    def __init__(self):
        self.blocks = {}          # n -> hash hex
        self.logs = []            # raw log dicts
        self.fail_next = None     # method name to raise on

    def _maybe_fail(self, name):
        if self.fail_next == name:
            raise ConnectionError(f"fake RPC outage in {name}")

    def get_block_number(self):
        self._maybe_fail("get_block_number")
        return max(self.blocks)

    def get_block(self, n):
        self._maybe_fail("get_block")
        return {"hash": self.blocks[n], "number": n}

    def get_logs(self, f):
        self._maybe_fail("get_logs")
        return [l for l in self.logs
                if f["fromBlock"] <= l["blockNumber"] <= f["toBlock"]]


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()


def _chain(w3, latest, tip_hashes=None):
    for n in range(latest + 1):
        w3.eth.blocks[n] = (tip_hashes or {}).get(n, f"0xhash{n:04d}")


def _listener(w3, tmp_path, **kw):
    cfg = FeeListenerConfig(
        rpc_url="https://example.invalid",
        token=TOKEN,
        fee_recipient=RECIPIENT,
        confirmations=kw.pop("confirmations", 2),
        poll_interval_secs=1,
        cursor_path=str(tmp_path / "cursor.json"),
        **kw,
    )
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    return FeeEventListener(config=cfg, ledger=ledger,
                            w3_factory=lambda: w3)


# --- happy path ------------------------------------------------------------


def test_records_fee_obligation_for_confirmed_inflow(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [
        _log("0xtx01", 5, 0, 1_000 * 10**18),
        _log("0xtx02", 6, 1, 2_000 * 10**18),
    ]
    L = _listener(w3, tmp_path, backfill_blocks=10)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 2
    # 5 % of each inflow, in wei
    pending = L.ledger.pending()
    assert len(pending) == 2
    amounts = sorted(int(o["amount_wei"]) for o in pending)
    assert amounts == [50 * 10**18, 100 * 10**18]
    for o in pending:
        assert o["status"] == STATUS_PENDING
        assert o["source"]["listener"] == "fee_event_listener"
        assert o["source"]["tx_hash"] in ("0xtx01", "0xtx02")


def test_unconfirmed_events_are_not_processed(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    # confirmations=2 -> tip=8; block-9 event must wait
    w3.eth.logs = [_log("0xtx09", 9, 0, 1_000 * 10**18)]
    L = _listener(w3, tmp_path)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 0
    assert L.ledger.pending() == []
    # cursor sits at the tip, not beyond
    assert L.load_cursor()["block"] == 8


def test_resume_after_restart_has_no_gap_no_replay(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtx01", 5, 0, 1_000 * 10**18)]
    L = _listener(w3, tmp_path, backfill_blocks=10)
    assert L.run_once()["recorded_count"] == 1
    # "restart": brand-new listener, same paths
    L2 = _listener(w3, tmp_path)
    out = L2.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 0
    assert len(L2.ledger.pending()) == 1


# --- adversarial: fake events ------------------------------------------------


def test_wrong_recipient_log_is_rejected(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    # RPC returns a Transfer to somebody else; client-side check must drop it.
    w3.eth.logs = [_log("0xtxEvil", 5, 0, 1_000 * 10**18, to=OTHER)]
    L = _listener(w3, tmp_path)
    events = L.fetch_transfer_events(w3, 1, 8)
    assert events == []
    assert L.run_once()["recorded_count"] == 0
    assert L.ledger.pending() == []


def test_wrong_token_log_is_rejected(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtxEvil", 5, 0, 1_000 * 10**18, token=OTHER)]
    L = _listener(w3, tmp_path)
    assert L.fetch_transfer_events(w3, 1, 8) == []


def test_wrong_topic_log_is_rejected(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtxEvil", 5, 0, 1_000 * 10**18,
                        topic0="0x" + "ab" * 32)]
    L = _listener(w3, tmp_path)
    assert L.fetch_transfer_events(w3, 1, 8) == []


def test_malformed_log_is_skipped_not_fatal(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [
        {"blockNumber": 5, "address": TOKEN},  # missing topics/data
        _log("0xtx01", 5, 0, 1_000 * 10**18),
    ]
    L = _listener(w3, tmp_path, backfill_blocks=10)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 1


# --- adversarial: reorgs ------------------------------------------------------


def test_reorg_rewinds_and_never_double_records(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtx01", 5, 0, 1_000 * 10**18)]
    L = _listener(w3, tmp_path, backfill_blocks=10)
    first = L.run_once()
    assert first["recorded_count"] == 1
    cursor = L.load_cursor()
    assert cursor["block"] == 8

    # Reorg: block 8 now has a different hash; the old event is still valid
    # (same tx) — rescan must be idempotent.
    w3.eth.blocks[8] = "0xreorged0008"
    w3.eth.logs.append(_log("0xtx02", 7, 0, 500 * 10**18))

    out = L.run_once()
    assert out["ok"] is True
    assert out["reorg"] is True
    # Only the NEW event is newly recorded; the rescan of 0xtx01 is a
    # no-op, reported honestly as a re-encounter rather than new money.
    assert out["recorded_count"] == 1
    assert out["reencountered"] == 1
    assert len(L.ledger.pending()) == 2
    ids = [o["id"] for o in L.ledger.pending()]
    assert len(set(ids)) == 2  # no duplicates


def test_idempotent_rerun_of_same_range(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtx01", 5, 0, 1_000 * 10**18)]
    L = _listener(w3, tmp_path)
    L.run_once()
    # Force a rescan of the same range by rewinding the cursor manually.
    L.save_cursor(4, w3.eth.blocks[4])
    out = L.run_once()
    assert out["ok"] is True
    assert len(L.ledger.pending()) == 1  # still exactly one obligation


# --- adversarial: RPC outages --------------------------------------------------


def test_rpc_outage_fails_closed_without_advancing_cursor(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtx01", 5, 0, 1_000 * 10**18)]
    L = _listener(w3, tmp_path, backfill_blocks=10)
    assert L.run_once()["recorded_count"] == 1
    cursor_before = L.load_cursor()

    # Chain advances with a new event; then the RPC dies mid-cycle.
    _chain(w3, 12)
    w3.eth.logs.append(_log("0xtx02", 9, 0, 2_000 * 10**18))
    w3.eth.fail_next = "get_logs"
    out = L.run_once()
    assert out["ok"] is False
    assert "error" in out
    assert L.load_cursor() == cursor_before  # cursor NOT advanced
    assert len(L.ledger.pending()) == 1      # no partial recording

    # Recovery: next run retries the same range and records the event.
    w3.eth.fail_next = None
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 1
    assert len(L.ledger.pending()) == 2


def test_outage_on_block_number_fails_closed(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    L = _listener(w3, tmp_path)
    w3.eth.fail_next = "get_block_number"
    out = L.run_once()
    assert out["ok"] is False
    assert L.load_cursor() is None


# --- dust ----------------------------------------------------------------------


def test_dust_inflow_is_observed_but_not_recorded(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    w3.eth.logs = [
        _log("0xdust", 5, 0, 100),                    # 100 wei < min
        _log("0xtx01", 5, 1, 1_000 * 10**18),
    ]
    L = _listener(w3, tmp_path, min_recordable_wei=1_000, backfill_blocks=10)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 1
    assert out["skipped_dust"] == 1
    assert len(L.ledger.pending()) == 1


# --- obligation identity -------------------------------------------------------


def test_two_identical_transfers_in_one_tx_record_two_obligations(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 10)
    # Same tx, same value, different log indexes: two distinct inflows.
    w3.eth.logs = [
        _log("0xtxSame", 5, 0, 1_000 * 10**18),
        _log("0xtxSame", 5, 1, 1_000 * 10**18),
    ]
    L = _listener(w3, tmp_path, backfill_blocks=10)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 2
    assert len(L.ledger.pending()) == 2


# --- no keys, no broadcast -------------------------------------------------------


# --- regression: web3.py v8 checksum enforcement at the RPC boundary --------
#
# Bug (found live 2026-10-24): the in-repo AXIOM_TOKEN constant is lowercase
# and was passed straight into eth_getLogs.  web3.py v8 validates EIP-55
# checksums on the filter address BEFORE any network I/O and raises
# InvalidAddress for non-checksum input, so run_once() failed closed on
# every cycle against a real client (the plain _FakeEth above never
# enforced this, which is why the suite missed it).  The fakes below run
# the REAL web3.py v8 validation: _ChecksumEnforcingEth calls
# web3._utils.validation.validate_address — the exact check
# Eth.get_logs applies — and one test drives a real Web3 client backed
# by a stub provider end to end.

from web3 import Web3
from web3.providers import BaseProvider
from web3.types import RPCResponse


class _ChecksumEnforcingEth(_FakeEth):
    """_FakeEth + the address validation web3.py v8 applies in get_logs."""

    def __init__(self):
        super().__init__()
        self.seen_filters = []

    def get_logs(self, f):
        from web3._utils.validation import validate_address

        validate_address(f["address"])  # raises InvalidAddress if not checksummed
        self.seen_filters.append(dict(f))
        return super().get_logs(f)


class _ChecksumW3:
    def __init__(self):
        self.eth = _ChecksumEnforcingEth()


class _StubProvider(BaseProvider):
    """Answers every RPC with an empty result; validation happens in web3."""

    def make_request(self, method, params):
        return RPCResponse(id=0, jsonrpc="2.0", result=[])


def test_real_web3_v8_rejects_lowercase_filter_address():
    # Sanity anchor for the regression tests: the real client really does
    # reject the lowercase in-repo token and accept its checksum form.
    w3 = Web3(provider=_StubProvider())
    with pytest.raises(Exception):
        w3.eth.get_logs({"fromBlock": 1, "toBlock": 2, "address": TOKEN})
    assert w3.eth.get_logs(
        {"fromBlock": 1, "toBlock": 2,
         "address": Web3.to_checksum_address(TOKEN)}
    ) == []


def test_lowercase_token_and_recipient_survive_checksum_enforcing_rpc(tmp_path):
    w3 = _ChecksumW3()
    _chain(w3, 10)
    w3.eth.logs = [_log("0xtx01", 5, 0, 1_000 * 10**18,
                        to=RECIPIENT.lower(), token=TOKEN)]
    # TOKEN is the lowercase in-repo AXIOM_TOKEN value; pass the recipient
    # lowercase too (e.g. a lowercase SINCOR_FEE_RECIPIENT env value).
    L = _listener(w3, tmp_path, backfill_blocks=10)
    L.config.fee_recipient = RECIPIENT.lower()
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 1
    # What actually reached the RPC boundary must be checksummed.
    assert w3.eth.seen_filters, "get_logs was never called"
    sent = w3.eth.seen_filters[0]["address"]
    assert sent == Web3.to_checksum_address(TOKEN)
    # And that exact filter passes a REAL web3 v8 client untouched.
    real = Web3(provider=_StubProvider())
    assert real.eth.get_logs(w3.eth.seen_filters[0]) == []


def test_checksummed_config_is_idempotent_at_rpc_boundary(tmp_path):
    # Production workaround compatibility: the running service already
    # passes a checksummed FEE_LISTENER_TOKEN — normalization must leave
    # checksummed token AND recipient byte-identical.
    w3 = _ChecksumW3()
    _chain(w3, 10)
    checksum_token = Web3.to_checksum_address(TOKEN)
    w3.eth.logs = [_log("0xtx01", 5, 0, 1_000 * 10**18,
                        token=checksum_token)]
    cfg = FeeListenerConfig(
        rpc_url="https://example.invalid",
        token=checksum_token,
        fee_recipient=RECIPIENT,  # already checksummed
        confirmations=2,
        poll_interval_secs=1,
        cursor_path=str(tmp_path / "cursor.json"),
        backfill_blocks=10,
    )
    ledger = ConversionLedger(str(tmp_path / "ledger.json"))
    L = FeeEventListener(config=cfg, ledger=ledger, w3_factory=lambda: w3)
    out = L.run_once()
    assert out["ok"] is True
    assert out["recorded_count"] == 1
    assert w3.eth.seen_filters[0]["address"] == checksum_token


def test_module_handles_no_key_material():
    import src.sincor2.onchain.fee_event_listener as mod

    src = open(mod.__file__, encoding="utf-8").read().lower()
    for banned in ("private_key", "eth_account", "sign_transaction",
                   "send_raw_transaction", "send_transaction"):
        assert banned not in src, f"listener must never touch: {banned}"
