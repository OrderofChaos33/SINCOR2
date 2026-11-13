"""Fail-closed treasury policy tests (build-out item 34) — all offline."""

import pytest

import src.sincor2.treasury_policy as tp


@pytest.fixture(autouse=True)
def _no_armed_env(monkeypatch):
    monkeypatch.delenv("SINCOR_FEE_EXECUTOR_ARMED", raising=False)
    yield


def test_not_converted_by_default_for_axm_to_treasury():
    d = tp.convert_before_treasury_if_needed(1250.0, "AXM", "TREASURY")
    assert d.converted is False
    assert "blocked" in d.reason
    assert d.amount == 1250.0
    # target must NOT claim USDC — nothing was converted
    assert d.target_asset == "AXM"


def test_not_converted_for_sinc_to_treasury():
    d = tp.convert_before_treasury_if_needed(980.0, "SINC", "TREASURY")
    assert d.converted is False
    assert d.reason  # a reason is always present


def test_trading_wallet_exception_still_fail_closed():
    wallet = "0xTradingWallet123"
    tp.treasury_policy.trading_wallets.add(wallet)
    try:
        d = tp.convert_before_treasury_if_needed(980.0, "SINC", wallet)
        assert d.converted is False
        assert "not required" in d.reason
    finally:
        tp.treasury_policy.trading_wallets.discard(wallet)


def test_non_fee_token_not_converted():
    d = tp.convert_before_treasury_if_needed(100.0, "USDC", "TREASURY")
    assert d.converted is False


def test_armed_flag_is_fail_closed_by_default():
    assert tp.is_conversion_executor_armed() is False


def test_legacy_armed_env_cannot_enable_conversion(monkeypatch):
    # Legacy boolean flags are not an authorization contract.
    monkeypatch.setenv("SINCOR_FEE_EXECUTOR_ARMED", "true")
    assert tp.is_conversion_executor_armed() is False
    d = tp.convert_before_treasury_if_needed(1250.0, "AXM", "TREASURY")
    assert d.converted is False
    assert "blocked" in d.reason
    assert d.target_asset == "AXM"


def test_policy_predicate_unchanged():
    # should_convert_before_treasury is the policy predicate ("should convert"),
    # distinct from the fail-closed execution signal ("did convert").
    assert tp.treasury_policy.should_convert_before_treasury("AXM", "TREASURY") is True
    assert tp.treasury_policy.should_convert_before_treasury("USDC", "TREASURY") is False
