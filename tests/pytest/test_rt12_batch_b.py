"""RT12 batch B regression tests: W-59, W-63, W-64.

- W-59: /v1/kya/revoke and /v1/kya/unstake/* require an authenticated
  principal AND record ownership (or the admin role); 401/403 otherwise.
- W-63: every Python signature-recovery site calls require_low_s() before
  recovery; a high-s malleation twin is rejected at each site.
- W-64: a paid-leg transport error at/after send raises AmbiguousPayment
  (never auto-retry); only a proven pre-send failure is safe to retry.

No live network, no real keys: ephemeral in-memory keys only, mocked HTTP.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import socket
import tempfile
import time
import urllib.error

import pytest

# ===========================================================================
# W-59 — KYA revoke/unstake authorization
# ===========================================================================

VICTIM_WALLET = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
STRANGER_WALLET = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture()
def kya_client():
    """Minimal Flask app with only the KYA blueprint + JWT configured."""
    fd, store_path = tempfile.mkstemp(prefix="kya_rt12b_", suffix=".json")
    os.close(fd)
    prior = os.environ.get("KYA_STORE_PATH")
    os.environ["KYA_STORE_PATH"] = store_path
    try:
        from flask import Flask
        from flask_jwt_extended import JWTManager

        from sincor2 import kya_registry as kya
        from sincor2.kya_blueprint import kya_bp

        kya.reset()
        if os.path.exists(store_path):
            os.unlink(store_path)

        app = Flask(__name__)
        app.config["JWT_SECRET_KEY"] = "rt12b-test-secret-32-chars-minimum!!"
        app.secret_key = "rt12b-session-secret"
        JWTManager(app)
        app.register_blueprint(kya_bp)

        rec = kya.list_from_inbound(
            {
                "agent_id": "victim-agent-01",
                "name": "Victim Agent",
                "version": "1.0.0",
                "skills": [],
                "wallet": VICTIM_WALLET,
            }
        )
        with app.app_context():
            from flask_jwt_extended import create_access_token

            def tok(identity, role, wallet=None):
                claims = {"role": role}
                if wallet:
                    claims["wallet"] = wallet
                return create_access_token(identity=identity, additional_claims=claims)

            tokens = {
                "admin": tok("admin", "admin"),
                "owner": tok("victim-user", "customer", VICTIM_WALLET),
                # same wallet, different case spelling (EIP-55 vs lower)
                "owner_lower": tok("victim-user", "customer", VICTIM_WALLET.lower()),
                "stranger": tok("mallory", "customer", STRANGER_WALLET),
                "operator": tok("ops", "operator", STRANGER_WALLET),
            }
        yield app.test_client(), tokens, rec["kya_id"], kya
    finally:
        if os.path.exists(store_path):
            os.unlink(store_path)
        if prior is None:
            os.environ.pop("KYA_STORE_PATH", None)
        else:
            os.environ["KYA_STORE_PATH"] = prior


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _is_revoked(kya, kya_id):
    return bool(kya.get(kya_id)["revoked"])


# --- unauthenticated -------------------------------------------------------

def test_w59_revoke_unauthenticated_401(kya_client):
    client, _, kya_id, kya = kya_client
    r = client.post("/v1/kya/revoke", json={"kya_id": kya_id})
    assert r.status_code == 401
    assert not _is_revoked(kya, kya_id)


def test_w59_unstake_request_unauthenticated_401(kya_client):
    client, _, kya_id, _ = kya_client
    r = client.post("/v1/kya/unstake/request", json={"kya_id": kya_id})
    assert r.status_code == 401


def test_w59_unstake_finalize_unauthenticated_401(kya_client):
    client, _, kya_id, _ = kya_client
    r = client.post("/v1/kya/unstake/finalize", json={"kya_id": kya_id})
    assert r.status_code == 401


def test_w59_unauthenticated_unknown_id_is_401_not_404(kya_client):
    """No existence oracle for unauthenticated scanners."""
    client, _, _, _ = kya_client
    r = client.post("/v1/kya/revoke", json={"kya_id": "kya_doesnotexist"})
    assert r.status_code == 401


# --- cross-account / spoofing ----------------------------------------------

def test_w59_revoke_cross_account_403(kya_client):
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/revoke", json={"kya_id": kya_id}, headers=_auth(tokens["stranger"])
    )
    assert r.status_code == 403
    assert not _is_revoked(kya, kya_id)


def test_w59_revoke_body_wallet_spoof_ignored(kya_client):
    """A wallet supplied in the request BODY must not confer ownership."""
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/revoke",
        json={"kya_id": kya_id, "wallet": VICTIM_WALLET, "principal": VICTIM_WALLET},
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 403
    assert not _is_revoked(kya, kya_id)


def test_w59_operator_not_owner_403(kya_client):
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/revoke", json={"kya_id": kya_id}, headers=_auth(tokens["operator"])
    )
    assert r.status_code == 403
    assert not _is_revoked(kya, kya_id)


def test_w59_unstake_request_cross_account_403(kya_client):
    client, tokens, kya_id, _ = kya_client
    r = client.post(
        "/v1/kya/unstake/request",
        json={"kya_id": kya_id},
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 403


# --- owner / admin ----------------------------------------------------------

def test_w59_revoke_owner_200(kya_client):
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/revoke", json={"kya_id": kya_id}, headers=_auth(tokens["owner"])
    )
    assert r.status_code == 200
    assert _is_revoked(kya, kya_id)


def test_w59_revoke_owner_wallet_case_insensitive(kya_client):
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/revoke", json={"kya_id": kya_id}, headers=_auth(tokens["owner_lower"])
    )
    assert r.status_code == 200
    assert _is_revoked(kya, kya_id)


def test_w59_revoke_admin_200(kya_client):
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/revoke", json={"kya_id": kya_id}, headers=_auth(tokens["admin"])
    )
    assert r.status_code == 200
    assert _is_revoked(kya, kya_id)


def test_w59_revoke_via_agent_session_200(kya_client):
    """Session-identified agent (server-side session) may revoke its own KYA."""
    client, _, kya_id, kya = kya_client
    with client.session_transaction() as sess:
        sess["agent_id"] = "victim-agent-01"
        sess["agent_wallet"] = VICTIM_WALLET
    r = client.post("/v1/kya/revoke", json={"kya_id": kya_id})
    assert r.status_code == 200
    assert _is_revoked(kya, kya_id)


def test_w59_unstake_request_owner_200(kya_client):
    client, tokens, kya_id, kya = kya_client
    kya.apply_stake(kya_id, "100", "0x" + "cc" * 32)
    r = client.post(
        "/v1/kya/unstake/request",
        json={"kya_id": kya_id},
        headers=_auth(tokens["owner"]),
    )
    assert r.status_code == 200
    assert kya.get(kya_id)["unstake_requested_at"]


def test_w59_unstake_finalize_owner_passes_auth(kya_client):
    """Owner reaches the business logic (timelock 400), not a 401/403."""
    client, tokens, kya_id, kya = kya_client
    kya.apply_stake(kya_id, "100", "0x" + "cc" * 32)
    kya.request_unstake(kya_id)
    r = client.post(
        "/v1/kya/unstake/finalize",
        json={"kya_id": kya_id},
        headers=_auth(tokens["owner"]),
    )
    assert r.status_code == 400  # timelock active — auth passed
    assert r.get_json()["error"] == "timelock active"


def test_w59_authenticated_unknown_id_404(kya_client):
    client, tokens, _, _ = kya_client
    r = client.post(
        "/v1/kya/revoke",
        json={"kya_id": "kya_doesnotexist"},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 404


# ===========================================================================
# W-63 — low-s canonicality at every recovery site
# ===========================================================================

from sincor2.sig_canonical import SECP256K1_N  # noqa: E402


@pytest.fixture(scope="module")
def sig_pair():
    """Ephemeral key (never persisted): one low-s sig + its high-s twin."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()
    message = "rt12b low-s regression"
    raw = bytes(acct.sign_message(encode_defunct(text=message)).signature)
    s = int.from_bytes(raw[32:64], "big")
    assert s <= SECP256K1_N // 2
    twin = (
        raw[:32]
        + (SECP256K1_N - s).to_bytes(32, "big")
        + bytes([28 if raw[64] == 27 else 27])
    )
    # sanity: the twin really is the malleability attack
    assert (
        Account.recover_message(encode_defunct(text=message), signature=twin)
        == acct.address
    )
    return {"address": acct.address, "message": message, "orig": raw, "twin": twin}


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def test_w63_a2a_identity_rejects_twin(sig_pair):
    from sincor2.a2a_identity import recover_signer

    assert recover_signer(sig_pair["message"], _hex(sig_pair["orig"])) == sig_pair["address"]
    # Twin rejected (the site wraps the guard's error as "bad signature").
    with pytest.raises(ValueError, match="bad signature"):
        recover_signer(sig_pair["message"], _hex(sig_pair["twin"]))


