"""Base Denim window sizing for the fee-event listener — offline, fake web3.

Base Denim (activated 2026-11-05) cut Base block time from ~2 s to
200 ms (5 blocks/sec).  The listener's two window constants were sized
for the 2 s era:

* ``REORG_WINDOW = 64`` covered ~2 min of chain history; at 200 ms
  blocks it covers only ~13 s — too shallow to rewind past a reorg and
  rescan the affected range.
* ``MAX_BLOCKS_PER_SCAN = 2_000`` covered ~67 min of chain per scan; at
  200 ms blocks it covers only ~6.7 min, so after a listener outage the
  catch-up needs one scan per ~7 min of downtime (at the 30 s poll
  interval that drains a backlog at only ~13x real time) and every
  mid-catch-up RPC failure restarts a much smaller unit of progress.

Denim-era targets pinned here: REORG_WINDOW = 640 blocks (~2 min at
200 ms) and MAX_BLOCKS_PER_SCAN = 10_000 blocks (~33 min per scan, the
eth_getLogs range public Base RPCs accept).
"""

from src.sincor2.onchain.fee_conversion_executor import ConversionLedger
from src.sincor2.onchain.fee_event_listener import (
    MAX_BLOCKS_PER_SCAN,
    REORG_WINDOW,
    FeeEventListener,
    FeeListenerConfig,
)

TOKEN = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
RECIPIENT = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"

DENIM_BLOCK_SECS = 0.2  # Base Denim: 200 ms blocks


class _FakeEth:
    def __init__(self):
        self.blocks = {}
        self.logs = []

    def get_block_number(self):
        return max(self.blocks)

    def get_block(self, n):
        return {"hash": self.blocks[n], "number": n}

    def get_logs(self, f):
        return [l for l in self.logs
                if f["fromBlock"] <= l["blockNumber"] <= f["toBlock"]]


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()


def _chain(w3, latest):
    for n in range(latest + 1):
        w3.eth.blocks[n] = f"0xhash{n:05d}"


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


# --- constant sizing ----------------------------------------------------------


def test_reorg_window_covers_two_minutes_at_denim_block_time():
    assert REORG_WINDOW == 640
    assert REORG_WINDOW * DENIM_BLOCK_SECS >= 120  # ~2 minutes of history


def test_scan_cap_covers_thirty_minutes_at_denim_block_time():
    assert MAX_BLOCKS_PER_SCAN == 10_000
    assert MAX_BLOCKS_PER_SCAN * DENIM_BLOCK_SECS >= 1_800  # ~33 min/scan


# --- behaviour: reorg rewind depth --------------------------------------------


def test_reorg_rewinds_full_denim_window(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 2_000)
    L = _listener(w3, tmp_path, backfill_blocks=2_000)
    first = L.run_once()
    assert first["ok"] is True
    assert L.load_cursor()["block"] == 1_998  # tip = 2000 - 2 confirmations

    # Reorg replaces the cursor block's hash.
    w3.eth.blocks[1_998] = "0xreorged01998"
    out = L.run_once()
    assert out["ok"] is True
    assert out["reorg"] is True
    # Rewind = REORG_WINDOW blocks: rescan starts at 1998 - 640 + 1.
    assert out["from_block"] == 1_359
    assert out["to_block"] == 1_998


# --- behaviour: catch-up span per scan -----------------------------------------


def test_single_scan_spans_full_catchup_window(tmp_path):
    w3 = _FakeW3()
    _chain(w3, 12_000)
    L = _listener(w3, tmp_path, start_block=0)
    out = L.run_once()
    assert out["ok"] is True
    # One scan advances the cursor the full MAX_BLOCKS_PER_SCAN window:
    # from_block(cursor -1) + 10_000 = block 9_999 (tip is 11_998).
    assert out["to_block"] == 9_999
    assert L.load_cursor()["block"] == 9_999
