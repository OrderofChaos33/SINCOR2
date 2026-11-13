"""Coinbase Onramp session client (human x402 payment rail).

Human-facing "buy USDC with a card" flow for the x402 rail:

  1. POST ``https://api.developer.coinbase.com/onramp/v1/token`` with the CDP
     API key (ES256 JWT auth, per CDP API-key authentication) to mint a
     one-time session token.
  2. Hand the user the Coinbase-hosted widget URL
     ``https://pay.coinbase.com/buy/select-asset?sessionToken=<token>``.

FAIL-CLOSED: without CDP credentials configured, :func:`create_onramp_session`
returns ``{"ok": False, "error": "onramp_unconfigured"}`` -- it never emits a
broken URL.  Secrets (key id, secret, JWT, session token) never appear in
logs or error payloads; the API key travels only in the ``Authorization``
header, never in a URL or query string.

The session does not price anything: ``amount_usd`` only prefills the
widget's ``presetFiatAmount``.  The actual payment amount always comes from
the x402 challenge (POST /rail/checkout), never from this client.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger("sincor.onramp")

#: Documented session-token endpoint (Coinbase CDP Onramp API).
ONRAMP_TOKEN_URL = "https://api.developer.coinbase.com/onramp/v1/token"
#: Coinbase-hosted onramp widget.
ONRAMP_HOSTED_URL = "https://pay.coinbase.com/buy/select-asset"

_JWT_URIS = ["POST api.developer.coinbase.com/onramp/v1/token"]
_JWT_TTL_SECONDS = 120

_ASSET_RE = re.compile(r"^[A-Z]{2,10}$")
_NETWORK_RE = re.compile(r"^[a-z0-9-]{1,24}$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_MAX_FIAT_USD = Decimal("1000000")


def _credentials() -> tuple[str, str, str] | tuple[str, str] | None:
    """Return (kind, key_id_or_token, secret_pem_or_empty), or None.

    Preferred form: CDP_API_KEY_ID + CDP_API_KEY_SECRET (PEM) -> ES256 JWT.
    Fallback form: CDP_API_KEY used raw as a Bearer token.
    Absent entirely -> None (fail closed).
    """
    key_id = os.getenv("CDP_API_KEY_ID", "").strip()
    secret = os.getenv("CDP_API_KEY_SECRET", "").strip()
    raw = os.getenv("CDP_API_KEY", "").strip()
    if key_id and secret and "BEGIN" in secret and "PRIVATE KEY" in secret:
        return ("jwt", key_id, secret)
    if raw:
        return ("bearer", raw)
    return None


def is_configured() -> bool:
    """True when any usable CDP credential form is present (no network)."""
    return _credentials() is not None


def _cdp_jwt(key_id: str, private_key_pem: str) -> str:
    """Sign a CDP API-key JWT (ES256) for the onramp session-token call."""
    import jwt as pyjwt

    now = int(time.time())
    payload = {
        "iss": "cdp",
        "nbf": now,
        "exp": now + _JWT_TTL_SECONDS,
        "sub": key_id,
        "uris": list(_JWT_URIS),
    }
    headers = {"kid": key_id, "nonce": secrets.token_hex(16)}
    return pyjwt.encode(payload, private_key_pem, algorithm="ES256", headers=headers)


def _http_post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 15
) -> dict[str, Any]:
    """POST JSON and return the decoded JSON body.  Monkeypatchable in tests."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _validate_amount(amount_usd: Any) -> str | None:
    """Validate a prefill amount; return canonical "NNN.NN" or None."""
    if amount_usd is None:
        return None
    text = str(amount_usd).strip()
    # Reject scientific notation, signs, and anything non-decimal.
    if not re.fullmatch(r"\d{1,7}(\.\d{1,2})?", text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value <= 0 or value > _MAX_FIAT_USD:
        return None
    return f"{value:.2f}"


def create_onramp_session(
    *,
    asset: str = "USDC",
    network: str = "base",
    amount_usd: Any = None,
    redirect_url: str | None = None,
    destination_address: str | None = None,
) -> dict[str, Any]:
    """Mint a one-time Coinbase Onramp session; return {"ok","url"} or an error.

    ``amount_usd`` only prefills the widget (presetFiatAmount) -- it is never
    a price authority.  Fail-closed: returns {"ok": False, "error": ...} and
    never a URL when credentials, inputs, or the token call are unusable.
    """
    asset = (asset or "").strip().upper()
    if not _ASSET_RE.match(asset):
        return {"ok": False, "error": "invalid_asset"}
    network = (network or "").strip().lower()
    if not _NETWORK_RE.match(network):
        return {"ok": False, "error": "invalid_network"}

    amount = _validate_amount(amount_usd)
    if amount_usd is not None and amount is None:
        return {"ok": False, "error": "invalid_amount"}

    if redirect_url:
        parsed = urllib.parse.urlparse(redirect_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"ok": False, "error": "invalid_redirect_url"}

    addresses: list[dict[str, Any]] = []
    if destination_address:
        if not _ADDRESS_RE.match(destination_address.strip()):
            return {"ok": False, "error": "invalid_destination_address"}
        addresses.append(
            {"address": destination_address.strip(), "blockchains": [network]}
        )

    creds = _credentials()
    if creds is None:
        return {"ok": False, "error": "onramp_unconfigured"}

    if creds[0] == "jwt":
        try:
            bearer = _cdp_jwt(creds[1], creds[2])
        except Exception:
            logger.warning("onramp: CDP JWT signing failed")
            return {"ok": False, "error": "onramp_auth_invalid"}
    else:
        bearer = creds[1]

    body: dict[str, Any] = {"addresses": addresses}
    try:
        resp = _http_post_json(
            ONRAMP_TOKEN_URL,
            body,
            {"Authorization": f"Bearer {bearer}"},
        )
    except urllib.error.HTTPError as exc:
        # Status code only -- response bodies are never trusted or logged.
        logger.warning("onramp: session token rejected (http %s)", exc.code)
        return {"ok": False, "error": "onramp_rejected"}
    except Exception:
        logger.warning("onramp: session token request failed")
        return {"ok": False, "error": "onramp_unavailable"}

    token = None
    if isinstance(resp, dict):
        token = resp.get("token")
        if not token and isinstance(resp.get("data"), dict):
            token = resp["data"].get("token")
    if not token or not isinstance(token, str):
        logger.warning("onramp: session token missing from response")
        return {"ok": False, "error": "onramp_token_missing"}

    params: list[tuple[str, str]] = [
        ("sessionToken", token),
        ("defaultAsset", asset),
        ("defaultNetwork", network),
    ]
    if amount is not None:
        params.append(("presetFiatAmount", amount))
    if redirect_url:
        params.append(("redirectUrl", redirect_url))
    url = f"{ONRAMP_HOSTED_URL}?{urllib.parse.urlencode(params)}"
    return {"ok": True, "url": url}