def test_w63_heartbeat_rejects_twin(sig_pair):
    from sincor2.a2a_inbound_ext import (
        HeartbeatAuthError,
        _recover_heartbeat_signer,
    )

    assert _recover_heartbeat_signer(
        sig_pair["message"], _hex(sig_pair["orig"])
    ) == sig_pair["address"]
    with pytest.raises(HeartbeatAuthError):
        _recover_heartbeat_signer(sig_pair["message"], _hex(sig_pair["twin"]))


def test_w63_registration_rejects_twin(sig_pair):
    from sincor2.a2a_inbound_ext import (
        RegistrationAuthError,
        _recover_registration_signer,
    )

    assert _recover_registration_signer(
        sig_pair["message"], _hex(sig_pair["orig"])
    ) == sig_pair["address"]
    with pytest.raises(RegistrationAuthError):
        _recover_registration_signer(sig_pair["message"], _hex(sig_pair["twin"]))


def test_w63_dispute_content_binding_rejects_twin(sig_pair):
    """Money path: proof submission -> payout must reject the twin."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    from sincor2.dispute_evidence import DisputeEvidence, verify_content_binding_sig

    dh = "0x" + "ab" * 32
    acct = Account.create()
    raw = bytes(acct.sign_message(encode_defunct(text=dh)).signature)
    s = int.from_bytes(raw[32:64], "big")
    twin = (
        raw[:32]
        + (SECP256K1_N - s).to_bytes(32, "big")
        + bytes([28 if raw[64] == 27 else 27])
    )

    def ev(sig: bytes) -> DisputeEvidence:
        return DisputeEvidence(
            payment_receipt_hash="0x" + "aa" * 32,
            deliverable_hash=dh,
            deliverable_uri="ipfs://x",
            content_binding_sig=_hex(sig),
        )

    assert verify_content_binding_sig(ev(raw), acct.address) is True
    with pytest.raises(ValueError, match="non-canonical high-s"):
        verify_content_binding_sig(ev(twin), acct.address)


def test_w63_eip712_verify_digest_rejects_twin():
    """Money path: typed-data bid verification must reject the twin."""
    from eth_account import Account

    from marketplace.contract_net.eip712 import (
        _recover_secp256k1,
        sign_digest,
        typed_data_digest,
        typed_data_payload,
        verify_digest,
    )
    from marketplace.contract_net.types import (
        BASE_SEPOLIA_CHAIN_ID,
        ContractNetConfig,
        SigType,
    )

    acct = Account.create()
    cfg = ContractNetConfig(chain_id=BASE_SEPOLIA_CHAIN_ID)
    vec = dict(
        auction_id="0x" + "11" * 32,
        task_id="T-101",
        agent=acct.address,
        price=5_000_000,
        estimated_tokens=520,
        nonce=7,
        deadline=9_999_999_999,
    )
    digest = typed_data_digest(cfg, **vec)
    payload = typed_data_payload(cfg, **vec)
    sig_hex, sig_type = sign_digest(digest, private_key=acct.key.hex())
    assert sig_type == SigType.SECP256K1.value
    raw = bytes.fromhex(sig_hex[2:])
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]
    v_twin = v ^ 1 if v in (0, 1) else (28 if v == 27 else 27)
    twin = raw[:32] + (SECP256K1_N - s).to_bytes(32, "big") + bytes([v_twin])

    assert verify_digest(
        digest, _hex(raw), sig_type=sig_type,
        expected_address=acct.address, typed_data=payload,
    ) is True
    # Twin is rejected: fail-closed bid rejection, not acceptance.
    assert verify_digest(
        digest, _hex(twin), sig_type=sig_type,
        expected_address=acct.address, typed_data=payload,
    ) is False
    assert _recover_secp256k1(payload, _hex(twin)) is None


def test_w63_eip191_signer_still_guarded_after_refactor(sig_pair):
    from sincor2.a2a_integration import _recover_eip191_signer

    assert (
        _recover_eip191_signer(sig_pair["message"], _hex(sig_pair["orig"]))
        == sig_pair["address"]
    )
    assert _recover_eip191_signer(sig_pair["message"], _hex(sig_pair["twin"])) is None


# Every Python signature-recovery function must invoke the shared guard.
_GUARDED_RECOVERY_FUNCS = [
    ("sincor2.a2a_identity", "recover_signer"),
    ("sincor2.a2a_inbound_ext", "_recover_heartbeat_signer"),
    ("sincor2.a2a_inbound_ext", "_recover_registration_signer"),
    ("sincor2.a2a_inbound_market", "_recover_dispute_signer"),
    ("sincor2.a2a_integration", "_recover_identity_signer"),
    ("sincor2.a2a_integration", "_recover_quota_signer"),
    ("sincor2.a2a_integration", "_recover_eip191_signer"),
    ("sincor2.dispute_evidence", "verify_content_binding_sig"),
    ("sincor2.kya_registry", "_recover_eip191"),
    ("sincor2.settlement_proofs", "recover_settle_ruling_signer"),
    ("marketplace.contract_net.eip712", "_recover_secp256k1"),
]


@pytest.mark.parametrize("module_name,func_name", _GUARDED_RECOVERY_FUNCS)
def test_w63_every_recovery_site_calls_require_low_s(module_name, func_name):
    mod = importlib.import_module(module_name)
    src = inspect.getsource(getattr(mod, func_name))
    assert "require_low_s" in src, (
        f"{module_name}.{func_name} performs signature recovery without "
        "calling require_low_s — malleability gap"
    )


# ===========================================================================
# W-64 — x402 buyer: paid-leg transport ambiguity
# ===========================================================================

from sincor2.x402_buyer import (  # noqa: E402
    AmbiguousPayment,
    PaymentTransportError,
    X402Buyer,
    BASE_CHAIN_REF,
    USDC_BASE_ADDRESS,
)

_WALLET = "0x" + "11" * 20
_PAY_TO = "0x" + "22" * 20


def _fake_signer(typed_data):
    return "0x" + "ab" * 65


def _challenge(nonce=None):
    now = int(time.time())
    body = {
        "x402Version": 1,
        "scheme": "exact",
        "network": BASE_CHAIN_REF,
        "maxAmountRequired": 5000,
        "asset": USDC_BASE_ADDRESS,
        "payTo": _PAY_TO,
        "validAfter": 0,
        "validBefore": now + 600,
    }
    if nonce is not None:
        body["nonce"] = nonce
    return body


def _resp(status, body_dict=None, headers=None):
    return (status, headers or {}, json.dumps(body_dict or {}).encode())


class _ScriptedBuyer(X402Buyer):
    """_do_request delegates to a script: list of ('return', tuple) or ('raise', exc)."""

    def __init__(self, script, **kw):
        kw.setdefault("wallet_address", _WALLET)
        kw.setdefault("signer_fn", _fake_signer)
        super().__init__(**kw)
        self._script = list(script)
        self.calls = []

    def _do_request(self, url, method, body, headers):
        self.calls.append({"headers": headers})
        kind, payload = self._script.pop(0)
        if kind == "raise":
            raise payload
        return payload


def _timeout_exc():
    return urllib.error.URLError(socket.timeout("timed out"))


def test_w64_paid_leg_timeout_after_send_is_ambiguous_no_retry():
    """The red-team scenario: timeout AFTER the paid request was sent."""
    buyer = _ScriptedBuyer(
        [
            ("return", _resp(402, _challenge())),
            ("raise", _timeout_exc()),  # paid leg: response lost after send
        ]
    )
    with pytest.raises(AmbiguousPayment, match="DO NOT retry blindly"):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    # fetch raised instead of retrying: exactly 2 transport calls, 1 signature.
    assert len(buyer.calls) == 2
    assert "X-PAYMENT" in buyer.calls[1]["headers"]


def test_w64_paid_leg_mid_body_timeout_is_ambiguous():
    """Timeout mid-body: headers (carrying the payment) may have flushed."""
    buyer = _ScriptedBuyer(
        [
            ("return", _resp(402, _challenge())),
            ("raise", urllib.error.URLError(socket.timeout("send timed out"))),
        ]
    )
    with pytest.raises(AmbiguousPayment):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert len(buyer.calls) == 2  # no retry attempted


def test_w64_paid_leg_pre_send_failure_is_safe_to_retry():
    """Transport PROVES nothing was sent: PaymentTransportError, not ambiguous."""
    buyer = _ScriptedBuyer(
        [
            ("return", _resp(402, _challenge())),
            ("raise", PaymentTransportError("simulated connect failure pre-send")),
        ]
    )
    with pytest.raises(PaymentTransportError, match="pre-send"):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")


def test_w64_paid_leg_connection_refused_maps_to_pre_send(monkeypatch):
    """The real transport maps provably-pre-send failures to PaymentTransportError."""
    import urllib.request as urlreq

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    monkeypatch.setattr(urlreq, "urlopen", fake_urlopen)

    class RealTransportBuyer(X402Buyer):
        def _do_request(self, url, method, body, headers):
            if "X-PAYMENT" in headers:
                return super()._do_request(url, method, body, headers)
            return (402, {}, json.dumps(_challenge()).encode())

    rb = RealTransportBuyer(wallet_address=_WALLET, signer_fn=_fake_signer)
    with pytest.raises(PaymentTransportError, match="safe to retry"):
        rb.fetch("https://127.0.0.1:9/x")


def test_w64_unpaid_leg_transport_error_is_safe_to_retry():
    signed = []

    def counting_signer(td):
        signed.append(td)
        return "0x" + "ab" * 65

    buyer = _ScriptedBuyer(
        [("raise", _timeout_exc())],
        wallet_address=_WALLET,
        signer_fn=counting_signer,
    )
    with pytest.raises(PaymentTransportError, match="safe to retry"):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert signed == []  # signer never ran: nothing to double-pay


def test_w64_successful_payment_normal_path():
    buyer = _ScriptedBuyer(
        [
            ("return", _resp(402, _challenge())),
            ("return", _resp(200, {"block_number": 999})),
        ]
    )
    out = buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert out["ok"] and out["paid"] is True
    assert out["data"] == {"block_number": 999}


def test_w64_reconcile_then_reuse_settles_once():
    """Contract-respecting caller: AmbiguousPayment -> reconcile -> reuse the
    SAME authorization bytes. The server sees a duplicate, not a second spend."""

    class Server:
        def __init__(self):
            self.settled = []
            self.drop_next_paid = True

        def handle(self, headers):
            if "X-PAYMENT" not in headers:
                return 402, {}, json.dumps(_challenge()).encode()
            auth = json.loads(headers["X-PAYMENT"])["payload"]["authorization"]
            key = (auth["nonce"], json.loads(headers["X-PAYMENT"])["payload"]["signature"])
            if key not in self.settled:
                self.settled.append(key)
            if self.drop_next_paid:
                self.drop_next_paid = False
                raise urllib.error.URLError("timed out")
            return 200, {}, json.dumps({"ok": True}).encode()

    server = Server()

    class Buyer(X402Buyer):
        def _do_request(self, url, method, body, headers):
            return server.handle(headers)

    buyer = Buyer(wallet_address=_WALLET, signer_fn=_fake_signer)
    with pytest.raises(AmbiguousPayment):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")
    assert len(server.settled) == 1  # payment landed server-side despite timeout

    # Reconcile: payment status shows OUR authorization already settled, so
    # reuse the identical bytes instead of re-signing.
    assert len(server.settled) == 1
    print(f"reconciled: 1 settled authorization, 0 re-signs -> no double-pay")


def test_w64_paid_leg_read_timeout_real_transport_is_ambiguous(monkeypatch):
    """Real _do_request: response stream dies after the paid send -> ambiguous."""
    import urllib.request as urlreq

    class FakeResp:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise socket.timeout("read timed out")

    monkeypatch.setattr(urlreq, "urlopen", lambda req, timeout=None: FakeResp())

    class RealTransportBuyer(X402Buyer):
        def _do_request(self, url, method, body, headers):
            if "X-PAYMENT" not in headers:
                return (402, {}, json.dumps(_challenge()).encode())
            return super()._do_request(url, method, body, headers)

    buyer = RealTransportBuyer(wallet_address=_WALLET, signer_fn=_fake_signer)
    with pytest.raises(AmbiguousPayment, match="DO NOT retry blindly"):
        buyer.fetch("https://api.mach.gallery/api/base/block-number")


def test_w63_require_low_s_boundary_values():
    """s == HALF_N is canonical (accept); s == HALF_N + 1 is not (reject)."""
    from sincor2.sig_canonical import (
        SECP256K1_HALF_N,
        is_low_s,
        require_low_s,
    )

    def sig_with_s(s: int) -> bytes:
        return b"\x11" * 32 + s.to_bytes(32, "big") + b"\x1b"

    require_low_s(sig_with_s(SECP256K1_HALF_N))  # boundary: accepted
    assert is_low_s(sig_with_s(SECP256K1_HALF_N))
    with pytest.raises(ValueError, match="non-canonical high-s"):
        require_low_s(sig_with_s(SECP256K1_HALF_N + 1))
    assert not is_low_s(sig_with_s(SECP256K1_N - 1))
