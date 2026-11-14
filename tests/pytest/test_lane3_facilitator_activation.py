"""Lane 3 facilitator activation proof (2026-11-13).

Proves the exact flip from ``facilitator_gated`` to settling for the
Lane 3 paid endpoints (POST /x402-live/quick-scan, POST
/audit-express/order), against a LOCAL MOCK FACILITATOR that speaks
the real facilitator contract — the shapes Coinbase CDP
(https://api.cdp.coinbase.com/platform/v2/x402) and the keyless
testnet facilitator (https://x402.org/facilitator) implement:

  POST /verify  {paymentPayload, paymentRequirements}
    -> {"isValid": bool, "invalidReason": str, "payer": str}
  POST /settle  (same body)
    -> {"success": bool, "errorReason": str, "payer": str,
        "transaction": str, "network": str}

Note what the real contract does NOT have: a ``payment_ref`` in the
/verify response. The pre-fix ``_HttpFacilitator`` called /verify
only and demanded one, so a genuinely configured production
facilitator would have failed closed on every valid payment and
nothing would ever have settled. The mock therefore deliberately
returns the real response shapes (no payment_ref anywhere) and
validates real EIP-3009 signatures: it recovers the EIP-712 signer
and checks it against the authorization, the amount against the
server's OWN paymentRequirements, and the recipient against payTo —
the same checks a real facilitator performs. Payments in these
tests are signed with a THROWAWAY key that exists only in this
test process. No real keys, no network beyond 127.0.0.1, no chain.
"""
from __future__ import annotations

import hashlib
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from sincor2 import lane3

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
# Throwaway buyer key — generated for this test file only, holds no
# funds on any network, never leaves the test process.
BUYER_KEY = "0x" + "11" * 32
BUYER = Account.from_key(BUYER_KEY).address


# ── mock facilitator ────────────────────────────────────────────────────────

def _typed_data(auth: dict, asset: str) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": "USD Coin",
            "version": "2",
            "chainId": 8453,
            "verifyingContract": asset,
        },
        "message": {
            "from": auth["from"],
            "to": auth["to"],
            "value": int(auth["value"]),
            "validAfter": int(auth["validAfter"]),
            "validBefore": int(auth["validBefore"]),
            "nonce": auth["nonce"],
        },
    }


def _validate(body: dict, settled_nonces: set) -> tuple[bool, str, dict]:
    """The checks a real facilitator performs. Returns
    (ok, reason, authorization)."""
    payload = (body.get("paymentPayload") or {}).get("payload") or {}
    req = body.get("paymentRequirements") or {}
    auth = payload.get("authorization") or {}
    signature = payload.get("signature") or ""
    try:
        signer = Account.recover_message(
            encode_typed_data(full_message=_typed_data(auth, req["asset"])),
            signature=signature)
    except Exception:
        return False, "invalid_signature", auth
    if signer.lower() != str(auth.get("from", "")).lower():
        return False, "signer_mismatch", auth
    if int(auth["value"]) != int(req["maxAmountRequired"]):
        return False, "value_mismatch", auth
    if str(auth["to"]).lower() != str(req["payTo"]).lower():
        return False, "recipient_mismatch", auth
    if int(auth["validBefore"]) < int(time.time()):
        return False, "authorization_expired", auth
    if auth["nonce"] in settled_nonces:
        return False, "authorization_already_settled", auth
    return True, "", auth


