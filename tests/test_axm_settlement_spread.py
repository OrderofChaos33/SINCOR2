"""P02 — AXM native settlement spread: 1.0 % of settled value to treasury.

Covers the standalone component (sincor2.axm_settlement_spread), its
config constant, and the wiring at the A2A settlement points.  The 5 %
platform fee and all USDC/ETH settlement behavior must be untouched.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2 import a2a_integration  # noqa: E402
from sincor2 import axm_settlement_spread as spread  # noqa: E402
from sincor2.settlement_proofs import (  # noqa: E402
    canonical_statement_hash,
    verify_proof_of_settlement,
)
from sincor2.treasury_settlement import TREASURY  # noqa: E402

ONE_AXM = 10**18
TREASURY_LITERAL = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


# ---------------------------------------------------------------------------
# Config constant
# ---------------------------------------------------------------------------

def test_default_spread_rate_is_100_bps():
    assert spread.AXM_SETTLEMENT_SPREAD_BPS == 100


def test_env_override_changes_rate():
    old = os.environ.get("SINCOR_AXM_SETTLEMENT_SPREAD_BPS")
    os.environ["SINCOR_AXM_SETTLEMENT_SPREAD_BPS"] = "250"
    try:
        reloaded = importlib.reload(spread)
        assert reloaded.AXM_SETTLEMENT_SPREAD_BPS == 250
        assert reloaded.compute_spread_wei(ONE_AXM) == 25 * 10**15
        fields = reloaded.spread_fields(asset="AXM", settled_amount_wei=ONE_AXM)
        assert fields["spread_bps_applied"] == 250
    finally:
        if old is None:
            os.environ.pop("SINCOR_AXM_SETTLEMENT_SPREAD_BPS", None)
        else:
            os.environ["SINCOR_AXM_SETTLEMENT_SPREAD_BPS"] = old
        importlib.reload(spread)
    assert spread.AXM_SETTLEMENT_SPREAD_BPS == 100


# ---------------------------------------------------------------------------
# Spread math
# ---------------------------------------------------------------------------

def test_spread_math_exact_on_round_amounts():
    assert spread.compute_spread_wei(ONE_AXM) == 10**16          # 1 AXM -> 0.01 AXM
    assert spread.compute_spread_wei(100 * ONE_AXM) == ONE_AXM   # 100 AXM -> 1 AXM
    assert spread.compute_spread_wei(250 * ONE_AXM) == 25 * 10**17  # 250 -> 2.5 AXM
    # Decimal cross-check in whole tokens: exactly 1.0 %, no float drift.
    for tokens in ("1", "100", "250", "1000"):
        wei = int(Decimal(tokens) * ONE_AXM)
        got = Decimal(spread.compute_spread_wei(wei)) / Decimal(ONE_AXM)
        assert got == Decimal(tokens) * Decimal("0.01")


def test_spread_math_floor_and_clamps():
    assert spread.compute_spread_wei(0) == 0
    assert spread.compute_spread_wei(99) == 0        # sub-wei spread floors to 0
    assert spread.compute_spread_wei(199) == 1
    assert spread.compute_spread_wei(-ONE_AXM) == 0  # negatives clamp like the fee


def test_spread_math_explicit_bps_and_zero_rate():
    assert spread.compute_spread_wei(ONE_AXM, spread_bps=250) == 25 * 10**15
    assert spread.compute_spread_wei(ONE_AXM, spread_bps=0) == 0


# ---------------------------------------------------------------------------
# Asset scoping + record annotation
# ---------------------------------------------------------------------------

def test_spread_fields_axm_only():
    assert spread.spread_fields(asset="AXM", settled_amount_wei=ONE_AXM) == {
        "spread_bps_applied": 100,
        "spread_amount": 10**16,
    }
    assert spread.spread_fields(asset="AXIOM", settled_amount_wei=ONE_AXM)[
        "spread_amount"
    ] == 10**16
    assert spread.spread_fields(asset="axm", settled_amount_wei=ONE_AXM)[
        "spread_amount"
    ] == 10**16
    for asset in ("USDC", "ETH", "WETH", "", None):
        assert spread.spread_fields(asset=asset, settled_amount_wei=ONE_AXM) == {
            "spread_bps_applied": 0,
            "spread_amount": 0,
        }


def test_annotate_axm_record_gains_fields_input_untouched():
    record = {"task_id": "t1", "asset": "AXM", "amount_wei": str(ONE_AXM)}
    out = spread.annotate_settlement_record(
        record, asset="AXM", settled_amount_wei=ONE_AXM
    )
    assert out["spread_bps_applied"] == 100
    assert out["spread_amount"] == 10**16
    assert "spread_bps_applied" not in record  # input never mutated


def test_annotate_non_axm_record_byte_identical():
    for asset in ("USDC", "ETH"):
        record = {"task_id": "t2", "asset": asset, "amount": "500000000"}
        out = spread.annotate_settlement_record(
            record, asset=asset, settled_amount_wei=500_000_000
        )
        assert out == record
        assert json.dumps(out, sort_keys=True) == json.dumps(record, sort_keys=True)


# ---------------------------------------------------------------------------
# Treasury destination
# ---------------------------------------------------------------------------

def test_spread_treasury_destination():
    assert spread.SPREAD_TREASURY_WALLET == TREASURY_LITERAL
    assert spread.SPREAD_TREASURY_WALLET == TREASURY
    assert spread.SPREAD_TREASURY_WALLET == a2a_integration.TREASURY_WALLET


# ---------------------------------------------------------------------------
# Wiring at the A2A settlement points
# ---------------------------------------------------------------------------

def test_maybe_record_fee_annotates_record_and_fee_unchanged():
    with patch("sincor2.a2a_integration.record_platform_fee_inflow") as rec:
        rec.return_value = {"event": "fee"}
        out = a2a_integration.maybe_record_a2a_platform_fee(
            axm_paid_wei=2 * ONE_AXM,
            tx_hash="0x" + "ef" * 32,
            task_id="task-spread",
            metadata={},
        )
        # Spread fields ride on the returned settlement record.
        assert out["spread_bps_applied"] == 100
        assert out["spread_amount"] == 2 * 10**16
        # The 5 % platform fee booking itself is exactly as before.
        kwargs = rec.call_args.kwargs
        assert kwargs["asset"] == "AXM"
        assert kwargs["source"] == "a2a_settlement"
        assert abs(kwargs["fee_amount"] - 0.1) < 1e-12  # 5 % of 2 AXM


def test_statement_with_spread_fields_still_verifies():
    statement = {
        "task_id": "t-spread",
        "tx_hash": "0x" + "aa" * 32,
        "axm_paid_wei": str(ONE_AXM),
        "platform_fee_bps": a2a_integration.A2A_PLATFORM_FEE_BPS,
        "platform_fee_wei": str(5 * 10**16),
        "spread_bps_applied": spread.AXM_SETTLEMENT_SPREAD_BPS,
        "spread_amount_wei": str(spread.compute_spread_wei(ONE_AXM)),
        "treasury": a2a_integration.TREASURY_WALLET,
        "payment_verification": "onchain",
    }
    proof = {
        "proof_of_settlement": {
            **statement,
            "proof_hash": canonical_statement_hash(statement),
        }
    }
    ok, _reason = verify_proof_of_settlement(proof)
    assert ok


def test_legacy_statement_without_spread_fields_still_verifies():
    # Pre-P02 statements carry no spread fields; their hashes cover
    # exactly their own contents, so they must keep verifying.
    statement = {
        "task_id": "t-legacy",
        "tx_hash": "0x" + "bb" * 32,
        "axm_paid_wei": str(ONE_AXM),
        "platform_fee_bps": 500,
        "platform_fee_wei": str(5 * 10**16),
        "treasury": a2a_integration.TREASURY_WALLET,
        "payment_verification": "onchain",
    }
    proof = {
        "proof_of_settlement": {
            **statement,
            "proof_hash": canonical_statement_hash(statement),
        }
    }
    ok, _reason = verify_proof_of_settlement(proof)
    assert ok
