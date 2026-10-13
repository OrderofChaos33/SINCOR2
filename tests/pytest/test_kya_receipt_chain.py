"""Hash-chain tamper-evidence for the KYA ReceiptBook."""

from __future__ import annotations

import pytest

from sincor2.kya.receipts import ReceiptBook
from sincor2.kya.registry import record_hash


@pytest.fixture()
def book(monkeypatch, tmp_path):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    return ReceiptBook()


def _day(book, date, pnl=10.0, **kw):
    body = {"date": date, "trades": 4, "wins": 3, "pnl": pnl,
            "max_dd": 1.0, "simulated": True}
    body.update(kw)
    return book.record_day(body)


def _legacy_row(date, pnl=10.0):
    """A row in the pre-chaining format: no prev_hash."""
    row = {"date": date, "trades": 4, "wins": 3, "pnl": pnl, "max_dd": 1.0,
           "simulated": True, "notes": "", "ts": 1, "win_rate": 0.75}
    row["attestation_hash"] = record_hash(row)
    return row


def test_two_days_chain_correctly(book):
    d1 = _day(book, "2026-10-10")
    d2 = _day(book, "2026-10-11")
    assert d1["prev_hash"] == ""
    assert d2["prev_hash"] == d1["attestation_hash"]
    assert d2["attestation_hash"] != d1["attestation_hash"]
    v = book.verify_chain()
    assert v == {"ok": True, "days": 2, "break_at": None}


def test_tampering_past_day_pnl_breaks_at_that_date(book):
    _day(book, "2026-10-10", pnl=10.0)
    _day(book, "2026-10-11", pnl=20.0)
    _day(book, "2026-10-12", pnl=30.0)
    # Operator silently rewrites history (in-memory or on-disk edit):
    with book.lock:
        book.days[0]["pnl"] = 999.0
    v = book.verify_chain()
    assert v["ok"] is False
    assert v["days"] == 3
    assert v["break_at"] == "2026-10-10"


def test_deleting_middle_day_breaks_chain(book):
    _day(book, "2026-10-10")
    _day(book, "2026-10-11")
    _day(book, "2026-10-12")
    with book.lock:
        book.days = [d for d in book.days if d["date"] != "2026-10-11"]
    v = book.verify_chain()
    assert v["ok"] is False
    # 2026-10-12 still points at the deleted day's hash
    assert v["break_at"] == "2026-10-12"


def test_out_of_order_insert_chains_by_date_order(book):
    _day(book, "2026-10-12")
    _day(book, "2026-10-10")
    _day(book, "2026-10-11")
    by_date = {d["date"]: d for d in book.days}
    assert by_date["2026-10-10"]["prev_hash"] == ""
    assert by_date["2026-10-11"]["prev_hash"] == by_date["2026-10-10"]["attestation_hash"]
    assert by_date["2026-10-12"]["prev_hash"] == by_date["2026-10-11"]["attestation_hash"]
    assert book.verify_chain()["ok"] is True


def test_same_date_rerecord_replaces_and_rechains(book):
    d2 = _day(book, "2026-10-11", pnl=20.0)
    _day(book, "2026-10-10")
    _day(book, "2026-10-12")
    old_d2_hash = d2["attestation_hash"]
    new_d2 = _day(book, "2026-10-11", pnl=1234.0)
    assert new_d2["attestation_hash"] != old_d2_hash
    assert len(book.days) == 3  # replaced, not duplicated
    by_date = {d["date"]: d for d in book.days}
    # successor's prev_hash must have been updated to the new hash
    assert by_date["2026-10-12"]["prev_hash"] == new_d2["attestation_hash"]
    assert book.verify_chain() == {"ok": True, "days": 3, "break_at": None}


def test_verify_chain_empty_book(book):
    assert book.verify_chain() == {"ok": True, "days": 0, "break_at": None}


def test_scorecard_surfaces_chain_ok(book):
    _day(book, "2026-10-10")
    _day(book, "2026-10-11")
    sc = book.scorecard()
    assert sc["chain_ok"] is True
    with book.lock:
        book.days[0]["pnl"] = -1.0
    sc = book.scorecard()
    assert sc["chain_ok"] is False


def test_legacy_row_as_earliest_date_passes(book):
    with book.lock:
        book.days = [_legacy_row("2026-10-10")]
    assert book.verify_chain() == {"ok": True, "days": 1, "break_at": None}


def test_legacy_row_as_non_earliest_breaks(book):
    with book.lock:
        book.days = [_legacy_row("2026-10-10"), _legacy_row("2026-10-11")]
    v = book.verify_chain()
    assert v["ok"] is False
    assert v["break_at"] == "2026-10-11"


def test_legacy_earliest_row_tamper_breaks(book):
    with book.lock:
        book.days = [_legacy_row("2026-10-10")]
    assert book.verify_chain()["ok"] is True
    with book.lock:
        book.days[0]["pnl"] = 777.0  # tamper without recomputing hash
    v = book.verify_chain()
    assert v["ok"] is False
    assert v["break_at"] == "2026-10-10"
