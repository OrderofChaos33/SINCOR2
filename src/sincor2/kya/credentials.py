"""JWT-based verifiable credentials for KYA agent identity.

SINCOR's own implementation of Skyfire-style verifiable credentials: the KYA
issuer mints short-lived signed JWTs that bind an ``agent_id`` + ``wallet`` to
a ``kya_id``. Verifiers check the signature, the standard time/issuer claims,
and then the *live* KYA registry record — a credential is only as good as the
identity behind it, so revocation or expiry of the registry record kills the
credential even inside its JWT TTL.

Issuer key
----------
ES256 (ECDSA over P-256/secp256r1). The private key is loaded from the
``KYA_VC_ISSUER_KEY`` env var (PEM: PKCS#8 ``PRIVATE KEY`` or SEC1
``EC PRIVATE KEY``). If the var is unset, an ephemeral key is generated at
first use and a warning is logged: tokens minted with it verify only inside
this process and the key is **never suitable for production**. The optional
``KYA_VC_KID`` env var pins a stable key id; otherwise the kid is the
``kya-``-prefixed SHA-256 fingerprint of the public key (stable for a given
key across restarts).

Key rotation
------------
``_KEYSTORE`` maps ``kid -> public key PEM`` (in-memory dict). Rotation is:
generate a new key, ``rotate_issuer(new_pem)`` (registers the new public key
and makes it the active signer); old public keys stay in the keystore so
tokens minted under the previous kid still verify during the grace window.
Old keys are dropped with ``retire_key(kid)`` (or ``_reset_issuer_state()``
for tests). Grace across process restarts requires re-registering the old
public key — the keystore is deliberately process-local; a durable keystore
is future work.

THREAT NOTE (adversarial self-review)
-------------------------------------
* Key compromise: whoever holds the issuer private key can mint credentials
  for ANY agent_id/wallet/kya_id, including revoked agents (the registry
  liveness check runs at verify time, so a compromised key + a live record
  is total impersonation). Impact is bounded by short TTLs and by revoking
  the registry record, but the key itself must be rotated immediately and
  the old kid retired. Store the key in the Secure Vault / HSM, never in
  chat, logs, or the repo.
* Token replay within TTL: a bearer JWT is replayable until ``exp``. Mitigate
  with short TTLs (default 24h; use minutes for high-value assertions) and
  require the wallet holder to prove key ownership (EIP-191 challenge) at the
  point of use — the credential asserts identity, not authorization.
* Clock-skew abuse: verify tolerates a token whose ``iat`` is up to 60s in
  the future (honest clock drift). An attacker cannot backdate ``exp`` past
  issuance — signature covers all claims — but a verifier with a clock far
  behind would accept expired tokens; run verifiers on NTP-synced clocks.
  Expiry itself is enforced strictly (no leeway on ``exp``).
* kid confusion: ``verify_credential`` resolves the ``kid`` header ONLY against
  the in-memory keystore; an unknown kid fails closed. A missing kid falls
  back to the currently active issuer key — never to a key supplied by the
  token. Attacker-chosen kids cannot inject keys.
* Algorithm confusion: the accepted algorithm set is pinned to ``("ES256",)``
  and the header ``alg`` is checked BEFORE key selection. ``"none"``,
  ``HS256``/``RS256`` downgrade tokens, and any other ``alg`` are rejected
  unconditionally — a symmetric-key confusion attack (signing with the public
  key as an HMAC secret) cannot land because HMAC algorithms are never in
  the allow-list and the verification key is always an EC public key.
* Registry trust: liveness is read from the live ``sincor2.kya_registry``
  store at verify time. If the registry itself is compromised, credentials
  follow it. Every failure path — unknown kya_id, revoked flag, revoked or
  expired (or missing) status, lookup error — raises ``InvalidCredential``.
  Verification never returns "valid" on ambiguity.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sincor2 import kya_registry

log = logging.getLogger(__name__)

ISSUER = "sincor-kya"
ALGORITHM = "ES256"
_ALLOWED_ALGORITHMS = (ALGORITHM,)
_CLOCK_SKEW_S = 60  # tolerated future-iat drift (seconds)
_DEFAULT_TTL_S = 86400

_ENV_KEY = "KYA_VC_ISSUER_KEY"
_ENV_KID = "KYA_VC_KID"

_WALLET_RE = kya_registry.WALLET_RE  # 0x + 40 hex, same shape the registry binds

_REQUIRED_CLAIMS = ("iss", "sub", "exp", "iat", "jti", "agent_id", "wallet")

# kid -> public-key PEM bytes. Process-local by design; see module docstring.
_KEYSTORE: Dict[str, bytes] = {}
_STATE_LOCK = threading.Lock()
_ACTIVE = {"key": None, "kid": None, "ephemeral": False}  # type: Dict[str, Any]


class CredentialError(Exception):
    """Base error for credential issuance/verification failures."""


class InvalidCredential(CredentialError):
    """A credential that failed verification (bad signature, expired,
    untrusted issuer, or the underlying KYA identity is not live)."""


def _public_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _fingerprint_kid(public_key: ec.EllipticCurvePublicKey) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "kya-" + hashlib.sha256(der).hexdigest()[:16]


def _load_private_key(pem: bytes) -> ec.EllipticCurvePrivateKey:
    """Parse + validate a PEM issuer key. Fail closed on anything but P-256."""
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:
        raise CredentialError(f"unusable {_ENV_KEY}: not a parseable PEM private key") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise CredentialError(
            f"{_ENV_KEY} must be an ES256 (P-256/secp256r1) EC private key"
        )
    return key


def _register_locked(key: ec.EllipticCurvePrivateKey, kid: str, ephemeral: bool) -> None:
    _KEYSTORE[kid] = _public_pem(key)
    _ACTIVE["key"] = key
    _ACTIVE["kid"] = kid
    _ACTIVE["ephemeral"] = ephemeral


def register_issuer_key(private_pem: bytes, kid: Optional[str] = None) -> str:
    """Register an issuer key's public half in the keystore without making it
    the active signer. Used to pre-stage the *next* rotation key. Returns kid.
    """
    key = _load_private_key(private_pem)
    kid = (kid or "").strip() or _fingerprint_kid(key.public_key())
    with _STATE_LOCK:
        _KEYSTORE[kid] = _public_pem(key)
    return kid


def rotate_issuer(private_pem: bytes, kid: Optional[str] = None) -> str:
    """Make a new key the active issuer. Previously registered keys stay in
    the keystore so in-flight tokens verify during the rotation grace window.
    Returns the new kid.
    """
    key = _load_private_key(private_pem)
    kid = (kid or "").strip() or _fingerprint_kid(key.public_key())
    with _STATE_LOCK:
        _register_locked(key, kid, ephemeral=False)
    log.warning("KYA VC issuer rotated; new active kid=%s", kid)
    return kid


def retire_key(kid: str) -> bool:
    """Drop a key from the keystore (end of rotation grace). Never retires the
    currently active key. Returns True if a key was removed.
    """
    with _STATE_LOCK:
        if kid == _ACTIVE.get("kid"):
            return False
        return _KEYSTORE.pop(kid, None) is not None


def _reset_issuer_state() -> None:
    """Clear cached issuer key + keystore. Test/ops hook: call after changing
    KYA_VC_ISSUER_KEY / KYA_VC_KID so the next issue/verify reloads env.
    """
    with _STATE_LOCK:
        _KEYSTORE.clear()
        _ACTIVE["key"] = None
        _ACTIVE["kid"] = None
        _ACTIVE["ephemeral"] = False


def _active_issuer() -> Tuple[ec.EllipticCurvePrivateKey, str]:
    with _STATE_LOCK:
        if _ACTIVE["key"] is not None:
            return _ACTIVE["key"], _ACTIVE["kid"]
        raw = (os.environ.get(_ENV_KEY) or "").strip()
        if raw:
            key = _load_private_key(raw.encode("utf-8"))
            kid = (os.environ.get(_ENV_KID) or "").strip() or _fingerprint_kid(
                key.public_key()
            )
            _register_locked(key, kid, ephemeral=False)
            log.info("KYA VC issuer loaded from %s (kid=%s)", _ENV_KEY, kid)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            kid = _fingerprint_kid(key.public_key())
            _register_locked(key, kid, ephemeral=True)
            log.warning(
                "%s is not set: generated an EPHEMERAL ES256 issuer key (kid=%s). "
                "Tokens minted with it verify only in this process. "
                "NEVER use this in production — set %s.",
                _ENV_KEY,
                kid,
                _ENV_KEY,
            )
        return _ACTIVE["key"], _ACTIVE["kid"]


def _normalize_wallet(wallet: Any) -> str:
    w = str(wallet or "").strip().lower()
    if not _WALLET_RE.match(w):
        raise CredentialError("wallet must be a 0x-prefixed 40-hex EVM address")
    return w


def issue_credential(
    agent_id: str, wallet: str, kya_id: str, ttl_s: int = _DEFAULT_TTL_S
) -> str:
    """Mint a signed JWT verifiable credential binding agent_id+wallet to kya_id.

    Claims: iss="sincor-kya", sub=kya_id, agent_id, wallet (lowercased),
    iat, exp, jti (uuid). Signed ES256 with the active issuer key; the ``kid``
    header names the signing key.
    """
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        raise CredentialError("agent_id required")
    kya_id = str(kya_id or "").strip()
    if not kya_id:
        raise CredentialError("kya_id required")
    wallet = _normalize_wallet(wallet)
    try:
        ttl = int(ttl_s)
    except (TypeError, ValueError) as exc:
        raise CredentialError("ttl_s must be an integer number of seconds") from exc

    key, kid = _active_issuer()
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": kya_id,
        "agent_id": agent_id,
        "wallet": wallet,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(
        payload, private_pem, algorithm=ALGORITHM, headers={"kid": kid, "typ": "JWT"}
    )


def _resolve_verification_key(kid: Optional[str]) -> bytes:
    """kid present -> keystore lookup (unknown kid fails closed).
    kid absent -> active issuer's public key (never a key from the token)."""
    with _STATE_LOCK:
        if kid:
            pem = _KEYSTORE.get(kid)
            if pem is None:
                raise CredentialError(f"unknown credential key id: {kid!r}")
            return pem
    # No kid: resolve against the currently configured issuer (loads from env
    # or generates the ephemeral key on first use, same as issuance).
    key, _ = _active_issuer()
    return _public_pem(key)


