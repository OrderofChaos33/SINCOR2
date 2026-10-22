"""Dev-watch item 262 — x402 paid-surface probe (aiskills402 audit hedge).

The aiskills402 audit of 10 CDP Bazaar storefronts found:
  * 7/9 serve 402s without `cache-control: no-store`
  * 5/9 have PAYMENT-REQUIRED header differing from body
  * 4/9 don't expose the header via CORS
  * 4/9 fail CORS preflight
  * 2/9 answer undeclared methods with HTML

This probe encodes the paid-surface checklist
(docs/ops/X402_PAID_SURFACE_CHECKLIST.md) as executable assertions and runs
it against EVERY declared method of OUR x402 paid surface — not just GET:

  GET  /x402/<resource_id>          -> 402 challenge (JSON envelope)
  POST /api/x402/challenge-bind     -> 402 once the wallet-ownership bind succeeds
  POST /api/x402/verify             -> 402 while the treasury transfer is pending
  GET  /api/paid/<resource_id>      -> 402 challenge when no access token presented
  POST /api/paid/<resource_id>      -> 402 challenge when no access token presented

Rules probed:
  R1  402 MUST carry `Cache-Control: no-store`
  R2  PAYMENT-REQUIRED header present; decoded envelope deep-equals body `accepts`
  R3a CORS MUST expose PAYMENT-REQUIRED on the 402 response
  R3b CORS preflight (OPTIONS) MUST allow the payment headers
  R4  undeclared methods -> 405 with a JSON body (never 404, never HTML)

Known-current deviations are xfail-marked with FINDING tags (strict=False):
the assertions stay strict; when the surface is fixed, delete the markers.
This commit's scope is the probe + checklist doc only (no route changes),
so gaps are REPORTED here, not fixed here.

Negative controls: `misconfigured_402` deliberately violates every
response rule and each check MUST raise on it; `wellformed_402` violates
nothing and each check MUST pass. If either stops discriminating, the probe
itself is broken — fix the probe, not the surface.

Run: PYTHONPATH=src:. <venv>/python -m pytest tests/pytest/test_x402_paid_surface_probe.py -q
"""
from __future__ import annotations

import base64
import binascii
import json

import pytest

WALLET = "0x" + "11" * 20
RESOURCE = "agent_task"

#: Header names buyers actually read (mirrors sincor2.x402_buyer parsing).
PAYMENT_REQUIRED_ALIASES = ("PAYMENT-REQUIRED", "X-Payment-Required", "Payment-Required")

#: Payment headers a browser buyer must be allowed to send cross-origin.
PAYMENT_HEADERS_NEEDED = ("x-payment-token", "x-payment")


# ---------------------------------------------------------------------------
# Checklist rule assertions (raise AssertionError on violation)
# ---------------------------------------------------------------------------

def check_rule1_no_store(resp) -> None:
    """RULE 1: every 402 MUST carry `Cache-Control: no-store`."""
    cc = resp.headers.get("Cache-Control", "")
    tokens = {t.strip().lower() for t in cc.split(",")}
    assert "no-store" in tokens, (
        "RULE 1 violated: 402 without 'Cache-Control: no-store' "
        f"(got Cache-Control: {cc!r})"
    )


