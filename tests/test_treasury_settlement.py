"""Tests for fee-only treasury settlement helper — no fund movement."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2.treasury_settlement import (  # noqa: E402
    extract_fee_from_quote_or_settlement,
    record_platform_fee_inflow,
)


def test_extract_fee_prefers_platform_fee_wei():
    payload = {"platform_fee_wei": "150000000000000000", "amount": "999"}
    assert extract_fee_from_quote_or_settlement(payload) == 1.5e17


def test_extract_fee_from_treasury_fee_split():
    payload = {
        "treasury_fee_split": {
            "to": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
            "platform_fee": "12.5",
        }
    }
    assert extract_fee_from_quote_or_settlement(payload) == 12.5


def test_extract_fee_empty_is_zero():
    assert extract_fee_from_quote_or_settlement({}) == 0.0
    assert extract_fee_from_quote_or_settlement({"foo": 1}) == 0.0


def test_record_platform_fee_skips_zero():
    assert record_platform_fee_inflow(fee_amount=0) is None
    assert record_platform_fee_inflow(fee_amount=-1) is None


def test_record_platform_fee_calls_record_inflow_projected_false():
    with patch("sincor2.treasury_settlement.record_inflow") as mock_rec:
        class Ev:
            def to_dict(self):
                return {"ok": True}

        mock_rec.return_value = Ev()
        out = record_platform_fee_inflow(
            fee_amount=3.25,
            asset="AXM",
            source="a2a_settlement",
            tx_hash="0xabc",
            task_id="task-1",
        )
        assert out == {"ok": True}
        mock_rec.assert_called_once()
        args, kwargs = mock_rec.call_args
        assert args[0] == 3.25
        assert kwargs["projected"] is False
        assert kwargs["source"] == "a2a_settlement"
        assert kwargs["tx_hash"] == "0xabc"
        assert kwargs["asset"] == "AXM"