def _assert_kya_live(kya_id: str) -> None:
    """Fail-closed liveness check against the live KYA registry."""
    try:
        rec = kya_registry.get(kya_id)
    except Exception as exc:
        raise InvalidCredential(f"kya registry lookup failed: {exc}") from exc
    if rec is None:
        raise InvalidCredential("unknown kya_id")
    if rec.get("revoked"):
        raise InvalidCredential("kya identity revoked")
    # Recompute status on a copy so a stale cached status can't slip through;
    # never mutate the store from the verify path.
    try:
        fresh = kya_registry.refresh_status(dict(rec))
    except Exception:
        fresh = rec
    status = str(fresh.get("status") or "")
    if not status or status in ("revoked", "expired"):
        raise InvalidCredential(f"kya identity not live (status={status!r})")


def verify_credential(token: str) -> Dict[str, Any]:
    """Verify a credential JWT and return its claims.

    Checks, in order: well-formed token; ``alg`` pinned to ES256 (``"none"``
    and every other algorithm rejected before key selection); signature
    against the ``kid``-resolved keystore key (unknown kid fails closed);
    ``exp`` strictly enforced; ``iat`` not more than 60s in the future;
    ``iss == "sincor-kya"``; required claims present; wallet shape sane;
    then the live registry liveness check (unknown / revoked / expired /
    missing status all raise). Raises ``InvalidCredential`` (a
    ``CredentialError``) on any failure.
    """
    if not token or not isinstance(token, str):
        raise CredentialError("empty credential token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise CredentialError(f"malformed credential: {exc}") from exc

    # Alg-confusion pin: check the header BEFORE selecting a key, and pin the
    # decode allow-list to ES256 only. "none" is never accepted.
    alg = header.get("alg")
    if alg != ALGORITHM:
        raise CredentialError(f"rejected JWT alg {alg!r}; only ES256 is accepted")

    public_pem = _resolve_verification_key(header.get("kid"))

    try:
        claims = jwt.decode(
            token,
            public_pem,
            algorithms=list(_ALLOWED_ALGORITHMS),
            issuer=ISSUER,
            options={"require": list(_REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidCredential("credential expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidCredential("credential from untrusted issuer") from exc
    except jwt.PyJWTError as exc:
        raise InvalidCredential(f"credential signature invalid: {exc}") from exc

    now = int(time.time())
    iat = claims.get("iat")
    if not isinstance(iat, int) or isinstance(iat, bool) or iat > now + _CLOCK_SKEW_S:
        raise InvalidCredential("credential issued in the future")

    wallet = str(claims.get("wallet") or "")
    if wallet != wallet.lower() or not _WALLET_RE.match(wallet):
        raise InvalidCredential("credential wallet malformed")

    _assert_kya_live(str(claims.get("sub")))
    return dict(claims)
