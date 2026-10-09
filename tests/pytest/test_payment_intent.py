"""WP4 tests: payment intent hardening (D3: USDC + AXM only)."""

import time

import pytest

from sincor2.payments.intent import (
    SUPPORTED_ASSETS,
    PaymentIntent,
    PaymentIntentError,
)

_VALID_RECIPIENT = "0x" + "11" * 20
_VALID_PAYER = "0x" + "22" * 20


def _make_intent(**overrides):
    base = dict(
        asset="USDC",
        chain="base",
        recipient=_VALID_RECIPIENT,
        amount_atomic=1_000_000,  # 1 USDC
        payer=_VALID_PAYER,
        ttl_seconds=3600,
        idempotency_key="idem-test-1",
    )
    base.update(overrides)
    return PaymentIntent.create_server_priced(**base)


def test_supported_assets_are_usdc_axm_only():
    assert SUPPORTED_ASSETS == frozenset({"USDC", "AXM"})


def test_usdc_intent_validates():
    intent = _make_intent(asset="USDC")
    assert intent.asset == "USDC"
    assert intent.amount_atomic == 1_000_000
    assert not intent.is_expired


def test_axm_intent_validates():
    intent = _make_intent(asset="AXM", amount_atomic=5_000_000_000_000_000_000)
    assert intent.asset == "AXM"


def test_fiat_usd_rejected():
    with pytest.raises(PaymentIntentError, match="only.*USDC.*AXM|unsupported asset"):
        _make_intent(asset="USD")


def test_stripe_intent_rejected():
    with pytest.raises(PaymentIntentError, match="unsupported asset"):
        _make_intent(asset="STRIPE")


def test_eth_rejected():
    with pytest.raises(PaymentIntentError, match="unsupported asset"):
        _make_intent(asset="ETH")


def test_empty_asset_rejected():
    with pytest.raises(PaymentIntentError, match="unsupported asset"):
        _make_intent(asset="")


def test_client_supplied_price_impossible():
    # There is no code path that reads amount from a request body.
    # create_server_priced requires amount_atomic as an explicit kwarg;
    # omitting it is a TypeError, not a default.
    with pytest.raises(TypeError):
        PaymentIntent.create_server_priced(
            asset="USDC",
            chain="base",
            recipient=_VALID_RECIPIENT,
            payer=_VALID_PAYER,
            ttl_seconds=3600,
            idempotency_key="idem-x",
        )


def test_zero_amount_rejected():
    with pytest.raises(PaymentIntentError, match="positive"):
        _make_intent(amount_atomic=0)


def test_negative_amount_rejected():
    with pytest.raises(PaymentIntentError, match="positive"):
        _make_intent(amount_atomic=-100)


def test_invalid_recipient_rejected():
    with pytest.raises(PaymentIntentError, match="recipient"):
        _make_intent(recipient="not-an-address")


def test_invalid_payer_rejected():
    with pytest.raises(PaymentIntentError, match="payer"):
        _make_intent(payer="bob")


def test_past_expiry_rejected():
    with pytest.raises(PaymentIntentError, match="future"):
        _make_intent(ttl_seconds=-10)


def test_missing_idempotency_key_rejected():
    with pytest.raises(PaymentIntentError, match="idempotency_key"):
        _make_intent(idempotency_key="")


def test_intent_is_immutable():
    intent = _make_intent()
    with pytest.raises(Exception):
        intent.amount_atomic = 999  # type: ignore[misc]


def test_unsupported_chain_rejected():
    with pytest.raises(PaymentIntentError, match="chain"):
        _make_intent(chain="ethereum")
