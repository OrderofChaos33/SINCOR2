"""P03 volume step-up: Vickrey fee steps 500 -> 1000 bps at sustained volume.

Founder ratification (2026-11-16): the 5 % Vickrey platform fee is a
deliberate 50 % penetration discount to the researched 10 % market
median. It steps up to the market median (1000 bps) once monthly
clearing volume reaches $1,000,000 for 2 consecutive months — and not
before. The rate for a clearing is resolved from the clearing-volume
history recorded before it (an append-only JSONL ledger); the award
carries the rate actually applied.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if not (ROOT / "marketplace" / "discovery.py").exists() and "marketplace" not in sys.modules:
    pkg = types.ModuleType("marketplace")
    pkg.__path__ = [str(ROOT / "marketplace")]
    sys.modules["marketplace"] = pkg

from marketplace.contract_net.types import Invite, SealedBid  # noqa: E402
from marketplace.contract_net.vickrey import clear_vickrey, split_clearing_proceeds  # noqa: E402
from marketplace.contract_net.volume_ledger import (  # noqa: E402
    BASE_FEE_BPS,
    STEP_UP_FEE_BPS,
    fee_bps_for_volumes,
    monthly_volumes_usd,
    record_clearing,
    resolve_fee_bps,
)

# Fixed clock for deterministic month bucketing: "now" is mid-March 2027.
NOW = datetime(2027, 3, 15, 12, 0, tzinfo=timezone.utc)
FEB = datetime(2027, 2, 10, 12, 0, tzinfo=timezone.utc)
MAR = datetime(2027, 3, 2, 12, 0, tzinfo=timezone.utc)

ONE_MILLION_USD_MICRO = 1_000_000 * 1_000_000  # $1M in micro-AXM


def _seed(ledger: Path, when: datetime, micro_axm: int, fee_bps: int = 500) -> None:
    record_clearing(
        auction_id="0xseed",
        task_id="seed",
        clearing_price_micro_axm=micro_axm,
        fee_bps=fee_bps,
        cleared_at=when,
        ledger_path=ledger,
    )


def _bid(agent_id: str, price: int) -> SealedBid:
    return SealedBid(
        auction_id="0x" + "11" * 32,
        task_id="t",
        agent_id=agent_id,
        agent_wallet="0x" + "22" * 20,
        price=price,
        estimated_tokens=100,
        nonce=1,
        deadline=9_999_999_999,
        digest="0x" + "33" * 32,
        signature="0x" + "44" * 32,
        sig_type="eip712-hmac-sha256",
    )


def _invite(agent_id: str) -> Invite:
    return Invite(
        agent_id=agent_id, name=agent_id, cosine=0.9, junior=False,
        llm_invited=True, reason="top-k",
    )


def _clear(prices, ledger: Path, when: datetime = NOW):
    bids = [_bid(f"agent-{i}", p) for i, p in enumerate(prices)]
    return clear_vickrey(
        auction_id="0x" + "11" * 32,
        task_id="t",
        bids=bids,
        invites=[_invite(b.agent_id) for b in bids],
        junior_reserved=False,
        llm_calls_avoided=0,
        tokens_saved=0,
        cleared_at=when,
        ledger_path=ledger,
    )


# ---------------------------------------------------------------- resolver


def test_resolver_defaults_to_base_rate():
    assert fee_bps_for_volumes(0, 0) == BASE_FEE_BPS == 500
    assert fee_bps_for_volumes(500_000, 500_000) == 500


def test_resolver_boundary_just_below_threshold_stays_base():
    # $999,999.99 in either month is NOT >= $1,000,000.
    assert fee_bps_for_volumes("999999.99", "2000000") == 500
    assert fee_bps_for_volumes("2000000", "999999.99") == 500
    assert fee_bps_for_volumes(Decimal("999999.99"), Decimal("999999.99")) == 500


def test_resolver_at_threshold_both_months_steps_up():
    assert fee_bps_for_volumes(1_000_000, 1_000_000) == STEP_UP_FEE_BPS == 1000
    assert fee_bps_for_volumes("1000000.00", "1500000") == 1000
    assert fee_bps_for_volumes(2_500_000.50, 1_000_000) == 1000


def test_resolver_one_month_above_one_below_stays_base():
    assert fee_bps_for_volumes(5_000_000, 999_999) == 500
    assert fee_bps_for_volumes(0, 5_000_000) == 500


def test_split_at_step_up_rate():
    fee, net = split_clearing_proceeds(2_000_000, fee_bps=1000)
    assert fee == 200_000  # 10 % of clearing
    assert net == 1_800_000
    # Default rate is untouched.
    assert split_clearing_proceeds(2_000_000) == (100_000, 1_900_000)


# ----------------------------------------------------------------- ledger


def test_monthly_volumes_bucket_by_calendar_month_utc(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    _seed(ledger, datetime(2027, 1, 31, 23, 59, tzinfo=timezone.utc), 5 * ONE_MILLION_USD_MICRO)
    _seed(ledger, FEB, 2 * ONE_MILLION_USD_MICRO)
    _seed(ledger, MAR, 3 * ONE_MILLION_USD_MICRO)
    current, previous = monthly_volumes_usd(ledger, now=NOW)
    assert current == Decimal("3000000")
    assert previous == Decimal("2000000")


def test_monthly_volumes_year_boundary(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    jan_now = datetime(2027, 1, 15, tzinfo=timezone.utc)
    _seed(ledger, datetime(2026, 12, 20, tzinfo=timezone.utc), ONE_MILLION_USD_MICRO)
    _seed(ledger, datetime(2027, 1, 5, tzinfo=timezone.utc), ONE_MILLION_USD_MICRO)
    current, previous = monthly_volumes_usd(ledger, now=jan_now)
    assert current == Decimal("1000000")
    assert previous == Decimal("1000000")  # December 2026, across the year seam
    assert resolve_fee_bps(ledger, now=jan_now) == 1000


def test_missing_ledger_reads_as_empty_history(tmp_path):
    missing = tmp_path / "nope.jsonl"
    assert monthly_volumes_usd(missing, now=NOW) == (Decimal("0"), Decimal("0"))
    assert resolve_fee_bps(missing, now=NOW) == 500


def test_record_written_per_clear_with_applied_rate(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    award = _clear([1_000_000, 2_000_000], ledger)
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["clearing_price_micro_axm"] == award.clearing_price == 2_000_000
    assert record["fee_bps"] == award.platform_fee_bps == 500
    assert record["ts"].startswith("2027-03-15T12:00:00")


def test_failed_auction_records_nothing(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    award = clear_vickrey(
        auction_id="0x" + "11" * 32, task_id="t", bids=[], invites=[],
        junior_reserved=False, llm_calls_avoided=0, tokens_saved=0,
        cleared_at=NOW, ledger_path=ledger,
    )
    assert award.phase == "failed"
    assert not ledger.exists()


# ------------------------------------------------------------- end-to-end


def test_two_consecutive_big_months_step_up_end_to_end(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    _seed(ledger, FEB, 1_200_000 * 1_000_000)  # Feb: $1.2M
    _seed(ledger, MAR, 1_100_000 * 1_000_000)  # Mar so far: $1.1M
    award = _clear([1_000_000, 2_000_000], ledger)
    assert award.platform_fee_bps == 1000
    assert award.platform_fee == 200_000  # 10 % of the 2_000_000 clearing
    assert award.winner_net_amount == 1_800_000
    assert award.platform_fee + award.winner_net_amount == award.clearing_price
    # The step-up clearing itself is recorded at the applied rate.
    last = json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["fee_bps"] == 1000


def test_single_big_month_stays_at_base_rate(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    _seed(ledger, FEB, 1_500_000 * 1_000_000)  # only the previous month is big
    award = _clear([1_000_000, 2_000_000], ledger)
    assert award.platform_fee_bps == 500
    assert award.platform_fee == 100_000


def test_current_month_just_below_threshold_stays_base(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    _seed(ledger, FEB, 2_000_000 * 1_000_000)
    _seed(ledger, MAR, 999_999 * 1_000_000)  # $999,999 — one dollar short
    award = _clear([1_000_000, 2_000_000], ledger)
    assert award.platform_fee_bps == 500


def test_default_volumes_match_baseline_fee_amounts(tmp_path):
    # Empty history: identical fee math to the pre-step-up branch.
    ledger = tmp_path / "vol.jsonl"
    award = _clear([1_000_000, 2_000_000, 3_000_000], ledger)
    assert award.clearing_price == 2_000_000
    assert award.platform_fee_bps == 500
    assert award.platform_fee == 100_000
    assert award.winner_net_amount == 1_900_000


def test_recording_can_be_disabled(tmp_path):
    ledger = tmp_path / "vol.jsonl"
    bids = [_bid("agent-0", 1_000_000), _bid("agent-1", 2_000_000)]
    award = clear_vickrey(
        auction_id="0x" + "11" * 32, task_id="t", bids=bids,
        invites=[_invite(b.agent_id) for b in bids],
        junior_reserved=False, llm_calls_avoided=0, tokens_saved=0,
        cleared_at=NOW, ledger_path=ledger, record_volume=False,
    )
    assert award.platform_fee_bps == 500
    assert not ledger.exists()


def test_env_var_configures_default_ledger_path(tmp_path, monkeypatch):
    ledger = tmp_path / "env-vol.jsonl"
    monkeypatch.setenv("SINCOR_VICKREY_CLEARING_LEDGER", str(ledger))
    bids = [_bid("agent-0", 1_000_000), _bid("agent-1", 2_000_000)]
    award = clear_vickrey(
        auction_id="0x" + "11" * 32, task_id="t", bids=bids,
        invites=[_invite(b.agent_id) for b in bids],
        junior_reserved=False, llm_calls_avoided=0, tokens_saved=0,
        cleared_at=NOW,
    )
    assert award.platform_fee_bps == 500
    assert ledger.exists()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