class _MockFacilitator:
    def __init__(self, https_cert=None, redirect_to=None) -> None:
        self.calls: list[dict] = []
        self.settled_nonces: set = set()
        self.force_invalid = False
        self.force_settle_fail = False
        # When set, /verify answers 302 -> redirect_to instead of a
        # verification result (a redirecting facilitator).
        self.redirect_to = redirect_to

        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                mock.calls.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": body,
                })
                if mock.redirect_to is not None and self.path == "/verify":
                    self.send_response(302)
                    self.send_header("Location", mock.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/verify":
                    data = mock.verify(body)
                elif self.path == "/settle":
                    data = mock.settle(body)
                else:
                    data = {"error": "unknown_path"}
                out = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        if https_cert is not None:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(https_cert[0], https_cert[1])
            self._server.socket = ctx.wrap_socket(
                self._server.socket, server_side=True)
        self.port = self._server.server_address[1]
        scheme = "https" if https_cert is not None else "http"
        self.url = f"{scheme}://127.0.0.1:{self.port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def verify(self, body: dict) -> dict:
        if self.force_invalid:
            return {"isValid": False,
                    "invalidReason": "forced_invalid_for_test",
                    "payer": BUYER}
        ok, reason, auth = _validate(body, self.settled_nonces)
        # Real contract: NO payment_ref in a /verify response.
        return {"isValid": ok, "invalidReason": reason,
                "payer": auth.get("from", "")}

    def settle(self, body: dict) -> dict:
        if self.force_settle_fail:
            return {"success": False,
                    "errorReason": "forced_settle_failure_for_test",
                    "payer": BUYER, "transaction": "", "network": "base"}
        ok, reason, auth = _validate(body, self.settled_nonces)
        if not ok:
            return {"success": False, "errorReason": reason,
                    "payer": auth.get("from", ""), "transaction": "",
                    "network": "base"}
        self.settled_nonces.add(auth["nonce"])
        tx = "0x" + hashlib.sha256(
            auth["nonce"].encode("utf-8")).hexdigest()
        return {"success": True, "errorReason": "",
                "payer": auth.get("from", ""), "transaction": tx,
                "network": "base"}

    def paths(self) -> list[str]:
        return [c["path"] for c in self.calls]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _RecordingServer:
    """Attacker endpoint: records anything that reaches it and
    answers a benign 200, so a credential-forwarding client would
    happily deliver the goods here."""

    def __init__(self) -> None:
        self.hits: list[dict] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                recorder.hits.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": dict(self.headers),
                })
                out = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = _record
            do_POST = _record

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _self_signed_cert(tmp_path):
    """Self-signed cert for 127.0.0.1 (IP SAN), generated in-test.
    Returns (cert_path, key_path). Tests point SSL_CERT_FILE at the
    cert so the stdlib HTTPS stack trusts exactly this mock."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]),
            critical=False)
        .sign(key, hashes.SHA256()))
    cert_path = tmp_path / "mock-cert.pem"
    key_path = tmp_path / "mock-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return str(cert_path), str(key_path)


def _https_mock(tmp_path, monkeypatch, redirect_to=None):
    """Start the mock facilitator over HTTPS on loopback, trusting
    its cert for this test only (via SSL_CERT_FILE). The caller
    closes the returned mock."""
    cert = _self_signed_cert(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", cert[0])
    return _MockFacilitator(https_cert=cert, redirect_to=redirect_to)


def _configure_https(monkeypatch, mock, *, allowlist="127.0.0.1"):
    """Deployment state pointing at the HTTPS mock. ``allowlist``
    is the SINCOR_X402_CDP_HOSTS value; None leaves the env unset
    (production default allowlist)."""
    monkeypatch.setenv("SINCOR_X402_FACILITATOR_URL", mock.url)
    if allowlist is None:
        monkeypatch.delenv("SINCOR_X402_CDP_HOSTS", raising=False)
    else:
        monkeypatch.setenv("SINCOR_X402_CDP_HOSTS", allowlist)
    monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("CDP_API_KEY_SECRET", raising=False)
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    lane3._ENDPOINT_CACHE.clear()


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mvp_app():
    from sincor2.mvp_app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture(scope="module")
def mvp_client(mvp_app):
    return mvp_app.test_client()


@pytest.fixture
def mock_facilitator():
    mock = _MockFacilitator()
    try:
        yield mock
    finally:
        mock.close()


@pytest.fixture
def configured(monkeypatch, mock_facilitator):
    """Deployment state WITH a facilitator configured (the flip)."""
    monkeypatch.setenv("SINCOR_X402_FACILITATOR_URL", mock_facilitator.url)
    monkeypatch.delenv("CDP_API_KEY_ID", raising=False)
    monkeypatch.delenv("CDP_API_KEY_SECRET", raising=False)
    monkeypatch.delenv("CDP_API_KEY", raising=False)
    # Loopback must never leave through a proxy.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    lane3._ENDPOINT_CACHE.clear()
    yield mock_facilitator
    lane3._ENDPOINT_CACHE.clear()


@pytest.fixture
def stubbed_rpc(monkeypatch):
    calls = []

    def fake_rpc(method, params):
        calls.append(method)
        return {
            "eth_getCode": "0x60806040",
            "eth_getBalance": "0xde0b6b3a7640000",
            "eth_blockNumber": "0x100",
        }[method]

    monkeypatch.setattr(lane3, "_base_rpc", fake_rpc)
    return calls


# ── payment construction (real EIP-3009 signatures, throwaway key) ─────────

_NONCE_COUNTER = [0]


def _signed_header(accepted: dict, value: int) -> str:
    _NONCE_COUNTER[0] += 1
    nonce = "0x" + _NONCE_COUNTER[0].to_bytes(32, "big").hex()
    auth = {
        "from": BUYER,
        "to": accepted["payTo"],
        "value": value,
        "validAfter": 0,
        "validBefore": 9999999999,
        "nonce": nonce,
    }
    signed = Account.sign_message(
        encode_typed_data(full_message=_typed_data(auth, accepted["asset"])),
        BUYER_KEY)
    wire_auth = dict(auth)
    wire_auth["value"] = str(value)  # real x402 clients send strings
    return json.dumps({
        "x402Version": 1,
        "scheme": "exact",
        "network": "base",
        "accepted": accepted,
        "payload": {
            "signature": "0x" + signed.signature.hex(),
            "authorization": wire_auth,
        },
    })


def _challenge(kind: str) -> dict:
    fn = lane3.quick_scan if kind == "quick-scan" else lane3.audit_order
    _, body = fn({}, {})
    return body["accepts"][0]


# ── the flip, end to end ────────────────────────────────────────────────────

def test_unconfigured_still_gated(monkeypatch):
    """Control: without SINCOR_X402_FACILITATOR_URL the same signed
    payment stays facilitator_gated and the work never runs."""
    monkeypatch.delenv("SINCOR_X402_FACILITATOR_URL", raising=False)
    lane3._ENDPOINT_CACHE.clear()
    header = _signed_header(_challenge("quick-scan"), 10000)
    status, body = lane3.quick_scan({"X-PAYMENT": header},
                                    {"address": USDC})
    assert status == 402
    assert body["settlement"] == "facilitator_gated"
    assert "result" not in body
    lane3._ENDPOINT_CACHE.clear()


def test_quick_scan_settles_end_to_end(configured, mvp_client, stubbed_rpc):
    header = _signed_header(_challenge("quick-scan"), 10000)
    resp = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["verification"]["passed"] is True
    assert body["result"]["is_contract"] is True
    assert body["result"]["is_usdc_contract"] is True
    # Settlement receipt rides in the 200 body.
    assert body["settlement"]
    # The facilitator saw verify THEN settle, exactly once each, with
    # the server's own requirement (payTo = treasury).
    assert configured.paths() == ["/verify", "/settle"]
    settle_body = configured.calls[1]["body"]
    assert settle_body["paymentRequirements"]["payTo"] == TREASURY
    assert settle_body["paymentRequirements"]["maxAmountRequired"] == "10000"
    # The paid work ran exactly once.
    assert stubbed_rpc.count("eth_getCode") == 1


def test_audit_order_settles_end_to_end(configured, mvp_client):
    header = _signed_header(_challenge("audit-order"), 500_000_000)
    resp = mvp_client.post(
        "/audit-express/order",
        json={"contact": "you@example.com", "repo": "https://x.example/r"},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["result"]["order_ref"].startswith("audit-express-")
    assert body["result"]["status"] == "intake_received"
    assert body["settlement"]
    assert configured.paths() == ["/verify", "/settle"]


# ── fail-closed paths ───────────────────────────────────────────────────────

def test_verify_invalid_never_settles(configured, mvp_client, stubbed_rpc):
    configured.force_invalid = True
    header = _signed_header(_challenge("quick-scan"), 10000)
    resp = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 402
    assert resp.get_json()["error"] == "payment_verification_failed"
    assert "result" not in resp.get_json()
    assert configured.paths() == ["/verify"]  # settle never attempted
    assert stubbed_rpc == []  # work never ran


def test_settle_failure_never_delivers(configured, mvp_client, stubbed_rpc):
    configured.force_settle_fail = True
    header = _signed_header(_challenge("quick-scan"), 10000)
    resp = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 402
    assert resp.get_json()["error"] == "payment_verification_failed"
    assert "result" not in resp.get_json()
    assert configured.paths() == ["/verify", "/settle"]
    assert stubbed_rpc == []  # verified-but-unsettled pays for nothing


def test_underpaid_signature_rejected(configured, mvp_client):
    """A genuinely signed authorization for the WRONG amount: the
    mock facilitator's own validation rejects it (amount vs the
    server's paymentRequirements), proving the mock is not a rubber
    stamp and the amount hedge holds end to end."""
    header = _signed_header(_challenge("quick-scan"), 9999)
    resp = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 402
    assert resp.get_json()["error"] == "payment_verification_failed"
    assert configured.paths() == ["/verify"]


def test_replay_never_delivers_twice(configured, mvp_client, stubbed_rpc):
    header = _signed_header(_challenge("quick-scan"), 10000)
    first = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert first.status_code == 200
    second = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    # The facilitator consumed the authorization's nonce at settle;
    # on replay its /verify refuses, so the work cannot run twice.
    assert second.status_code == 402
    assert stubbed_rpc.count("eth_getCode") == 1
    assert configured.paths().count("/settle") == 1


def test_unreachable_facilitator_fails_closed(monkeypatch, mvp_client):
    monkeypatch.setenv("SINCOR_X402_FACILITATOR_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    lane3._ENDPOINT_CACHE.clear()
    header = _signed_header(_challenge("quick-scan"), 10000)
    resp = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 402
    assert resp.get_json()["error"] == "payment_verification_failed"
    lane3._ENDPOINT_CACHE.clear()


# ── CDP auth headers ────────────────────────────────────────────────────────

def test_cdp_jwt_auth_headers(monkeypatch, tmp_path, mvp_client,
                              stubbed_rpc):
    """With CDP_API_KEY_ID + CDP_API_KEY_SECRET (a throwaway EC key
    generated in-test) and the facilitator's HTTPS host on the CDP
    allowlist, every facilitator call carries a per-request ES256
    Bearer JWT scoped to that call's exact method+host+path — the
    auth the real CDP facilitator requires."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    mock = _https_mock(tmp_path, monkeypatch)
    try:
        _configure_https(monkeypatch, mock)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode("utf-8")
        public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
        monkeypatch.setenv("CDP_API_KEY_ID", "test-key-id-not-a-real-key")
        monkeypatch.setenv("CDP_API_KEY_SECRET", pem)

        header = _signed_header(_challenge("quick-scan"), 10000)
        resp = mvp_client.post(
            "/x402-live/quick-scan",
            json={"address": USDC},
            headers={"X-PAYMENT": header},
        )
        assert resp.status_code == 200
        assert mock.paths() == ["/verify", "/settle"]
        for call, path in zip(mock.calls, ["/verify", "/settle"]):
            authz = call["headers"].get("Authorization", "")
            assert authz.startswith("Bearer ")
            token = authz[len("Bearer "):]
            headers = pyjwt.get_unverified_header(token)
            assert headers["alg"] == "ES256"
            assert headers["kid"] == "test-key-id-not-a-real-key"
            claims = pyjwt.decode(token, public_pem, algorithms=["ES256"])
            assert claims["sub"] == "test-key-id-not-a-real-key"
            assert claims["uris"] == [
                f"POST 127.0.0.1:{mock.port}{path}"]
    finally:
        mock.close()
        lane3._ENDPOINT_CACHE.clear()


