"""JWKS key discovery for KYA credentials. No Flask, no chain."""
from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sincor2.kya import get_jwks, key_for_kid
from sincor2.kya.jwks import kid_for_key

_ISSUER_ENV = "KYA_VC_ISSUER_KEY"
_PREV_ENV = "KYA_VC_PREV_KEYS"


def _fresh_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture()
def issuer_env(monkeypatch):
    """Install exactly one configured issuer key; nothing else leaks in."""
    pem = _fresh_pem()
    monkeypatch.setenv(_ISSUER_ENV, pem)
    monkeypatch.delenv(_PREV_ENV, raising=False)
    return pem


def _pub(pem: str):
    return serialization.load_pem_private_key(pem.encode(), None).public_key()


def _b64u_pad(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_jwks_shape_matches_rfc7517(issuer_env):
    doc = get_jwks()
    assert set(doc) == {"keys"}
    assert len(doc["keys"]) == 1
    jwk = doc["keys"][0]
    assert jwk["kty"] == "EC"
    assert jwk["crv"] == "P-256"
    assert jwk["use"] == "sig"
    assert jwk["alg"] == "ES256"
    assert isinstance(jwk["kid"], str) and len(jwk["kid"]) == 16
    x = _b64u_pad(jwk["x"])
    y = _b64u_pad(jwk["y"])
    assert len(x) == 32 and len(y) == 32
    # public key bytes round-trip to the configured issuer key
    pub = _pub(issuer_env).public_numbers()
    assert int.from_bytes(x, "big") == pub.x
    assert int.from_bytes(y, "big") == pub.y
    # no private material ever leaves this endpoint
    assert "d" not in jwk


def test_kid_derivation_stable_across_calls(issuer_env):
    kid1 = get_jwks()["keys"][0]["kid"]
    kid2 = get_jwks()["keys"][0]["kid"]
    assert kid1 == kid2
    # independent derivation: first 16 hex of SHA-256(uncompressed pub bytes)
    pub = _pub(issuer_env)
    nums = pub.public_numbers()
    raw = b"\x04" + nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
    assert kid1 == hashlib.sha256(raw).hexdigest()[:16]
    assert kid1 == kid_for_key(pub)


def test_rotation_previous_key_present_with_distinct_kid(issuer_env, monkeypatch):
    old_pem = issuer_env
    new_pem = _fresh_pem()
    monkeypatch.setenv(_ISSUER_ENV, new_pem)
    monkeypatch.setenv(_PREV_ENV, json.dumps([old_pem]))
    doc = get_jwks()
    kids = [k["kid"] for k in doc["keys"]]
    assert len(kids) == 2
    assert len(set(kids)) == 2  # primary and previous must differ
    assert kids[0] == kid_for_key(_pub(new_pem))  # primary first
    assert kids[1] == kid_for_key(_pub(old_pem))  # previous kept for grace


def test_empty_keystore_returns_empty_set(monkeypatch):
    monkeypatch.delenv(_ISSUER_ENV, raising=False)
    monkeypatch.delenv(_PREV_ENV, raising=False)
    assert get_jwks() == {"keys": []}  # not an error; no ephemeral key served


def test_key_for_kid_returns_verifying_key(issuer_env):
    kid = get_jwks()["keys"][0]["kid"]
    key = key_for_kid(kid)
    assert key.public_numbers() == _pub(issuer_env).public_numbers()


def test_key_for_kid_unknown_raises(issuer_env):
    with pytest.raises(KeyError):
        key_for_kid("deadbeefdeadbeef")


def test_key_for_kid_unknown_raises_when_empty(monkeypatch):
    monkeypatch.delenv(_ISSUER_ENV, raising=False)
    monkeypatch.delenv(_PREV_ENV, raising=False)
    with pytest.raises(KeyError):
        key_for_kid("deadbeefdeadbeef")


def test_misconfigured_key_fails_loud(monkeypatch):
    monkeypatch.setenv(_ISSUER_ENV, "not-a-pem")
    monkeypatch.delenv(_PREV_ENV, raising=False)
    with pytest.raises(ValueError):
        get_jwks()


def test_wrong_curve_rejected(monkeypatch):
    secp256k1 = ec.generate_private_key(ec.SECP256K1())
    pem = secp256k1.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv(_ISSUER_ENV, pem)
    monkeypatch.delenv(_PREV_ENV, raising=False)
    with pytest.raises(ValueError):
        get_jwks()
