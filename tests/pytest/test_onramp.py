"""Tests for the Coinbase Onramp session client (human x402 rail).

Hermetic: the HTTP layer (sincor2.onramp._http_post_json) is monkeypatched;
no test performs a real network call.
"""
from __future__ import annotations

import logging

import pytest

from sincor2 import onramp


@pytest.fixture()
def no_cdp_env(monkeypatch):
    """Clear every supported CDP credential env var."""
    for var in (
        "CDP_API_KEY",
        "CDP_API_KEY_ID",
        "CDP_API_KEY_SECRET",
        "CDP_PROJECT_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    return None


def _pem_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_create_onramp_session_fail_closed_without_keys(no_cdp_env, monkeypatch):
    calls = []

    def _boom(url, payload, headers, timeout):  # pragma: no cover - must not be called
        calls.append(url)
        raise AssertionError("network layer must not be reached when unconfigured")

    monkeypatch.setattr(onramp, "_http_post_json", _boom)
    result = onramp.create_onramp_session()
    assert calls == []
    assert result == {"ok": False, "error": "onramp_unconfigured"}
    assert "url" not in result


def test_session_url_formation_with_mocked_http(monkeypatch):
    """Happy path: mocked token endpoint -> hosted pay.coinbase.com URL."""
    key_id = "test-key-id-123"
    monkeypatch.setenv("CDP_API_KEY_ID", key_id)
    monkeypatch.setenv("CDP_API_KEY_SECRET", _pem_key())
    monkeypatch.delenv("CDP_API_KEY", raising=False)

    captured = {}

    def _fake_post(url, payload, headers, timeout=15):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = dict(headers)
        return {"token": "sess_abc123", "channel_id": "ch_1"}

    monkeypatch.setattr(onramp, "_http_post_json", _fake_post)

    result = onramp.create_onramp_session(
        destination_address="0x1111111111111111111111111111111111111111"
    )

    assert result["ok"] is True
    assert result["url"] == (
        "https://pay.coinbase.com/buy/select-asset"
        "?sessionToken=sess_abc123&defaultAsset=USDC&defaultNetwork=base"
    )
    # Correct endpoint
    assert captured["url"] == "https://api.developer.coinbase.com/onramp/v1/token"
    # Key sent as Bearer JWT in the Authorization header...
    auth = captured["headers"]["Authorization"]
    assert auth.startswith("Bearer ")
    jwt_token = auth[len("Bearer ") :]
    assert jwt_token.count(".") == 2
    # ...JWT kid header carries the key id...
    import jwt as pyjwt

    header = pyjwt.get_unverified_header(jwt_token)
    assert header["kid"] == key_id
    # ...and the key/secret appear NOWHERE else: not in URL, not in body.
    body_str = repr(captured["payload"])
    assert "sess_abc123" not in body_str
    for needle in (key_id,):
        assert needle not in captured["url"]
    assert "Authorization" not in body_str


def test_raw_bearer_key_never_in_url_or_query(monkeypatch):
    """CDP_API_KEY used raw: it goes in the header only, never the URL."""
    raw = "bearer-token-value-xyz"
    monkeypatch.setenv("CDP_API_KEY", raw)
    monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("CDP_API_KEY_SECRET", raising=False)

    captured = {}

    def _fake_post(url, payload, headers, timeout=15):
        captured["headers"] = dict(headers)
        captured["url"] = url
        return {"data": {"token": "sess_bearer_1"}}

    monkeypatch.setattr(onramp, "_http_post_json", _fake_post)
    result = onramp.create_onramp_session()

    assert result["ok"] is True
    assert raw not in result["url"]
    assert raw not in captured["url"]
    assert captured["headers"]["Authorization"] == f"Bearer {raw}"


def test_no_secret_leakage_in_logs_or_errors(monkeypatch, caplog):
    """Network failure: error payload and logs carry no secrets."""
    raw = "super-secret-cdp-key-999"
    monkeypatch.setenv("CDP_API_KEY", raw)
    monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("CDP_API_KEY_SECRET", raising=False)

    import urllib.error

    def _fail(url, payload, headers, timeout=15):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(onramp, "_http_post_json", _fail)

    with caplog.at_level(logging.WARNING, logger="sincor.onramp"):
        result = onramp.create_onramp_session(
            destination_address="0x2222222222222222222222222222222222222222"
        )

    assert result == {"ok": False, "error": "onramp_unavailable"}
    log_text = caplog.text
    assert raw not in log_text
    assert "0x2222222222222222222222222222222222222222" not in log_text
    assert result.get("url") is None


@pytest.mark.parametrize(
    "bad",
    ["abc", "-5", "0", "", "1e999999999999", "12.345", "999999999999999"],
)
def test_invalid_amount_usd_rejected(monkeypatch, bad):
    monkeypatch.setenv("CDP_API_KEY", "k")
    result = onramp.create_onramp_session(amount_usd=bad)
    assert result == {"ok": False, "error": "invalid_amount"}


def test_valid_amount_prefills_only(monkeypatch):
    """amount_usd only prefills the widget (presetFiatAmount); nothing is priced."""
    monkeypatch.setenv("CDP_API_KEY", "k")
    monkeypatch.setattr(
        onramp, "_http_post_json", lambda url, payload, headers, timeout=15: {"token": "t"}
    )
    result = onramp.create_onramp_session(amount_usd="25.5")
    assert result["ok"] is True
    assert "presetFiatAmount=25.50" in result["url"]
    # The session response carries no price authority — only ok + url.
    assert set(result.keys()) == {"ok", "url"}


@pytest.mark.parametrize("bad", ["USDC;DROP", "x" * 40, "usdc!", ""])
def test_invalid_asset_rejected(monkeypatch, bad):
    monkeypatch.setenv("CDP_API_KEY", "k")
    result = onramp.create_onramp_session(asset=bad)
    assert result == {"ok": False, "error": "invalid_asset"}


def test_jwt_claims_shape(monkeypatch):
    """CDP API-key JWT carries the documented claims and signs with the PEM."""
    key_id = "kid-abc"
    pem = _pem_key()
    token = onramp._cdp_jwt(key_id, pem)

    from cryptography.hazmat.primitives import serialization

    pub = (
        serialization.load_pem_private_key(pem.encode(), password=None)
        .public_key()
    )
    import jwt as pyjwt

    claims = pyjwt.decode(token, pub, algorithms=["ES256"], issuer="cdp")
    assert claims["sub"] == key_id
    assert claims["iss"] == "cdp"
    assert claims["exp"] - claims["nbf"] == 120
    assert claims["uris"] == ["POST api.developer.coinbase.com/onramp/v1/token"]
