"""AXM-only quotes, 500 bps platform fee, realized fee inflow."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2 import a2a_integration  # noqa: E402
from sincor2.treasury_settlement import record_platform_fee_inflow  # noqa: E402


def test_default_primary_token_is_axiom():
    assert a2a_integration.A2A_PRIMARY_TOKEN == "AXIOM"
    assert a2a_integration.A2A_PLATFORM_FEE_BPS == 500


def test_platform_fee_500_bps_on_1e18():
    assert a2a_integration._compute_platform_fee_wei(10**18) == 5 * 10**16


def test_reject_sinc_accept_axm():
    assert a2a_integration._reject_non_axm("SINC")
    assert a2a_integration._reject_non_axm("AXM") is None
    assert a2a_integration._reject_non_axm("AXIOM") is None
    assert a2a_integration._reject_non_axm(None) is None
    assert a2a_integration._reject_non_axm("") is None


def test_record_platform_fee_inflow_skips_zero():
    assert record_platform_fee_inflow(fee_amount=0) is None
    assert record_platform_fee_inflow(fee_amount=Decimal("0")) is None