# ── CDP credential containment (Strix medium, PR #691) ─────────────────────
# The pre-patch client attached the CDP credential to whatever host
# SINCOR_X402_FACILITATOR_URL named, and urllib's default redirect
# handler re-sent the Authorization header to redirect targets on
# other origins — a compromised or redirecting facilitator would
# receive the operator's long-lived CDP key (CWE-522). The tests
# below pin the containment: allowlist + HTTPS gating, and
# fail-closed redirect refusal on credentialed requests.

def test_credentials_allowed_matrix(monkeypatch):
    """The gate itself: default allowlist is the CDP host over HTTPS
    only; SINCOR_X402_CDP_HOSTS replaces it when set."""
    monkeypatch.delenv("SINCOR_X402_CDP_HOSTS", raising=False)
    assert lane3._credentials_allowed(
        "https://api.cdp.coinbase.com/platform/v2/x402")
    assert not lane3._credentials_allowed(
        "http://api.cdp.coinbase.com/platform/v2/x402")
    assert not lane3._credentials_allowed("https://x402.org/facilitator")
    assert not lane3._credentials_allowed("https://evil.example.com/x")
    monkeypatch.setenv("SINCOR_X402_CDP_HOSTS",
                       "x402.org, other.example:8443")
    assert lane3._credentials_allowed("https://x402.org/facilitator")
    assert lane3._credentials_allowed("https://other.example:8443/f")
    assert not lane3._credentials_allowed(
        "https://api.cdp.coinbase.com/platform/v2/x402")
    assert not lane3._credentials_allowed("http://x402.org/facilitator")


