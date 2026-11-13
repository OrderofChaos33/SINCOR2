"""VERIFIED-3: slash_bps bound enforcement.

StakeLedger.slash() must reject slash_bps > 10000 (100%). Without the bound,
20000 bps would slash 200% and drive the ledger negative.
"""
from __future__ import annotations

import os

import pytest

from sincor2.onchain.stake_ledger import StakeLedger

TEST_ADJUDICATOR = "test-adjudicator-id"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    """Fresh ledger with an agent holding locked stake."""
    monkeypatch.setenv("SINCOR_ADJUDICATOR_ID", TEST_ADJUDICATOR)
    led = StakeLedger(path=str(tmp_path / "ledger.json"))
    # Deposit and directly set up a lock for the task
    led.deposit("agent-1", 1000, reference="test-deposit")
    rec = led._agent("agent-1")
    rec["locks"]["task-1"] = "1000"
    led._save()
    return led


def test_slash_bps_10000_succeeds(ledger):
    """10000 bps = 100% = maximum allowed; slashes the full lock."""
    result = ledger.slash("agent-1", "task-1", 10000, "ghost",
                          adjudicator=TEST_ADJUDICATOR)
    assert result["slashed_wei"] == "1000"


def test_slash_bps_10001_raises(ledger):
    """10001 bps exceeds 100%; must raise ValueError."""
    with pytest.raises(ValueError, match="slash_bps must be within"):
        ledger.slash("agent-1", "task-1", 10001, "ghost",
                     adjudicator=TEST_ADJUDICATOR)


def test_slash_bps_20000_raises(ledger):
    """20000 bps = 200%; must raise ValueError, ledger must stay non-negative."""
    with pytest.raises(ValueError, match="slash_bps must be within"):
        ledger.slash("agent-1", "task-1", 20000, "ghost",
                     adjudicator=TEST_ADJUDICATOR)
    # Ledger state unchanged: deposit still intact
    rec = ledger._agent("agent-1")
    assert int(rec["deposited_wei"]) >= 0


def test_slash_bps_zero_raises(ledger):
    """0 bps is not a valid slash; must raise ValueError."""
    with pytest.raises(ValueError, match="slash_bps must be within"):
        ledger.slash("agent-1", "task-1", 0, "ghost",
                     adjudicator=TEST_ADJUDICATOR)


def test_slash_bps_negative_raises(ledger):
    """Negative bps must raise ValueError."""
    with pytest.raises(ValueError, match="slash_bps must be within"):
        ledger.slash("agent-1", "task-1", -100, "ghost",
                     adjudicator=TEST_ADJUDICATOR)
