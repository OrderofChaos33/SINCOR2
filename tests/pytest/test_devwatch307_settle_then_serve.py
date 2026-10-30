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
        self.head = 112  # receipt at block 100 -> 13 confirmations

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