def test_cdp_redirect_never_forwards_credentials(monkeypatch, tmp_path,
                                                 mvp_client, stubbed_rpc):
    """The allowlisted facilitator answers /verify with a 302 to an
    attacker host. The credential attached to the first hop must NOT
    be forwarded: the credentialed opener refuses the redirect, the
    payment fails closed, and the attacker sees nothing."""
    attacker = _RecordingServer()
    mock = _https_mock(tmp_path, monkeypatch,
                       redirect_to=attacker.url + "/steal")
    try:
        _configure_https(monkeypatch, mock)
        monkeypatch.setenv("CDP_API_KEY",
                           "test-raw-cdp-key-not-a-real-key")
        header = _signed_header(_challenge("quick-scan"), 10000)
        resp = mvp_client.post(
            "/x402-live/quick-scan",
            json={"address": USDC},
            headers={"X-PAYMENT": header},
        )
        assert resp.status_code == 402
        assert resp.get_json()["error"] == "payment_verification_failed"
        assert "result" not in resp.get_json()
        # The credential WAS attached to the allowlisted first hop…
        assert mock.paths() == ["/verify"]
        assert mock.calls[0]["headers"].get("Authorization") == \
            "Bearer test-raw-cdp-key-not-a-real-key"
        # …and never reached the attacker: no request arrived there.
        assert attacker.hits == []
        assert stubbed_rpc == []  # work never ran
    finally:
        mock.close()
        attacker.close()
        lane3._ENDPOINT_CACHE.clear()


