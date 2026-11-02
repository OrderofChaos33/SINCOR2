"""Dev-watch item 307 — settle-then-serve invariant (x402-express hedge).

The x402-express 1.2.0 flaw had two halves: (1) the middleware served
content BEFORE settlement completed, so a settlement that failed
mid-flow still delivered the goods; (2) parameterized routes skipped
payment enforcement entirely, so whole route shapes were ungated.

This file pins both halves against OUR surface:

(a) Mid-flow settlement failure -> zero content + HTTP 402.
    The transfer can be visible on-chain while settlement still
    refuses: the tx reverted (``tx_failed``), or the #547 finality
    gate refuses below 12 confirmations (``awaiting_finality``). On
    every settle/verify surface the answer must be 402, no access
    token may be minted, no fulfillment/intake may be recorded, and
    no delivery URL may appear anywhere in the response.

(b) Route-shape matrix. Every route that serves paid content or
    delivery — static (``/rail/settle``), :param-style
    (``/api/paid/<resource_id>``, ``/download/<intake_id>``,
    ``/files/guides/<filename>``) and wildcard
    (``/guides/<path:_slug>``, ``/static/<path:filename>``) — is
    asserted payment-gated for an unpaid caller. An enumeration guard
    over the production URL map forces any FUTURE route whose path
    smells like paid content/delivery to join the matrix (or be
    listed as a reviewed exception), so a new route shape cannot
    skip the gate silently — the exact x402-express failure mode.

The chain is a canned ``_rpc_call`` stub (no network, no keys), the
same seam the D2 settle-integrity tests use.
"""
from __future__ import annotations

import sqlite3

import pytest
from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

import sincor2.platform_payments as pp
import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

# Well-known PUBLIC Hardhat dev account #0 — test-only, never funded.
_PAYER_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
PAYER = _PAYER_ACCT.address

TX_OK = "0x" + "a1" * 32
TX_REVERTED = "0x" + "b2" * 32
TX_LOWCONF = "0x" + "c3" * 32

SKILL_RESOURCE = "agent_task"
PACK_RESOURCE = "rail-pack-credentialing_launch"
PACK_SKU = "credentialing_launch"


