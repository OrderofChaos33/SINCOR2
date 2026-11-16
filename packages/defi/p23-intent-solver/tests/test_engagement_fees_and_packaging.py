"""Added coverage for the P23 package: engagement fee math, solver-bonding
silence, oracle stub, and packaging smoke.

Engagement terms under test (catalog): secondary service form is
2 bps of order volume + 50% of price improvement, capped at 0.98% of
order volume. These are commercial terms; the reference code's internal
settlement fee is a separate 8 bps constant and is also asserted here
so the two can never be silently conflated.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from sincor_p23_intent_solver import engagement_fee, fee_breakdown
from sincor_p23_intent_solver.intent_dark_pool import (
    FEE_BPS,
    Intent,
    SettlementEngine,
)
from sincor_p23_intent_solver.price_oracle_stub import (
    OracleStubError,
    wiring_for,
)

PKG_ROOT = Path(__file__).resolve().parents[1]


# -- engagement fee math -------------------------------------------------------

def test_fee_volume_component_only():
    # No improvement: fee is exactly 2 bps of volume.
    assert engagement_fee(1_000_000, 0) == 200


def test_fee_adds_half_of_price_improvement():
    # 2 bps of 1,000,000 = 200; 50% of 10,000 improvement = 5,000.
    assert engagement_fee(1_000_000, 10_000) == 5_200


def test_fee_cap_binds_at_0_98_percent():
    # Uncapped would be 200 + 500,000; cap is 0.98% of 1,000,000 = 9,800.
    assert engagement_fee(1_000_000, 1_000_000) == 9_800
    bd = fee_breakdown(1_000_000, 1_000_000)
    assert bd["capped"] is True and bd["cap"] == 9_800


def test_fee_exactly_at_cap_not_reduced():
    # improvement 19,200 -> share 9,600; 200 + 9,600 == cap 9,800 exactly.
    assert engagement_fee(1_000_000, 19_200) == 9_800
    assert fee_breakdown(1_000_000, 19_200)["capped"] is False


def test_fee_zero_volume_and_negative_improvement():
    assert engagement_fee(0, 10_000) == 0
    assert engagement_fee(1_000_000, -5_000) == 200  # negative PI -> 0 share
    with pytest.raises(ValueError):
        engagement_fee(-1, 0)


def test_fee_floor_division_wei_exact():
    # 999 volume: 999*2//10000 == 0; cap 999*98//10000 == 9.
    assert engagement_fee(999, 3) == 1  # 0 + 3*50//100 == 1
    assert engagement_fee(999, 10**9) == 9


def test_internal_settlement_fee_is_separate_8bps():
    # The code constant is 8 bps and is NOT the engagement fee.
    assert FEE_BPS == 8
    assert SettlementEngine._fee(1_000_000) == 800
    assert engagement_fee(1_000_000, 0) == 200 != 800


# -- solver bonding: the code specifies nothing --------------------------------

def test_no_bonding_surface_in_code():
    """Bonding silence check: the extracted module exposes no bond asset,
    bond size, custody, slashing, or solver-registration API. The bonding
    spec (SOLVER_BONDING.md) therefore records every term as
    'not specified in code — engagement term to be set'."""
    import sincor_p23_intent_solver.intent_dark_pool as m

    names = [n.lower() for n in dir(m)]
    for needle in ("bond", "slash", "stake", "collateral", "register_solver"):
        assert not any(needle in n for n in names), needle
    src = Path(m.__file__).read_text()
    for needle in ("bond", "slash", "collateral"):
        assert needle not in src.lower(), needle


# -- oracle stub -----------------------------------------------------------------

class _Result:
    def __init__(self, price_fp):
        self.price_fp = price_fp


class _FakeOracle:
    def get_price(self, asset, now=None, strict=False):
        if asset == "ETH/USD":
            return _Result(300_000_000_000)
        raise RuntimeError("no price")


def test_oracle_stub_binds_declared_assets_only():
    feed = wiring_for("P03_INTENT_DARK", _FakeOracle())
    assert feed.assets == ["ETH/USD", "BTC/USD"]
    assert feed.price("ETH/USD") == 300_000_000_000
    with pytest.raises(OracleStubError):
        feed.price("SOL/USD")
    with pytest.raises(RuntimeError):  # oracle failure propagates (fail-closed)
        feed.price("BTC/USD")
    with pytest.raises(KeyError):
        wiring_for("P99_UNKNOWN", _FakeOracle())


def test_price_feed_for_uses_stub():
    from sincor_p23_intent_solver.intent_dark_pool import price_feed_for

    feed = price_feed_for(_FakeOracle())
    assert feed.product_key == "P03_INTENT_DARK"
    intent = Intent("i", "alice", "AXM", "USDC", 100, 90,
                    time.time() + 3600, 0)
    assert intent.commitment  # module still fully functional


# -- packaging smoke ---------------------------------------------------------------

def test_package_imports_clean_standalone():
    """Smoke: in a fresh interpreter with ONLY the package src on the path,
    the package imports with no platform (sincor2) modules loaded."""
    code = (
        "import sys\n"
        "import sincor_p23_intent_solver as p\n"
        "assert not any(m.startswith('sincor2') for m in sys.modules), "
        "[m for m in sys.modules if m.startswith('sincor2')]\n"
        "assert p.DarkPool and p.Matcher and p.SettlementEngine and p.SplitSolver\n"
        "print('smoke-ok', p.__version__)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(PKG_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "smoke-ok" in proc.stdout


def test_no_banned_word_in_package_docs():
    banned = "certi" + "fied"  # assembled so this file stays clean too
    for name in ("README.md", "PACKAGING.md", "ENGAGEMENT.md", "DEPLOY.md",
                 "SOLVER_BONDING.md"):
        text = (PKG_ROOT / name).read_text().lower()
        assert banned not in text, name