def test_no_credentials_to_non_allowlist_host(monkeypatch, configured,
                                              mvp_client, stubbed_rpc):
    """CDP credentials are configured, but the facilitator URL is a
    plain-HTTP loopback host that is NOT on the allowlist: neither
    the raw key nor the JWT pair may be sent. The facilitator is
    still called — keyless, exactly like the testnet facilitator —
    and the payment settles."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("utf-8")
    monkeypatch.setenv("CDP_API_KEY_ID", "test-key-id-not-a-real-key")
    monkeypatch.setenv("CDP_API_KEY_SECRET", pem)
    monkeypatch.setenv("CDP_API_KEY", "test-raw-cdp-key-not-a-real-key")

    header = _signed_header(_challenge("quick-scan"), 10000)
    resp = mvp_client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 200
    assert configured.paths() == ["/verify", "/settle"]
    for call in configured.calls:
        assert "Authorization" not in call["headers"]


def test_no_credentials_to_unlisted_https_host(monkeypatch, tmp_path,
                                               mvp_client, stubbed_rpc):
    """HTTPS alone is not enough: an HTTPS facilitator host that is
    not on the allowlist (env unset -> production default) receives
    no credential either."""
    mock = _https_mock(tmp_path, monkeypatch)
    try:
        _configure_https(monkeypatch, mock, allowlist=None)
        monkeypatch.setenv("CDP_API_KEY",
                           "test-raw-cdp-key-not-a-real-key")
        header = _signed_header(_challenge("quick-scan"), 10000)
        resp = mvp_client.post(
            "/x402-live/quick-scan",
            json={"address": USDC},
            headers={"X-PAYMENT": header},
        )
        assert resp.status_code == 200
        assert mock.paths() == ["/verify", "/settle"]
        for call in mock.calls:
            assert "Authorization" not in call["headers"]
    finally:
        mock.close()
        lane3._ENDPOINT_CACHE.clear()


def test_cdp_allowlisted_host_settles_end_to_end(monkeypatch, tmp_path,
                                                 mvp_client, stubbed_rpc):
    """Happy path on the allowlist: the HTTPS facilitator host named
    in SINCOR_X402_CDP_HOSTS receives the CDP credential on both
    calls, and the payment verifies + settles end to end."""
    mock = _https_mock(tmp_path, monkeypatch)
    try:
        _configure_https(monkeypatch, mock)
        monkeypatch.setenv("CDP_API_KEY",
                           "test-raw-cdp-key-not-a-real-key")
        header = _signed_header(_challenge("quick-scan"), 10000)
        resp = mvp_client.post(
            "/x402-live/quick-scan",
            json={"address": USDC},
            headers={"X-PAYMENT": header},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["verification"]["passed"] is True
        assert body["settlement"]
        assert mock.paths() == ["/verify", "/settle"]
        for call in mock.calls:
            assert call["headers"].get("Authorization") == \
                "Bearer test-raw-cdp-key-not-a-real-key"
        assert stubbed_rpc.count("eth_getCode") == 1
    finally:
        mock.close()
        lane3._ENDPOINT_CACHE.clear()