def _decode_payment_required_header(value: str):
    """Decode a PAYMENT-REQUIRED header: raw JSON, base64 JSON, or base64url JSON."""
    text = value.strip().strip('"')
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    padded = text + "=" * (-len(text) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return json.loads(decoder(padded).decode("utf-8"))
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
    raise AssertionError(
        "RULE 2 violated: PAYMENT-REQUIRED header is neither JSON nor base64 JSON"
    )


def check_rule2_header_body_consistent(resp) -> None:
    """RULE 2: PAYMENT-REQUIRED header, decoded, MUST deep-equal body `accepts`."""
    header_value = None
    for name in PAYMENT_REQUIRED_ALIASES:
        if resp.headers.get(name):
            header_value = resp.headers.get(name)
            break
    assert header_value, "RULE 2 violated: 402 carries no PAYMENT-REQUIRED header"
    decoded = _decode_payment_required_header(header_value)
    body = resp.get_json(silent=True) or {}
    assert "accepts" in body, (
        "RULE 2 violated: 402 body has no 'accepts' envelope to compare against"
    )
    assert decoded == body["accepts"], (
        "RULE 2 violated: PAYMENT-REQUIRED header envelope differs from body 'accepts'"
    )


def check_rule3a_cors_exposes_payment_required(resp) -> None:
    """RULE 3a: CORS MUST expose PAYMENT-REQUIRED on the 402 response."""
    exposed = resp.headers.get("Access-Control-Expose-Headers", "")
    tokens = {t.strip().lower() for t in exposed.split(",") if t.strip()}
    assert "payment-required" in tokens, (
        "RULE 3 violated: Access-Control-Expose-Headers does not list "
        f"PAYMENT-REQUIRED (got {exposed!r})"
    )


def check_rule3b_preflight_allows_payment_headers(client, path: str) -> None:
    """RULE 3b: CORS preflight MUST allow the payment headers."""
    resp = client.options(
        path,
        headers={
            "Origin": "https://bazaar.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Payment-Token, X-Payment",
        },
    )
    assert resp.status_code in (200, 204), (
        f"RULE 3 violated: preflight on {path} returned {resp.status_code}"
    )
    assert resp.headers.get("Access-Control-Allow-Origin"), (
        f"RULE 3 violated: preflight on {path} has no Access-Control-Allow-Origin"
    )
    allowed = {
        t.strip().lower()
        for t in resp.headers.get("Access-Control-Allow-Headers", "").split(",")
        if t.strip()
    }
    for needed in PAYMENT_HEADERS_NEEDED:
        assert needed in allowed, (
            f"RULE 3 violated: preflight Allow-Headers {sorted(allowed)} "
            f"does not allow {needed}"
        )


def check_rule4_undeclared_method_405_json(client, method: str, path: str) -> None:
    """RULE 4: undeclared methods -> 405 with a JSON body (never 404, never HTML)."""
    resp = client.open(path, method=method)
    assert resp.status_code == 405, (
        f"RULE 4 violated: {method} {path} returned {resp.status_code}, want 405"
    )
    ctype = resp.headers.get("Content-Type", "")
    assert "application/json" in ctype, (
        f"RULE 4 violated: {method} {path} 405 body is {ctype!r}, not JSON"
    )


RESPONSE_RULE_CHECKS = (
    check_rule1_no_store,
    check_rule2_header_body_consistent,
    check_rule3a_cors_exposes_payment_required,
)


# ---------------------------------------------------------------------------
# Fixtures: isolated x402 DB + minimal app with ONLY the billing blueprint
# ---------------------------------------------------------------------------

@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    """Point the x402 SQLite store at a throwaway DB for the test."""
    import sincor2.x402_payments as x402

    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def paid_client():
    """Minimal Flask app with ONLY the mvp billing blueprint registered.

    Same proven pattern as test_x402_replay_auth.billing_client:
    sincor2.mvp_app must be imported first so billing._bind_mvp() binds
    against the initializing module (import-order quirk on origin/main).
    """
    import sincor2.mvp_app  # noqa: F401  (import order matters; see docstring)
    from flask import Flask

    from sincor2.mvp_blueprints import billing as billing_mod

    app = Flask("x402_paid_surface_probe")
    app.register_blueprint(billing_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _signed_bind(client, challenge_id: str, resource_id: str = RESOURCE):
    """POST /api/x402/challenge-bind with a fresh low-s EIP-191 signature.

    Returns (response, wallet). A valid bind returns 402/payment_required —
    that IS the paid surface under probe for this route.
    """
    from eth_account import Account
    from eth_account.messages import encode_defunct

    import sincor2.x402_payments as x402
    from sincor2.sig_canonical import is_low_s

    signed = None
    for _ in range(50):
        acct = Account.create()
        wallet = acct.address
        message = x402.challenge_bind_message(challenge_id, resource_id, wallet.lower())
        sig = acct.sign_message(encode_defunct(text=message)).signature
        if is_low_s(sig):
            signed = (wallet, "0x" + sig.hex())
            break
    assert signed, "probe could not mint a low-s bind signature after 50 keys"
    wallet, signature = signed
    resp = client.post(
        "/api/x402/challenge-bind",
        json={
            "challenge_id": challenge_id,
            "payer_wallet": wallet,
            "signature": signature,
        },
    )
    return resp, wallet


def _fetch_402(case: str, client, monkeypatch):
    """Produce the unpaid 402 response for one declared paid method."""
    import sincor2.x402_payments as x402

    if case == "challenge_get":
        return client.get(f"/x402/{RESOURCE}", query_string={"wallet": WALLET})
    if case == "paid_get":
        return client.get(f"/api/paid/{RESOURCE}", query_string={"wallet": WALLET})
    if case == "paid_post":
        return client.post(f"/api/paid/{RESOURCE}", json={"payer_wallet": WALLET})
    if case == "bind_post":
        ch = x402.create_challenge(RESOURCE)
        assert ch.get("challenge_id"), ch
        resp, _wallet = _signed_bind(client, ch["challenge_id"])
        return resp
    if case == "verify_post":
        # Mock the on-chain leg as "transfer not yet observed" — no network.
        monkeypatch.setattr(
            x402,
            "verify_treasury_transfer",
            lambda tx_hash, **kw: {"ok": False, "error": "tx_pending"},
        )
        ch = x402.create_challenge(RESOURCE)
        assert ch.get("challenge_id"), ch
        bind_resp, wallet = _signed_bind(client, ch["challenge_id"])
        assert bind_resp.status_code == 402, bind_resp.get_json(silent=True)
        return client.post(
            "/api/x402/verify",
            json={
                "challenge_id": ch["challenge_id"],
                "tx_hash": "0x" + "ab" * 32,
                "payer_wallet": wallet,
            },
        )
    raise AssertionError(f"unknown paid-surface case {case!r}")


#: Every declared method on our x402 paid endpoints (not just GET).
CASES = ["challenge_get", "bind_post", "verify_post", "paid_get", "paid_post"]

CASE_PATHS = {
    "challenge_get": f"/x402/{RESOURCE}",
    "bind_post": "/api/x402/challenge-bind",
    "verify_post": "/api/x402/verify",
    "paid_get": f"/api/paid/{RESOURCE}",
    "paid_post": f"/api/paid/{RESOURCE}",
}

#: (undeclared method, path) pairs — each must 405 with a JSON body.
UNDECLARED = [
    ("POST", f"/x402/{RESOURCE}"),
    ("GET", "/api/x402/challenge-bind"),
    ("DELETE", f"/api/paid/{RESOURCE}"),
    ("PUT", f"/api/paid/{RESOURCE}"),
]

_CHALLENGE_402_ERRORS = {"ownership_proof_required", "payment_required", "tx_pending"}


# ---------------------------------------------------------------------------
# The paid surface itself: every declared method must 402 when unpaid
# ---------------------------------------------------------------------------

class TestPaidSurface402Status:
    @pytest.mark.parametrize("case", CASES)
    def test_unpaid_request_yields_402_challenge(self, paid_client, x402_db,
                                                monkeypatch, case):
        resp = _fetch_402(case, paid_client, monkeypatch)
        assert resp.status_code == 402, (
            case, resp.status_code, resp.get_data(as_text=True)[:300])
        body = resp.get_json(silent=True) or {}
        assert body.get("error") in _CHALLENGE_402_ERRORS, body


# ---------------------------------------------------------------------------
# Checklist rules against the live surface.
#
# FINDING markers: the assertion is the checklist; the xfail records the
# measured deviation. Scope here is probe + doc only — no route changes —
# so deviations are reported, not fixed. Delete a marker when fixed.
# ---------------------------------------------------------------------------

class TestChecklistRules:
    @pytest.mark.xfail(
        strict=False,
        reason="FINDING-1: 402s carry no Cache-Control header — "
               "jsonify(ch), 402 in mvp_blueprints/billing.py sets none; "
               "checklist requires `no-store`.",
    )
    @pytest.mark.parametrize("case", CASES)
    def test_rule1_no_store_on_402(self, paid_client, x402_db, monkeypatch, case):
        resp = _fetch_402(case, paid_client, monkeypatch)
        assert resp.status_code == 402, (case, resp.status_code)
        check_rule1_no_store(resp)

    @pytest.mark.xfail(
        strict=False,
        reason="FINDING-2: no PAYMENT-REQUIRED header is emitted on 402s, and "
               "the body envelope lives under `payment`, not x402-v2 `accepts`.",
    )
    @pytest.mark.parametrize("case", CASES)
    def test_rule2_header_body_consistency(self, paid_client, x402_db,
                                          monkeypatch, case):
        resp = _fetch_402(case, paid_client, monkeypatch)
        assert resp.status_code == 402, (case, resp.status_code)
        check_rule2_header_body_consistent(resp)

    @pytest.mark.xfail(
        strict=False,
        reason="FINDING-3: no CORS headers on the billing blueprint — 402s do "
               "not expose PAYMENT-REQUIRED and OPTIONS preflight carries no "
               "Access-Control-Allow-* headers.",
    )
    @pytest.mark.parametrize("case", CASES)
    def test_rule3a_cors_exposes_header(self, paid_client, x402_db,
                                        monkeypatch, case):
        resp = _fetch_402(case, paid_client, monkeypatch)
        assert resp.status_code == 402, (case, resp.status_code)
        check_rule3a_cors_exposes_payment_required(resp)

    @pytest.mark.xfail(
        strict=False,
        reason="FINDING-3: OPTIONS preflight on paid routes carries no "
               "Access-Control-Allow-Origin / Allow-Headers (no CORS middleware "
               "on the billing blueprint).",
    )
    @pytest.mark.parametrize("case", CASES)
    def test_rule3b_preflight_allows_payment_headers(self, paid_client, case):
        check_rule3b_preflight_allows_payment_headers(paid_client, CASE_PATHS[case])

    @pytest.mark.xfail(
        strict=False,
        reason="FINDING-4: undeclared methods get Werkzeug's default HTML 405 "
               "page — no 405 errorhandler returning JSON on the blueprint.",
    )
    @pytest.mark.parametrize("method,path", UNDECLARED,
                             ids=[f"{m} {p}" for m, p in UNDECLARED])
    def test_rule4_undeclared_method_405_json(self, paid_client, method, path):
        check_rule4_undeclared_method_405_json(paid_client, method, path)


# ---------------------------------------------------------------------------
# Negative controls: the checks must discriminate.
# ---------------------------------------------------------------------------

def _synth_402(body: dict, headers: dict | None = None):
    """Synthetic 402 response (deliberately misconfigured or well-formed)."""
    from flask import Response

    resp = Response(json.dumps(body), status=402, content_type="application/json")
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


@pytest.fixture()
def misconfigured_402():
    """Deliberately violates every response rule (negative control).

    Mirrors the aiskills402 audit findings: no Cache-Control, header
    envelope (solana) differs from body `accepts` (base), no CORS exposure.
    """
    body = {
        "ok": False,
        "error": "payment_required",
        "accepts": [{"scheme": "exact", "network": "base", "maxAmountRequired": "1000"}],
    }
    headers = {
        "PAYMENT-REQUIRED": json.dumps(
            {"scheme": "exact", "network": "solana", "maxAmountRequired": "1000"}
        ),
    }
    return _synth_402(body, headers)


@pytest.fixture()
def wellformed_402():
    """Violates nothing — every response-rule check must pass on this."""
    accepts = [{"scheme": "exact", "network": "base", "maxAmountRequired": "1000"}]
    body = {"ok": False, "error": "payment_required", "accepts": accepts}
    headers = {
        # base64-encoded envelope, deep-equal to body["accepts"]
        "PAYMENT-REQUIRED": base64.b64encode(json.dumps(accepts).encode()).decode(),
        "Cache-Control": "no-store",
        "Access-Control-Expose-Headers": "PAYMENT-REQUIRED",
    }
    return _synth_402(body, headers)


@pytest.mark.parametrize(
    "check", RESPONSE_RULE_CHECKS, ids=[c.__name__ for c in RESPONSE_RULE_CHECKS]
)
def test_misconfigured_402_fails_every_response_rule(misconfigured_402, check):
    """Each checklist assertion must catch the deliberately broken fixture."""
    with pytest.raises(AssertionError):
        check(misconfigured_402)


@pytest.mark.parametrize(
    "check", RESPONSE_RULE_CHECKS, ids=[c.__name__ for c in RESPONSE_RULE_CHECKS]
)
def test_wellformed_402_passes_every_response_rule(wellformed_402, check):
    """Each checklist assertion must accept a compliant 402 (not vacuously failing)."""
    check(wellformed_402)  # must not raise


def _mini_app(*, cors: bool = False, json_405: bool = False):
    """Throwaway app for preflight / 405 negative controls."""
    from flask import Flask, jsonify

    app = Flask("x402_probe_negative")
    if cors:
        @app.after_request
        def _add_cors(resp):
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Headers"] = "X-Payment-Token, X-Payment"
            return resp

    if json_405:
        @app.errorhandler(405)
        def _method_not_allowed(_err):
            return jsonify({"ok": False, "error": "method_not_allowed"}), 405

    @app.route("/paid/x", methods=["GET"])
    def _paid():
        return jsonify({"ok": True})

    return app.test_client()


def test_preflight_check_fails_without_cors():
    with pytest.raises(AssertionError):
        check_rule3b_preflight_allows_payment_headers(_mini_app(cors=False), "/paid/x")


def test_preflight_check_passes_with_cors():
    check_rule3b_preflight_allows_payment_headers(_mini_app(cors=True), "/paid/x")


def test_rule4_check_fails_on_html_405():
    with pytest.raises(AssertionError):
        check_rule4_undeclared_method_405_json(_mini_app(json_405=False), "POST", "/paid/x")


def test_rule4_check_passes_on_json_405():
    check_rule4_undeclared_method_405_json(_mini_app(json_405=True), "POST", "/paid/x")
