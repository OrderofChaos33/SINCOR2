"""JWKS-style public-key discovery for KYA credential verification (RFC 7517).

Issuer keystore contract (shared with the VC JWT issuer):
  - KYA_VC_ISSUER_KEY  : PEM of the primary issuer private key (ES256 / P-256).
                         Same env the VC module signs with.
  - KYA_VC_PREV_KEYS   : optional JSON list of PEM strings for previous issuer
                         keys, kept during rotation grace so credentials signed
                         under the old key still verify.

Key-id (kid) derivation: stable kid = first 16 hex chars of SHA-256 over the
uncompressed public key bytes (0x04 || X || Y, 32 bytes each for P-256).
Deterministic across restarts and across machines given the same key, so
verifiers can cache JWKs by kid without coordination.

Empty keystore: get_jwks() returns {"keys": []} and never raises for missing
config. This is deliberate: silently generating and serving an ephemeral key
would let verifiers pin a throwaway key and trust credentials minted by it.
An empty set is the honest signal -- "this issuer has no configured keys" --
and forces a loud operator action instead of a silent insecure default.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_KTY = "EC"
_CRV = "P-256"
_ALG = "ES256"
_USE = "sig"

_ISSUER_KEY_ENV = "KYA_VC_ISSUER_KEY"
_PREV_KEYS_ENV = "KYA_VC_PREV_KEYS"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _normalize_pem(pem: str) -> str:
    """Tolerate env values where newlines were escaped as literal backslash-n."""
    pem = pem.strip()
    if "-----BEGIN" in pem and "\n" not in pem and "\\n" in pem:
        pem = pem.replace("\\n", "\n")
    return pem


def _load_private_key(pem: str):
    """Load a P-256 EC key from a PEM string.

    Accepts a private-key PEM (what the VC signer uses) or a public-key PEM.
    Anything else -- wrong curve, wrong type, unparsable -- raises ValueError
    (fail closed: a misconfigured key must be loud, never silently skipped).
    """
    data = _normalize_pem(pem).encode("utf-8")
    key = None
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError):
        try:
            key = serialization.load_pem_public_key(data)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{_ISSUER_KEY_ENV}: not a parseable EC PEM") from exc
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise ValueError(f"{_ISSUER_KEY_ENV}: only P-256 (ES256) keys are supported")
    return key


def _uncompressed_bytes(pub: ec.EllipticCurvePublicKey) -> bytes:
    nums = pub.public_numbers()
    return (
        b"\x04"
        + nums.x.to_bytes(32, "big")
        + nums.y.to_bytes(32, "big")
    )


def kid_for_key(pub: ec.EllipticCurvePublicKey) -> str:
    """Stable key id: first 16 hex chars of SHA-256(uncompressed pubkey bytes)."""
    return hashlib.sha256(_uncompressed_bytes(pub)).hexdigest()[:16]


def _keystore() -> list:
    """Ordered [primary, *previous] public keys, parsed fresh from env each call."""
    keys = []
    primary = (os.environ.get(_ISSUER_KEY_ENV) or "").strip()
    if primary:
        keys.append(_load_private_key(primary))
    prev_raw = (os.environ.get(_PREV_KEYS_ENV) or "").strip()
    if prev_raw:
        try:
            prev_list = json.loads(prev_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{_PREV_KEYS_ENV}: not valid JSON") from exc
        if not isinstance(prev_list, list):
            raise ValueError(f"{_PREV_KEYS_ENV}: must be a JSON list of PEMs")
        for pem in prev_list:
            if not isinstance(pem, str):
                raise ValueError(f"{_PREV_KEYS_ENV}: entries must be PEM strings")
            keys.append(_load_private_key(pem))
    # De-duplicate on kid (a rotation that kept the old key in PREV would
    # otherwise publish the same kid twice); primary wins.
    seen = set()
    unique = []
    for key in keys:
        kid = kid_for_key(key)
        if kid in seen:
            continue
        seen.add(kid)
        unique.append(key)
    return unique


def public_jwk(pub: ec.EllipticCurvePublicKey) -> dict:
    """RFC 7517 public JWK for one P-256 issuer key. Never includes 'd'."""
    nums = pub.public_numbers()
    return {
        "kty": _KTY,
        "use": _USE,
        "kid": kid_for_key(pub),
        "alg": _ALG,
        "crv": _CRV,
        "x": _b64u(nums.x.to_bytes(32, "big")),
        "y": _b64u(nums.y.to_bytes(32, "big")),
    }


def get_jwks() -> dict:
    """JWKS document for this issuer: {"keys": [...]}.

    Empty keystore -> {"keys": []}. See module docstring for why we never
    generate-and-serve an ephemeral key here.
    """
    return {"keys": [public_jwk(k) for k in _keystore()]}


def key_for_kid(kid: str):
    """Return the EC public key for a kid so verifiers can check ES256 signatures.

    Raises KeyError for unknown kids (fail closed: never substitute a
    different key when the requested one is absent).
    """
    for key in _keystore():
        if kid_for_key(key) == kid:
            return key
    raise KeyError(f"unknown key id: {kid}")