def _pad(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


class _FakeChain:
    """tx_hash -> canned receipt; mutable head for finality control.

    ``register`` records a successful transfer; ``register_reverted``
    records a mined-but-reverted tx (status 0x0) — the mid-flow
    settlement failure: the payment transaction exists on-chain yet
    settled nothing.
    """

    def __init__(self):
        self.transfers: dict[str, dict] = {}
        self.raw: dict[str, object] = {}
        self.head = 112  # receipt at block 100 -> 13 confirmations

    def register_raw(self, tx_hash, receipt):
        """Serve ``receipt`` verbatim for this tx (malformed shapes)."""
        self.raw[tx_hash.lower()] = receipt

    def register(self, tx_hash, payer, amount_atomic, token="USDC",
                 block=100):
        self.transfers[tx_hash.lower()] = {
            "payer": payer, "amount": amount_atomic,
            "token_addr": pp.token_address(token), "block": block,
            "status": "0x1",
        }

    def register_reverted(self, tx_hash, block=100):
        self.transfers[tx_hash.lower()] = {
            "payer": PAYER, "amount": 0,
            "token_addr": pp.token_address("USDC"), "block": block,
            "status": "0x0",
        }

    def rpc(self, method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            tx = params[0].lower()
            if tx in self.raw:
                return self.raw[tx]
            t = self.transfers.get(tx)
            if t is None:
                return None
            return {
                "status": t["status"],
                "blockNumber": hex(t["block"]),
                "logs": [{
                    "address": t["token_addr"],
                    "topics": [pp.TRANSFER_TOPIC, _pad(t["payer"]),
                               _pad(pp.TREASURY)],
                    "data": hex(t["amount"]),
                }] if t["status"] == "0x1" else [],
            }
        if method == "eth_blockNumber":
            return hex(self.head)
        raise AssertionError(f"unexpected RPC method {method}")


def _bind(challenge_id, resource_id, acct=_PAYER_ACCT):
    sig = acct.sign_message(_encode_defunct(
        text=x402.challenge_bind_message(
            challenge_id, resource_id, acct.address))).signature.hex()
    bound = x402.bind_challenge_payer(challenge_id, acct.address, sig)
    assert bound.get("ownership_proven") is True, bound
    return bound


def _challenge_status(challenge_id: str) -> str:
    with x402._conn() as conn:
        row = conn.execute(
            "SELECT status FROM x402_challenges WHERE challenge_id=?",
            (challenge_id,)).fetchone()
    return row["status"] if row else ""


def _assert_no_content(body, raw_text=""):
    """Zero-content assertion: no token, no delivery, no execution."""
    assert "access_token" not in body, body
    assert "download_url" not in body, body
    assert "fulfillment" not in body, body
    assert "execution" not in body, body
    assert "download_url" not in raw_text
    assert body.get("ok") is not True, body


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "sincor2.treasury_inflow._LEDGER_PATH", tmp_path / "inflow.jsonl")
    monkeypatch.setattr("sincor2.agent_billing._log_path",
                        lambda: tmp_path / "platform_payments.jsonl")
    # Hermetic OFAC verdict (the finality-refused cases never reach
    # the screen; the happy path in test (a) does not run here).
    monkeypatch.setattr(
        pp, "screen_payer_sanctions",
        lambda w: {"verdict": "pass", "detail": "clean"})
    pp._ofac_oracle = None
    pp._ofac_oracle_error_at = None
    chain = _FakeChain()
    monkeypatch.setattr(pp, "_rpc_call", chain.rpc)
    x402.init_x402_db()
    pp.init_platform_payments_db()
    yield {"tmp": tmp_path, "chain": chain}
    pp._ofac_oracle = None
    pp._ofac_oracle_error_at = None


@pytest.fixture()
def billing_client():
    """Minimal app with ONLY the mvp billing blueprint (probe pattern:
    sincor2.mvp_app is imported first so billing._bind_mvp() binds)."""
    import sincor2.mvp_app  # noqa: F401  (import order matters)
    from flask import Flask

    from sincor2.mvp_blueprints import billing as billing_mod

    app = Flask("devwatch307_billing")
    app.register_blueprint(billing_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture()
def rail_client():
    from flask import Flask

    import sincor2.rail_routes as rr

    app = Flask("devwatch307_rail")
    app.config["TESTING"] = True
    rr.init_rail_routes(app)
    return app.test_client()


def _intake_count(tmp_path) -> int:
    conn = sqlite3.connect(str(tmp_path / "revenue.db"))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM pack_intakes").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (a) Settlement fails mid-flow -> zero content + 402
# ---------------------------------------------------------------------------

class TestMidFlowSettlementFailure:
    def test_rail_settle_reverted_tx_serves_nothing(self, env, rail_client):
        """The tx is mined but REVERTED: settlement failed mid-flow.
        /rail/settle must answer 402, record no intake, and leave the
        challenge unfulfilled."""
        chain = env["chain"]
        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        chain.register_reverted(TX_REVERTED)

        res = rail_client.post("/rail/settle", json={
            "challenge_id": cid, "tx_hash": TX_REVERTED,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com"})
        body = res.get_json()
        assert res.status_code == 402, (res.status_code, body)
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_rail_settle_below_finality_serves_nothing(self, env,
                                                        rail_client):
        """The transfer is real but below the 12-confirmation gate
        (#547): settlement refuses mid-flow -> 402 + zero content."""
        chain = env["chain"]
        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        amount = int(x402.get_resource(PACK_RESOURCE)["amount_atomic"])
        chain.register(TX_LOWCONF, PAYER, amount)
        chain.head = 103  # 4 confirmations — below the gate

        res = rail_client.post("/rail/settle", json={
            "challenge_id": cid, "tx_hash": TX_LOWCONF,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com"})
        body = res.get_json()
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "awaiting_finality", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_x402_verify_reverted_tx_is_402_and_mints_no_token(
            self, env, billing_client):
        """Same reverted settlement presented to POST /api/x402/verify:
        the x402 answer to an unsettled payment is 402 (not a generic
        client error), and no access token is minted."""
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        env["chain"].register_reverted(TX_REVERTED)

        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_REVERTED,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "tx_failed", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

        # And the serve surface itself stays shut without a token.
        res = billing_client.get(f"/api/paid/{SKILL_RESOURCE}")
        assert res.status_code == 402, res.status_code
        _assert_no_content(res.get_json() or {},
                           res.get_data(as_text=True))

    def test_x402_verify_below_finality_is_402_with_payment_envelope(
            self, env, billing_client):
        """A rail-pack challenge verified through /api/x402/verify
        while below finality: 402 + the payment envelope a buyer
        needs to retry (accepts / PAYMENT-REQUIRED), zero content."""
        cid = x402.create_challenge(PACK_RESOURCE)["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        amount = int(x402.get_resource(PACK_RESOURCE)["amount_atomic"])
        env["chain"].register(TX_LOWCONF, PAYER, amount)
        env["chain"].head = 103  # 4 confirmations

        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_LOWCONF,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "awaiting_finality", body
        _assert_no_content(body, res.get_data(as_text=True))
        # The 402 must carry the retry terms (paid-surface rule R2).
        assert body.get("accepts") or res.headers.get("PAYMENT-REQUIRED"), (
            "402 without payment envelope", body,
            res.headers.get("PAYMENT-REQUIRED"))
        assert _challenge_status(cid) == "pending"

    def test_settled_payment_still_serves(self, env, billing_client,
                                          monkeypatch):
        """Control: once settlement HAS confirmed, the same surfaces
        serve — the invariant blocks unsettled payments, not serving.
        The transfer seam is stubbed 'settled' exactly the way
        verify_challenge consumes it (receipt-level confirmation)."""
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        monkeypatch.setattr(
            x402, "verify_treasury_transfer",
            lambda tx_hash, **kw: {
                "ok": True, "payer_wallet": PAYER,
                "amount_atomic": 1000, "amount_display": 0.01,
                "token": "SINC", "confirmations": 13,
                "required_confirmations": 12})

        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert res.status_code == 200, (res.status_code, body)
        token = body.get("access_token")
        assert token, body

        res = billing_client.get(
            f"/api/paid/{SKILL_RESOURCE}",
            headers={"X-Payment-Token": token})
        assert res.status_code == 200, (
            res.status_code, res.get_data(as_text=True)[:300])


# ---------------------------------------------------------------------------
# (b) Route-shape matrix: every paid-content route is payment-gated
# ---------------------------------------------------------------------------

#: (shape, method, path, kwargs, expected statuses, forbidden body keys)
#: An unpaid caller must never receive paid content from any shape.
MATRIX = [
    # :param-style — the x402-express blind spot
    ("param", "GET", "/api/paid/agent_task", {}, (402,), ("execution",)),
    ("param", "POST", "/api/paid/agent_task", {"json": {}}, (402,),
     ("execution",)),
    ("param", "GET", "/x402/agent_task", {}, (402,), ("execution",)),
    ("param", "GET", "/download/devwatch307-no-such-intake", {}, (404,),
     ()),
    ("param", "GET",
     "/download/devwatch307-no-such-intake?token=wrong-token", {},
     (404,), ()),
    ("param", "GET", "/files/guides/sincor-starter-guide-ORDER1.pdf",
     {}, (401, 403), ()),
    # static settle/verify surfaces
    ("static", "POST", "/rail/settle",
     {"json": {"challenge_id": "no-such-challenge",
               "tx_hash": TX_OK, "payer_wallet": PAYER}},
     (402,), ("download_url", "fulfillment")),
    ("static", "POST", "/api/x402/verify",
     {"json": {"challenge_id": "no-such-challenge",
               "tx_hash": TX_OK, "payer_wallet": PAYER}},
     (400, 402), ("access_token",)),
    ("static", "POST", "/api/crypto/verify-payment",
     {"json": {"payment_id": "p1", "tx_hash": TX_OK,
               "email": "buyer@example.com", "amount": 10}},
     (402, 503), ("order_id",)),
    # wildcard — must not become a content side-channel
    ("wildcard", "GET", "/guides/some/retired/deep/link", {}, (301, 302),
     ()),
    ("wildcard", "GET",
     "/static/files/guides/sincor-starter-guide-ORDER1.pdf", {},
     (404,), ()),
]

#: Paid-content markers: any production route whose path contains one
#: of these must appear in MATRIX_RULES below (or in EXCEPTIONS with a
#: reason). This is the by-construction half of the hedge: a new paid
#: route shape fails CI until it is classified here.
_PAID_PATH_MARKERS = ("/paid/", "download", "guides", "settle",
                     "verify-payment")

MATRIX_RULES = {
    "/api/paid/<resource_id>",
    "/x402/<resource_id>",
    "/download/<intake_id>",
    "/files/guides/<filename>",
    "/rail/settle",
    "/api/x402/verify",
    "/api/crypto/verify-payment",
    "/guides/<path:_slug>",
    "/static/<path:filename>",
}

#: Reviewed non-content matches: the path matches a marker but the
#: route moves no paid content/delivery.
_EXCEPTION_RULES = {
    # A2A auction settle (internal AXM ledger lifecycle) — settles
    # tasks, serves no paid content; gated by the auction state
    # machine, audited by the money-path wireup audit.
    "/api/a2a/settle",
    # Underwriting mandate settle — an internal risk-lifecycle action
    # on an underwriting envelope record (engine.settle raises
    # PermissionError/ValueError on unauthorized or invalid settles);
    # it releases no paid content or delivery.
    "/v1/mandates/<envelope_id>/settle",
    # Marketing/docs pages: no paid payload (the paid guide PDFs live
    # under /files/guides/<filename>, in the matrix above).
    "/guides",
}


@pytest.fixture(scope="module")
def prod_client():
    """The production app object, exactly as railway_start loads it."""
    from sincor2.mvp_app import app

    app.config.update(TESTING=True)
    return app.test_client()


class TestRouteShapeMatrix:
    @pytest.mark.parametrize(
        "shape,method,path,kwargs,statuses,forbidden",
        MATRIX,
        ids=[f"{m[0]}:{m[1]}:{m[2]}" for m in MATRIX],
    )
    def test_unpaid_caller_gets_no_paid_content(
            self, prod_client, shape, method, path, kwargs, statuses,
            forbidden):
        resp = prod_client.open(path, method=method, **kwargs)
        assert resp.status_code in statuses, (
            shape, method, path, resp.status_code,
            resp.get_data(as_text=True)[:300])
        body = resp.get_json(silent=True) or {}
        for key in forbidden:
            assert key not in body, (shape, path, key, body)
        # No PDF bytes from any shape.
        assert resp.headers.get("Content-Type", "").find(
            "application/pdf") == -1, (shape, path)

    def test_every_paid_route_shape_is_in_the_matrix(self, prod_client):
        from sincor2.mvp_app import app

        all_rules = {rule.rule for rule in app.url_map.iter_rules()}
        found = {
            rule for rule in all_rules
            if any(marker in rule for marker in _PAID_PATH_MARKERS)
        }
        unclassified = found - MATRIX_RULES - _EXCEPTION_RULES
        assert not unclassified, (
            "paid-content route(s) missing from the settle-then-serve "
            f"matrix: {sorted(unclassified)} — add a gate assertion "
            "to MATRIX or a reviewed exception")
        # And every route the matrix claims to cover must still exist.
        assert MATRIX_RULES <= all_rules, (
            "matrix lists route(s) no longer in the URL map: "
            f"{sorted(MATRIX_RULES - all_rules)}")


# ---------------------------------------------------------------------------
# (c) Malformed receipts / raising verifiers -> 402, never 500 HTML / 400.
#
# Builder B round-2 break: the settle-before-serve ORDERING held (zero
# content leaked), but a class of mid-flow verification failures escaped
# with the wrong response SHAPE — 500 HTML (or a 400 where the sibling
# surface answers 402) — because verify_treasury_transfer only guarded
# its RPC calls while _parse_transfer_logs consumed the receipt with no
# shape validation, and neither settle route guarded the verify call.
# The invariant's response-shape half: on EVERY settle/verify surface,
# a verification that cannot complete means "payment not settled" —
# HTTP 402 with the endpoint's JSON error envelope, no token minted,
# no intake recorded, challenge still pending.
# ---------------------------------------------------------------------------

def _transfer_log(data="0x1", topic0=None, token="USDC"):
    return {
        "address": pp.token_address(token),
        "topics": [topic0 if topic0 is not None else pp.TRANSFER_TOPIC,
                   _pad(PAYER), _pad(pp.TREASURY)],
        "data": data,
    }


def _receipt(logs, status="0x1", block=100):
    return {"status": status, "blockNumber": hex(block), "logs": logs}


def _malformed_receipts(token):
    """(shape id, canned receipt) — the transfer log must carry the
    surface's own token address, or the parser legitimately skips it
    before ever reaching the malformed field (skill resources settle
    in SINC, rail packs in USDC)."""
    return [
        ("log_data_not_hex",
         _receipt([_transfer_log(data="nothex", token=token)])),
        ("topic_wrong_type",
         _receipt([_transfer_log(topic0=123, token=token)])),
        ("receipt_is_a_list", ["not", "a", "receipt", "object"]),
    ]


MALFORMED_RECEIPTS = _malformed_receipts("USDC")
MALFORMED_RECEIPTS_SINC = _malformed_receipts("SINC")


class TestMalformedReceiptResponseShape:
    @pytest.mark.parametrize(
        "shape,receipt", MALFORMED_RECEIPTS_SINC,
        ids=[m[0] for m in MALFORMED_RECEIPTS_SINC])
    def test_x402_verify_malformed_receipt_is_402_not_500(
            self, env, billing_client, shape, receipt):
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        env["chain"].register_raw(TX_OK, receipt)

        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert body is not None, (shape, res.get_data(as_text=True)[:200])
        assert res.status_code == 402, (shape, res.status_code, body)
        assert body.get("error") == "receipt_malformed", (shape, body)
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

    @pytest.mark.parametrize(
        "shape,receipt", MALFORMED_RECEIPTS,
        ids=[m[0] for m in MALFORMED_RECEIPTS])
    def test_rail_settle_malformed_receipt_is_402_not_500(
            self, env, rail_client, shape, receipt):
        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        env["chain"].register_raw(TX_OK, receipt)

        res = rail_client.post("/rail/settle", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com"})
        body = res.get_json()
        assert body is not None, (shape, res.get_data(as_text=True)[:200])
        assert res.status_code == 402, (shape, res.status_code, body)
        assert body.get("error") == "receipt_malformed", (shape, body)
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_x402_verify_raising_verify_challenge_is_402(
            self, env, billing_client, monkeypatch):
        """Even if verify_challenge itself raises, the route answers
        402 (payment not settled) — never Flask's 500 HTML page."""
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)

        def _boom(*a, **kw):
            raise RuntimeError("verify exploded")

        monkeypatch.setattr(x402, "verify_challenge", _boom)
        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("ok") is False, body
        assert body.get("error") == "rpc_failed", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

    def test_rail_settle_raising_verify_challenge_is_402(
            self, env, rail_client, monkeypatch):
        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)

        def _boom(*a, **kw):
            raise RuntimeError("verify exploded")

        monkeypatch.setattr(x402, "verify_challenge", _boom)
        res = rail_client.post("/rail/settle", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com"})
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("ok") is False, body
        assert body.get("error") == "rpc_failed", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_rpc_failure_agrees_402_on_both_surfaces(
            self, env, billing_client, rail_client, monkeypatch):
        """_rpc_call raising -> verify reports rpc_failed. Both settle
        surfaces must answer 402 for it (pre-fix drift: billing 400,
        rail 402 — the disagreement this item exists to eliminate)."""
        def _boom(method, params, attempts=3):
            raise RuntimeError("rpc down")

        monkeypatch.setattr(pp, "_rpc_call", _boom)

        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "rpc_failed", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        res = rail_client.post("/rail/settle", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com"})
        body = res.get_json()
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "rpc_failed", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_platform_verify_raising_verifier_is_402_not_500(
            self, env, billing_client, monkeypatch):
        """POST /api/platform/verify: a raising checkout verifier must
        surface as 402 + the JSON envelope, not 500 HTML (same class,
        corroborated by Builder B on the third surface)."""
        import sincor2.mvp_blueprints.billing as billing_mod

        def _boom(*a, **kw):
            raise RuntimeError("verify exploded")

        monkeypatch.setattr(
            billing_mod, "platform_verify_checkout", _boom)
        res = billing_client.post("/api/platform/verify", json={
            "payment_id": "pay_devwatch307", "tx_hash": TX_OK,
            "payer_wallet": PAYER,
            "customer_email": "buyer@example.com"})
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("ok") is False, body
        _assert_no_content(body, res.get_data(as_text=True))


class TestVerifyTreasuryTransferTotality:
    """Unit pins for fix requirement 1: verify_treasury_transfer (and
    its log parser) return a failure RESULT for every malformed
    receipt shape — they never raise on receipt-shaped input."""

    @pytest.mark.parametrize(
        "shape,receipt", MALFORMED_RECEIPTS,
        ids=[m[0] for m in MALFORMED_RECEIPTS])
    def test_malformed_receipts_return_receipt_malformed(
            self, env, shape, receipt):
        env["chain"].register_raw(TX_OK, receipt)
        vr = pp.verify_treasury_transfer(
            TX_OK, token="USDC", expected_atomic=1000,
            payer_wallet=PAYER)
        assert vr.get("ok") is False, (shape, vr)
        assert vr.get("error") == "receipt_malformed", (shape, vr)

    @pytest.mark.parametrize("logs", [
        "not-a-list",
        [None, 42, "junk"],
        [{"address": 123, "topics": "nope", "data": None}],
        [{"topics": [pp.TRANSFER_TOPIC], "data": "0x1"}],
    ], ids=["logs_not_list", "log_entries_not_dicts",
            "fields_wrong_types", "log_missing_address"])
    def test_parser_never_raises_on_garbage_logs(self, env, logs):
        env["chain"].register_raw(TX_OK, _receipt(logs))
        vr = pp.verify_treasury_transfer(
            TX_OK, token="USDC", expected_atomic=1000,
            payer_wallet=PAYER)
        # A result, never an exception: either malformed, or no
        # qualifying treasury transfer could be parsed from the junk.
        assert vr.get("ok") is False, vr
        assert vr.get("error") in (
            "receipt_malformed", "no_treasury_transfer"), vr


# ---------------------------------------------------------------------------
# (d) Round 3: the could-not-complete family through the REAL verifier.
#
# Builder B's re-check of 8c5a7c0e (NOT CONFIRMED) found two survivors
# of the exact family round 2 targeted — both one-line mapping
# omissions, both reachable only through the real
# verify_treasury_transfer path (round 2's platform test mocked the
# verifier RAISING; the real verifier converts RPC failures into
# result dicts, it does not raise):
#
#   SHAPE 1 — eth_blockNumber fails -> the verifier returns
#     {error: "confirmations_unavailable"}. /api/x402/verify answered
#     400 while /rail/settle and /api/platform/verify answered 402:
#     the string was missing from _PAID_SURFACE_VERIFY_402_ERRORS.
#   SHAPE 2 — eth_getTransactionReceipt fails on a real W-113-bound
#     platform checkout -> the verifier returns {error: "rpc_failed"}.
#     /api/platform/verify answered 400: its own status mapping had
#     no rpc_failed entry and fell through to the default.
#
# These tests stub ONLY the _rpc_call seam, per-method, and run the
# real verifier end-to-end on all three surfaces — no monkeypatched
# verifier anywhere in this section.
# ---------------------------------------------------------------------------

_USDC_297 = 297_000_000  # starter checkout / rail pack amount (USDC)


def _head_down_rpc(chain):
    """The real chain, except eth_blockNumber raises (head down)."""
    def rpc(method, params, attempts=3):
        if method == "eth_blockNumber":
            raise RuntimeError("head down")
        return chain.rpc(method, params, attempts)
    return rpc


def _receipt_down_rpc(chain):
    """The real chain, except eth_getTransactionReceipt raises."""
    def rpc(method, params, attempts=3):
        if method == "eth_getTransactionReceipt":
            raise RuntimeError("rpc down")
        return chain.rpc(method, params, attempts)
    return rpc


def _platform_sign(acct, payment_id):
    """EIP-191 signature over the platform bind message (W-113)."""
    return acct.sign_message(_encode_defunct(
        text=pp.platform_bind_message(
            payment_id, acct.address))).signature.hex()


def _insert_platform_checkout(payment_id, payer_wallet):
    """Platform 'starter' checkout (USDC $297) bound to payer_wallet
    at creation, mirroring the W-113 regression insert."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    with pp._conn() as conn:
        conn.execute(
            """INSERT INTO platform_checkouts
               (payment_id, plan_id, product_name, token, amount_atomic,
                amount_display, usd_reference, payer_wallet,
                customer_email, status, tx_hash, created_at, expires_at,
                metadata)
               VALUES (?, 'starter', 'Starter', 'USDC', ?, 297.0, 297.0,
                       ?, 'buyer@example.com', 'pending', NULL, ?, ?, ?)""",
            (payment_id, str(_USDC_297), payer_wallet,
             now.isoformat(), (now + timedelta(hours=1)).isoformat(),
             '{"order_type": "subscription", "billing": "month"}'))
        conn.commit()


def _checkout_status(payment_id):
    with pp._conn() as conn:
        row = conn.execute(
            "SELECT status FROM platform_checkouts WHERE payment_id=?",
            (payment_id,)).fetchone()
    return row["status"] if row else ""


def _post_platform_verify(client, payment_id, tx_hash):
    return client.post("/api/platform/verify", json={
        "payment_id": payment_id, "tx_hash": tx_hash,
        "payer_wallet": PAYER, "customer_email": "buyer@example.com",
        "payer_signature": _platform_sign(_PAYER_ACCT, payment_id)})


class TestCouldNotCompleteFamilyRealPath:
    def test_x402_verify_confirmations_unavailable_is_402(
            self, env, billing_client, monkeypatch):
        """SHAPE 1: the receipt is real, but the chain head is
        unreachable while counting confirmations -> the verifier
        returns confirmations_unavailable -> /api/x402/verify must
        answer 402 (pre-fix: 400), mint nothing, challenge pending."""
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        amount = int(x402.get_resource(SKILL_RESOURCE)["amount_atomic"])
        env["chain"].register(TX_OK, PAYER, amount, token="SINC")
        monkeypatch.setattr(
            pp, "_rpc_call", _head_down_rpc(env["chain"]))

        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "confirmations_unavailable", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

    def test_rail_settle_confirmations_unavailable_is_402(
            self, env, rail_client, monkeypatch):
        """SHAPE 1, sibling surface: /rail/settle answers 402 for the
        same head-down failure (agreement control)."""
        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        amount = int(x402.get_resource(PACK_RESOURCE)["amount_atomic"])
        env["chain"].register(TX_OK, PAYER, amount)
        monkeypatch.setattr(
            pp, "_rpc_call", _head_down_rpc(env["chain"]))

        res = rail_client.post("/rail/settle", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER, "buyer_email": "buyer@example.com"})
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "confirmations_unavailable", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_platform_verify_confirmations_unavailable_is_402(
            self, env, billing_client, monkeypatch):
        """SHAPE 1, third surface: a real W-113-bound platform
        checkout whose head lookup fails mid-verify ->
        /api/platform/verify answers 402 confirmations_unavailable
        (agreement control) and the checkout stays pending."""
        _insert_platform_checkout("pay_dw307_r3_head", PAYER)
        env["chain"].register(TX_OK, PAYER, _USDC_297)
        monkeypatch.setattr(
            pp, "_rpc_call", _head_down_rpc(env["chain"]))

        res = _post_platform_verify(
            billing_client, "pay_dw307_r3_head", TX_OK)
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "confirmations_unavailable", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _checkout_status("pay_dw307_r3_head") == "pending"

    def test_platform_verify_receipt_rpc_failure_is_402(
            self, env, billing_client, monkeypatch):
        """SHAPE 2: a real W-113-bound platform checkout (bound payer
        + valid EIP-191 payer signature) whose receipt fetch fails at
        the RPC layer. The real verifier does NOT raise — it returns
        {error: "rpc_failed"} — and /api/platform/verify must answer
        402 (pre-fix: 400), leaving the checkout pending with nothing
        provisioned."""
        _insert_platform_checkout("pay_dw307_r3_rpc", PAYER)
        monkeypatch.setattr(
            pp, "_rpc_call", _receipt_down_rpc(env["chain"]))

        res = _post_platform_verify(
            billing_client, "pay_dw307_r3_rpc", TX_OK)
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("ok") is False, body
        assert body.get("error") == "rpc_failed", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _checkout_status("pay_dw307_r3_rpc") == "pending"


# ---------------------------------------------------------------------------
# (e) Tidy (coordinator-ruled; Builder B round-2 re-check, observation
# 1): claim-step race outcomes agree on 402.
#
# store_busy / fulfill_conflict are NOT verification failures: the
# on-chain transfer verified fine, but the claim step — the atomic
# BEGIN IMMEDIATE transaction that turns a verified transfer into a
# fulfilled challenge — lost a transient store race, and a clean
# retry settles. /rail/settle already answers 402 for both (its
# default for any verify failure outside its client-error list);
# /api/x402/verify answered 400 because neither string was in
# _PAID_SURFACE_VERIFY_402_ERRORS — a 400 tells the buyer "your
# request was wrong" for what is really "payment not settled (yet)".
# The fix adds both strings to that one tuple.
#
# Levels pinned, plainly:
#   * store_busy — REAL PATH, HTTP level: the orders-DB write lock
#     is taken on a side connection from inside the _rpc_call stub,
#     i.e. in the window between verify_challenge's read/verify
#     phase and its claim phase (the technique Builder B used), so
#     the real verify_challenge produces the result end-to-end.
#   * fulfill_conflict — ROUTE-DECISION level only. The claim UPDATE
#     returning rowcount != 1 is not deterministically reachable
#     through the real store: the UPDATE runs inside the same
#     BEGIN IMMEDIATE transaction that just re-read the row, SQLite
#     serializes writers, so the row cannot vanish between that
#     read and the update, and there is no injectable seam between
#     them. The test therefore injects the result at the
#     verify_challenge seam (the seam the round-2 raising-verifier
#     tests use) and pins the route's status DECISION for a
#     fulfill_conflict result — the same one-line mapping fixed.
# ---------------------------------------------------------------------------

class _WriteLockHolder:
    """Holds the orders-DB write lock (BEGIN IMMEDIATE) on a side
    connection, acquired lazily from inside the RPC seam and held
    until release(). Release rolls the empty transaction back."""

    def __init__(self):
        self.conn = None
        self.armed = True

    def acquire(self):
        if self.armed and self.conn is None:
            conn = sqlite3.connect(str(x402._db_path()))
            conn.execute("BEGIN IMMEDIATE")
            self.conn = conn

    def release(self):
        self.armed = False
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def _locking_rpc(chain, holder):
    """The real chain, except the verify's first RPC takes the
    orders-DB write lock, which stays held into the claim phase."""
    def rpc(method, params, attempts=3):
        holder.acquire()
        return chain.rpc(method, params, attempts)
    return rpc


class TestClaimStepRaceOutcomes:
    def test_x402_verify_store_busy_is_402_and_retry_settles(
            self, env, billing_client, monkeypatch):
        """store_busy through the REAL path: the transfer verifies,
        but the claim transaction cannot acquire the store write
        lock -> /api/x402/verify answers 402 store_busy (pre-fix:
        400), mints nothing, challenge stays pending — and once the
        lock clears, the clean retry settles (200 + token)."""
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        amount = int(x402.get_resource(SKILL_RESOURCE)["amount_atomic"])
        env["chain"].register(TX_OK, PAYER, amount, token="SINC")
        holder = _WriteLockHolder()
        monkeypatch.setattr(
            pp, "_rpc_call", _locking_rpc(env["chain"], holder))
        try:
            res = billing_client.post("/api/x402/verify", json={
                "challenge_id": cid, "tx_hash": TX_OK,
                "payer_wallet": PAYER})
        finally:
            holder.release()
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "store_busy", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

        # The lock has cleared: the same verify now settles.
        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert res.status_code == 200, (res.status_code, body)
        assert body.get("access_token"), body
        assert _challenge_status(cid) == "fulfilled"

    def test_rail_settle_store_busy_stays_402(
            self, env, rail_client, monkeypatch):
        """Control: the same store race through /rail/settle already
        answers 402 (rail's default for non-client verify failures)
        — pinned so the two surfaces cannot drift apart again."""
        res = rail_client.post("/rail/checkout", json={
            "pack_sku": PACK_SKU, "payer_wallet": PAYER,
            "buyer_email": "buyer@example.com"})
        assert res.status_code == 402, res.get_json()
        cid = res.get_json()["challenge_id"]
        _bind(cid, PACK_RESOURCE)
        amount = int(x402.get_resource(PACK_RESOURCE)["amount_atomic"])
        env["chain"].register(TX_OK, PAYER, amount)
        holder = _WriteLockHolder()
        monkeypatch.setattr(
            pp, "_rpc_call", _locking_rpc(env["chain"], holder))
        try:
            res = rail_client.post("/rail/settle", json={
                "challenge_id": cid, "tx_hash": TX_OK,
                "payer_wallet": PAYER,
                "buyer_email": "buyer@example.com"})
        finally:
            holder.release()
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("error") == "store_busy", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _intake_count(env["tmp"]) == 0
        assert _challenge_status(cid) == "pending"

    def test_x402_verify_fulfill_conflict_maps_to_402(
            self, env, billing_client, monkeypatch):
        """fulfill_conflict, ROUTE-DECISION pin (see the section (e)
        header for why the real store cannot produce it
        deterministically): a fulfill_conflict result out of
        verify_challenge must be answered 402 by /api/x402/verify
        (pre-fix: 400) — the same transient-race family as
        store_busy, which rail already answers 402."""
        cid = x402.create_challenge(SKILL_RESOURCE)["challenge_id"]
        _bind(cid, SKILL_RESOURCE)
        monkeypatch.setattr(
            x402, "verify_challenge",
            lambda *a, **kw: {"ok": False,
                              "error": "fulfill_conflict"})
        res = billing_client.post("/api/x402/verify", json={
            "challenge_id": cid, "tx_hash": TX_OK,
            "payer_wallet": PAYER})
        body = res.get_json()
        assert body is not None, res.get_data(as_text=True)[:200]
        assert res.status_code == 402, (res.status_code, body)
        assert body.get("ok") is False, body
        assert body.get("error") == "fulfill_conflict", body
        _assert_no_content(body, res.get_data(as_text=True))
        assert _challenge_status(cid) == "pending"

