"""P2 audit — Batch B (Payments & compliance) regression tests.

B1: receipt finality not enforced — orders stay pending below the
    chain-specific confirmation depth; reorgs move orders to
    reverted/disputed with an audit record, never silently fulfilled.
B2: OFAC screening on the payment path — sanctioned senders blocked
    (fail-closed), clean senders pass, screening outage blocks in strict
    mode (default) and is audit-flagged when fail-open is configured.
B3: legacy Stripe webhook event idempotency — insert-or-ignore claim on the
    Stripe event ID before processing; duplicates acked without
    re-fulfillment; loud log when the integration is disabled.
B4: paid vault auth — vault materials served only for orders whose canonical
    record is paid/active; pending/failed/refunded denied.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account as _Account
from eth_account.messages import encode_defunct as _encode_defunct

from sincor2 import platform_payments as pp

# Entries verified present in the vendored OFAC SDN snapshot.
SANCTIONED_EVM_ADDR = "0x252a8bd2319d8a555b872990601221b3a2053bce"  # MESRI, Behzad
# W-113: the clean payer is a signable test account (well-known PUBLIC
# Hardhat dev account #0 — test-only, never funded, not on the SDN
# list): the bound verify flow requires an EIP-191 ownership signature
# from the payer, which an arbitrary address cannot produce.
CLEAN_ACCT = _Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
CLEAN_ADDR = CLEAN_ACCT.address

TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32
TX_C = "0x" + "cc" * 32
TX_D = "0x" + "dd" * 32
TX_E = "0x" + "ee" * 32


# --------------------------------------------------------------------------
# fixtures & helpers
# --------------------------------------------------------------------------

@pytest.fixture()
def pp_db(tmp_path, monkeypatch):
    """Isolated platform-payments DB + fresh OFAC oracle cache per test."""
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "platform_orders.db"))
    monkeypatch.delenv("PLATFORM_OFAC_STRICT", raising=False)
    monkeypatch.delenv("PLATFORM_FINALITY_CONFIRMATIONS_8453", raising=False)
    monkeypatch.delenv("PLATFORM_FINALITY_CONFIRMATIONS_JSON", raising=False)
    pp.init_platform_payments_db()
    pp._ofac_oracle = None
    pp._ofac_oracle_error_at = None
    yield tmp_path
    pp._ofac_oracle = None
    pp._ofac_oracle_error_at = None


def _pad_addr(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:].lower()


def _transfer_log(payer: str, amount_atomic: int = 10**18) -> dict:
    return {
        "address": pp.AXM,
        "topics": [pp.TRANSFER_TOPIC, _pad_addr(payer), _pad_addr(pp.TREASURY)],
        "data": hex(amount_atomic),
    }


def _make_rpc(payer: str, block_num: int, latest_num: int,
              receipt_status: str = "0x1", no_receipt: bool = False):
    """Fake _rpc_call: receipt at block_num, chain head at latest_num."""
    def fake_rpc(method, params):
        if method == "eth_getTransactionReceipt":
            if no_receipt:
                return None
            return {
                "status": receipt_status,
                "blockNumber": hex(block_num),
                "logs": [_transfer_log(payer)],
            }
        if method == "eth_blockNumber":
            return hex(latest_num)
        raise AssertionError(f"unexpected RPC method: {method}")
    return fake_rpc


def _insert_checkout(payment_id: str, tx_hash: str = "", status: str = "pending",
                     token: str = "AXM", payer_wallet: str = "") -> None:
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(hours=1)).isoformat()
    with pp._conn() as conn:
        conn.execute(
            """INSERT INTO platform_checkouts
               (payment_id, plan_id, product_name, token, amount_atomic,
                amount_display, usd_reference, payer_wallet, customer_email,
                status, tx_hash, created_at, expires_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payment_id, "intel", "Professional", token, str(10**18), 1.0,
             149.0, payer_wallet, "buyer@test.com", status, tx_hash or None,
             now.isoformat(), exp,
             '{"order_type": "subscription", "billing": "month"}'),
        )
        conn.commit()


def _platform_sign(payment_id: str, acct=CLEAN_ACCT) -> str:
    """W-113: EIP-191 payer signature over the platform bind message."""
    return acct.sign_message(_encode_defunct(
        text=pp.platform_bind_message(
            payment_id, acct.address))).signature.hex()


def _verify_bound(payment_id: str, tx_hash: str, acct=CLEAN_ACCT):
    """verify_checkout under the W-113 bound flow: the checkout's bound
    payer plus its EIP-191 ownership proof."""
    return pp.verify_checkout(
        payment_id, tx_hash, payer_wallet=acct.address,
        payer_signature=_platform_sign(payment_id, acct))


def _pass_ofac(monkeypatch):
    monkeypatch.setattr(
        pp, "screen_payer_sanctions",
        lambda w: {"verdict": "pass", "detail": "clean"},
    )


def _fresh_ofac_oracle(monkeypatch):
    """Real vendored OFAC oracle with a fresh list timestamp (not STALE)."""
    def build():
        from sincor2.defi.ofac_sdn import build_ofac_oracle
        import time as _time
        oracle = build_ofac_oracle()
        oracle.aml.list_updated_at = _time.time()
        return oracle
    monkeypatch.setattr(pp, "_get_ofac_oracle", build)


# --------------------------------------------------------------------------
# B1: receipt finality
# --------------------------------------------------------------------------

class TestReceiptFinality:
    def test_below_depth_stays_pending(self, pp_db, monkeypatch):
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-1", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 100))
        r = _verify_bound("PLAT-B1-1", TX_A)
        assert r["ok"] is True
        assert r["status"] == "awaiting_finality"
        assert r["confirmations"] == 1
        assert r["required_confirmations"] == 12
        assert pp._load_checkout("PLAT-B1-1")["status"] == "awaiting_finality"

    def test_zero_confirmations_stays_pending(self, pp_db, monkeypatch):
        # Degenerate head-behind-receipt case: 0 confirmations -> pending.
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-0", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 99))
        r = _verify_bound("PLAT-B1-0", TX_A)
        assert r["ok"] is True
        assert r["status"] == "awaiting_finality"
        assert r["confirmations"] == 0

    def test_at_depth_finalizes(self, pp_db, monkeypatch):
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-2", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 111))
        r = _verify_bound("PLAT-B1-2", TX_B)
        assert r["ok"] is True
        assert r["status"] == "finalized"
        assert r["confirmations"] == 12
        assert pp._load_checkout("PLAT-B1-2")["status"] == "finalized"
        assert pp.mark_checkout_fulfilled("PLAT-B1-2") is True
        assert pp._load_checkout("PLAT-B1-2")["status"] == "fulfilled"

    def test_depth_is_configurable_per_chain(self, pp_db, monkeypatch):
        _pass_ofac(monkeypatch)
        monkeypatch.setenv("PLATFORM_FINALITY_CONFIRMATIONS_8453", "2")
        assert pp.finality_confirmations() == 2
        assert pp.finality_confirmations(8453) == 2
        # Unknown chain falls back to the named fallback constant.
        assert pp.finality_confirmations(1) == pp.FINALITY_CONFIRMATIONS_FALLBACK
        _insert_checkout("PLAT-B1-3", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 101))
        r = _verify_bound("PLAT-B1-3", TX_C)
        assert r["ok"] is True and r["status"] == "finalized"
        assert r["required_confirmations"] == 2

    def test_default_depth_is_named_not_magic(self, pp_db):
        assert pp.FINALITY_CONFIRMATIONS_BY_CHAIN[8453] == 12
        assert pp.finality_confirmations() == 12

    def test_treasury_transfer_reports_confirmations(self, pp_db, monkeypatch):
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 105))
        vr = pp.verify_treasury_transfer(
            TX_A, token="AXM", expected_atomic=10**18)
        assert vr["ok"] is True
        assert vr["confirmations"] == 6
        assert vr["required_confirmations"] == 12
        assert vr["receipt_block"] == 100
        assert vr["latest_block"] == 105

    def test_reorg_before_fulfillment_reverts_with_audit(
            self, pp_db, monkeypatch):
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-4", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 100))
        r1 = _verify_bound("PLAT-B1-4", TX_D)
        assert r1["status"] == "awaiting_finality"
        # The tx vanishes from chain: reorged out / dropped.
        monkeypatch.setattr(
            pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 110, no_receipt=True))
        r2 = _verify_bound("PLAT-B1-4", TX_D)
        assert r2["ok"] is False
        assert r2["error"] == "reorg_detected"
        assert r2["status"] == "reverted"
        assert pp._load_checkout("PLAT-B1-4")["status"] == "reverted"
        events = [a["event"] for a in pp.list_payment_audits("PLAT-B1-4")]
        assert "reorg_tx_missing" in events

    def test_deep_reorg_after_fulfillment_disputes(self, pp_db, monkeypatch):
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-5", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 111))
        r = _verify_bound("PLAT-B1-5", TX_E)
        assert r["status"] == "finalized"
        assert pp.mark_checkout_fulfilled("PLAT-B1-5") is True
        # Deep reorg AFTER fulfillment: must never silently stay fulfilled.
        monkeypatch.setattr(
            pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 130, no_receipt=True))
        r2 = pp.recheck_finality("PLAT-B1-5")
        assert r2["status"] == "disputed"
        assert r2["reorg"] is True
        assert pp._load_checkout("PLAT-B1-5")["status"] == "disputed"
        events = [a["event"] for a in pp.list_payment_audits("PLAT-B1-5")]
        assert "deep_reorg_after_fulfillment" in events

    def test_healthy_recheck_keeps_fulfilled(self, pp_db, monkeypatch):
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-6", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 111))
        assert _verify_bound("PLAT-B1-6", TX_A)["status"] == "finalized"
        assert pp.mark_checkout_fulfilled("PLAT-B1-6") is True
        r = pp.recheck_finality("PLAT-B1-6")
        assert r["status"] == "fulfilled" and r["reorg"] is False

    def test_same_tx_cannot_finalize_twice(self, pp_db, monkeypatch):
        # Adversarial: two checkouts claim the same tx. The second must be
        # rejected at finalization — never double-provisioned.
        _pass_ofac(monkeypatch)
        _insert_checkout("PLAT-B1-8A", payer_wallet=CLEAN_ADDR)
        _insert_checkout("PLAT-B1-8B", payer_wallet=CLEAN_ADDR)
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(CLEAN_ADDR, 100, 111))
        r1 = _verify_bound("PLAT-B1-8A", TX_A)
        assert r1["status"] == "finalized"
        r2 = _verify_bound("PLAT-B1-8B", TX_A)
        assert r2["ok"] is False
        assert r2["error"] == "tx_already_used"
        # Loser goes back to pending and can retry with a different tx.
        assert pp._load_checkout("PLAT-B1-8B")["status"] == "pending"

    def test_legacy_completed_row_migrates_to_fulfilled(self, pp_db, monkeypatch):
        _insert_checkout("PLAT-B1-7", tx_hash=TX_B, status="completed")
        r = pp.verify_checkout("PLAT-B1-7", TX_B)
        assert r["ok"] is True
        assert r["status"] == "already_fulfilled"
        assert pp._load_checkout("PLAT-B1-7")["status"] == "fulfilled"


# --------------------------------------------------------------------------
# B2: OFAC screening on the payment path
# --------------------------------------------------------------------------

class TestOfacScreening:
    def _finalize_rpc(self, monkeypatch, payer):
        monkeypatch.setattr(pp, "_rpc_call", _make_rpc(payer, 100, 111))

    def test_sanctioned_sender_blocked(self, pp_db, monkeypatch):
        _fresh_ofac_oracle(monkeypatch)
        # W-113: no private key exists for a sanctioned address, so no
        # verify-time ownership signature can ever be produced for it;
        # the screen is exercised on the bound recheck leg (the same
        # _maybe_finalize OFAC screen) with the sanctioned wallet bound
        # to an awaiting_finality checkout.
        _insert_checkout("PLAT-B2-1", tx_hash=TX_A,
                         status="awaiting_finality",
                         payer_wallet=SANCTIONED_EVM_ADDR)
        self._finalize_rpc(monkeypatch, SANCTIONED_EVM_ADDR)
        r = pp.recheck_finality("PLAT-B2-1")
        assert r["ok"] is False
        assert r["error"] == "sanctions_hit"
        assert r["status"] == "blocked"
        assert pp._load_checkout("PLAT-B2-1")["status"] == "blocked"
        events = [a["event"] for a in pp.list_payment_audits("PLAT-B2-1")]
        assert "ofac_hit" in events

    def test_sanctioned_sender_blocked_case_insensitive(
            self, pp_db, monkeypatch):
        # Uppercase sender address must still hit the (lowercased) list.
        _fresh_ofac_oracle(monkeypatch)
        _insert_checkout("PLAT-B2-1B", tx_hash=TX_B,
                         status="awaiting_finality",
                         payer_wallet=SANCTIONED_EVM_ADDR)
        self._finalize_rpc(monkeypatch, SANCTIONED_EVM_ADDR.upper())
        r = pp.recheck_finality("PLAT-B2-1B")
        assert r["ok"] is False and r["error"] == "sanctions_hit"

    def test_clean_sender_passes(self, pp_db, monkeypatch):
        _fresh_ofac_oracle(monkeypatch)
        _insert_checkout("PLAT-B2-2", payer_wallet=CLEAN_ADDR)
        self._finalize_rpc(monkeypatch, CLEAN_ADDR)
        r = _verify_bound("PLAT-B2-2", TX_C)
        assert r["ok"] is True
        assert r["status"] == "finalized"
        assert r["ofac_verdict"] == "pass"

    def test_screening_unavailable_strict_blocks(self, pp_db, monkeypatch):
        monkeypatch.setattr(
            pp, "_get_ofac_oracle",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated outage")),
        )
        _insert_checkout("PLAT-B2-3", payer_wallet=CLEAN_ADDR)
        self._finalize_rpc(monkeypatch, CLEAN_ADDR)
        r = _verify_bound("PLAT-B2-3", TX_D)
        assert r["ok"] is False
        assert r["error"] == "ofac_screening_unavailable"
        assert r["status"] == "blocked"
        assert pp.ofac_strict_mode() is True  # default is fail-closed
        events = [a["event"] for a in pp.list_payment_audits("PLAT-B2-3")]
        assert "ofac_unavailable_blocked" in events

    def test_screening_unavailable_non_strict_failopen_with_audit(
            self, pp_db, monkeypatch):
        monkeypatch.setenv("PLATFORM_OFAC_STRICT", "false")
        assert pp.ofac_strict_mode() is False
        monkeypatch.setattr(
            pp, "_get_ofac_oracle",
            lambda: (_ for _ in ()).throw(RuntimeError("simulated outage")),
        )
        _insert_checkout("PLAT-B2-4", payer_wallet=CLEAN_ADDR)
        self._finalize_rpc(monkeypatch, CLEAN_ADDR)
        r = _verify_bound("PLAT-B2-4", TX_E)
        assert r["ok"] is True
        assert r["status"] == "finalized"
        events = [a["event"] for a in pp.list_payment_audits("PLAT-B2-4")]
        assert "ofac_unavailable_failopen" in events

    def test_stale_list_treated_as_unavailable(self, pp_db, monkeypatch):
        # Force the oracle's list age past the 48h fail-closed threshold so
        # the test is deterministic regardless of the vendored snapshot's
        # real age; strict mode must fail closed, never trust it.
        _real_get_oracle = pp._get_ofac_oracle

        def _stale_oracle():
            oracle = _real_get_oracle()
            oracle.aml.list_updated_at = (
                datetime.now(timezone.utc) - timedelta(hours=49)
            ).timestamp()
            return oracle

        monkeypatch.setattr(pp, "_get_ofac_oracle", _stale_oracle)
        _insert_checkout("PLAT-B2-5", payer_wallet=CLEAN_ADDR)
        self._finalize_rpc(monkeypatch, CLEAN_ADDR)
        r = _verify_bound("PLAT-B2-5", TX_A)
        assert r["ok"] is False
        assert r["error"] == "ofac_screening_unavailable"


# --------------------------------------------------------------------------
# B3 + B4: Flask app (mvp) tests
# --------------------------------------------------------------------------

def _mvp_client(tmp_path, monkeypatch):
    """Isolated mvp app test client: fresh orders DB + guides dir."""
    import sincor2.mvp_app as mvp
    import sincor2.mvp_blueprints.billing as billing

    monkeypatch.setattr(mvp, "DB_PATH", str(tmp_path / "orders.db"))
    mvp.init_db()
    guides = tmp_path / "guides"
    guides.mkdir()
    monkeypatch.setattr(billing, "pdf_guides_dir", str(guides))
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "platform.db"))
    pp.init_platform_payments_db()
    mvp.app.config.update(TESTING=True)
    return mvp.app.test_client(), billing, mvp


class _FakeStripeProcessor:
    enabled = True

    def __init__(self, event_data):
        self._event_data = dict(event_data)

    def verify_webhook(self, payload, sig_header):
        return True, dict(self._event_data)


def _stripe_event(event_id, email="buyer@test.com", cents=29700,
                  session_id="cs_test_1"):
    return {
        "event": "payment_completed",
        "event_id": event_id,
        "customer_email": email,
        "amount_total": cents,
        "session_id": session_id,
        "subscription_id": "",
    }


def _insert_order(mvp, order_id, email, payment_status, product="Starter"):
    db = mvp.get_db()
    # get_db needs an app context; callers run inside test_request_context.
    db.execute(
        """INSERT INTO orders (order_id, customer_email, product_name, amount,
           currency, payment_status, delivery_status, order_type, created_at,
           updated_at)
           VALUES (?, ?, ?, 297, 'USD', ?, 'delivered', 'subscription', ?, ?)""",
        (order_id, email, product, payment_status,
         datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


@pytest.fixture()
def mvp_env(tmp_path, monkeypatch):
    client, billing, mvp = _mvp_client(tmp_path, monkeypatch)
    monkeypatch.setenv("LEGACY_FIAT_PAYMENTS_ENABLED", "true")
    fulfilled = []
    monkeypatch.setattr(
        billing, "trigger_fulfillment",
        lambda *a, **k: fulfilled.append(a[0]),
    )
    return client, billing, mvp, fulfilled


class TestStripeEventIdempotency:
    @staticmethod
    def _post(client):
        return client.post(
            "/api/payment/webhook",
            data=b"{}",
            headers={"Stripe-Signature": "t=1,v1=abc"},
            content_type="application/json",
        )

    def test_same_event_twice_fulfills_once(self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        monkeypatch.setattr(
            billing, "stripe_processor",
            _FakeStripeProcessor(_stripe_event("evt_dup_1")),
        )
        r1 = self._post(client)
        r2 = self._post(client)
        assert r1.status_code == 200, r1.data
        assert r2.status_code == 200, r2.data
        assert r2.get_json().get("duplicate") is True
        assert len(fulfilled) == 1

    def test_concurrent_duplicate_exactly_once(self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        monkeypatch.setattr(
            billing, "stripe_processor",
            _FakeStripeProcessor(_stripe_event("evt_race_1")),
        )
        errors, codes = [], []

        def fire():
            try:
                c = mvp.app.test_client()
                r = c.post(
                    "/api/payment/webhook",
                    data=b"{}",
                    headers={"Stripe-Signature": "t=1,v1=abc"},
                    content_type="application/json",
                )
                codes.append(r.status_code)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=fire) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, errors
        assert codes and all(c == 200 for c in codes), codes
        assert len(fulfilled) == 1, fulfilled

    def test_distinct_events_each_fulfill(self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        for eid in ("evt_a_1", "evt_a_2"):
            monkeypatch.setattr(
                billing, "stripe_processor",
                _FakeStripeProcessor(_stripe_event(eid)),
            )
            r = self._post(client)
            assert r.status_code == 200, r.data
        assert len(fulfilled) == 2

    def test_event_while_disabled_rejected_with_loud_log(
            self, mvp_env, monkeypatch, caplog):
        client, billing, mvp, fulfilled = mvp_env
        monkeypatch.setenv("LEGACY_FIAT_PAYMENTS_ENABLED", "false")
        with caplog.at_level(logging.ERROR):
            r = client.post(
                "/api/payment/webhook",
                data=b"{}",
                headers={"Stripe-Signature": "t=1,v1=abc"},
                content_type="application/json",
            )
        assert r.status_code == 410
        assert not fulfilled
        assert any("DISABLED" in rec.message for rec in caplog.records), \
            [rec.message for rec in caplog.records]

    def test_missing_event_id_refused(self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        ed = _stripe_event("evt_x")
        del ed["event_id"]
        monkeypatch.setattr(
            billing, "stripe_processor", _FakeStripeProcessor(ed))
        r = self._post(client)
        assert r.status_code == 400
        assert not fulfilled


class TestPlatformVerifyFinalityGate:
    """B1 at the HTTP layer: no provisioning below the depth gate."""

    def _finalized_payload(self, status="finalized"):
        return {
            "ok": True,
            "status": status,
            "payment_id": "PLAT-HTTP-1",
            "order_id": "TOKEN-ORD-PLAT-HTTP-1",
            "tx_hash": TX_A,
            "payer_wallet": CLEAN_ADDR,
            "product_name": "Professional",
            "plan_id": "intel",
            "token": "AXM",
            "amount_atomic": str(10**18),
            "amount_display": 1.0,
            "usd_reference": 149.0,
            "order_type": "subscription",
            "billing": "month",
            "customer_email": "buyer@test.com",
            "confirmations": 12,
            "required_confirmations": 12,
            "ofac_verdict": "pass",
        }

    def test_awaiting_finality_returns_202_no_provision(
            self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        payload = self._finalized_payload(status="awaiting_finality")
        monkeypatch.setattr(
            billing, "platform_verify_checkout", lambda *a, **k: dict(payload))
        r = client.post("/api/platform/verify",
                        json={"payment_id": "PLAT-HTTP-1", "tx_hash": TX_A})
        assert r.status_code == 202, r.data
        body = r.get_json()
        assert body["status"] == "awaiting_finality"
        assert body["confirmations"] == 12
        assert not fulfilled  # nothing provisioned below the depth gate
        with mvp.app.test_request_context():
            row = mvp.get_db().execute(
                "SELECT payment_status FROM orders WHERE order_id=?",
                ("TOKEN-ORD-PLAT-HTTP-1",)).fetchone()
        assert row is not None and row["payment_status"] == "pending"

    def test_finalized_provisions_exactly_once(self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        payload = self._finalized_payload()
        monkeypatch.setattr(
            billing, "platform_verify_checkout", lambda *a, **k: dict(payload))
        r1 = client.post("/api/platform/verify",
                         json={"payment_id": "PLAT-HTTP-1", "tx_hash": TX_A})
        assert r1.status_code == 200, r1.data
        assert r1.get_json()["status"] == "fulfilled"
        assert len(fulfilled) == 1
        with mvp.app.test_request_context():
            row = mvp.get_db().execute(
                "SELECT payment_status FROM orders WHERE order_id=?",
                ("TOKEN-ORD-PLAT-HTTP-1",)).fetchone()
        assert row["payment_status"] == "completed"
        # Idempotent re-poll: already fulfilled -> 200, no second provision.
        payload2 = self._finalized_payload(status="already_fulfilled")
        monkeypatch.setattr(
            billing, "platform_verify_checkout",
            lambda *a, **k: dict(payload2))
        r2 = client.post("/api/platform/verify",
                         json={"payment_id": "PLAT-HTTP-1", "tx_hash": TX_A})
        assert r2.status_code == 200, r2.data
        assert len(fulfilled) == 1

    def test_sanctions_hit_maps_to_403(self, mvp_env, monkeypatch):
        client, billing, mvp, fulfilled = mvp_env
        monkeypatch.setattr(
            billing, "platform_verify_checkout",
            lambda *a, **k: {"ok": False, "error": "sanctions_hit",
                             "status": "blocked"})
        r = client.post("/api/platform/verify",
                        json={"payment_id": "PLAT-HTTP-9", "tx_hash": TX_B})
        assert r.status_code == 403, r.data
        assert not fulfilled


class TestPaidVaultAuth:
    EMAIL = "vault-b4@test.com"

    def _orders(self, mvp_env, statuses):
        client, billing, mvp, _ = mvp_env
        with mvp.app.test_request_context():
            for i, st in enumerate(statuses):
                _insert_order(mvp, f"VAULT-{i}", self.EMAIL, st)
        return client

    def test_pending_order_denied_402(self, mvp_env):
        client, billing, mvp, _ = mvp_env
        with mvp.app.test_request_context():
            _insert_order(mvp, "VAULT-P", self.EMAIL, "pending")
        r = client.get(
            "/admin/training-vault",
            query_string={"email": self.EMAIL, "order_id": "VAULT-P"},
        )
        assert r.status_code == 402, r.status_code

    def test_failed_and_refunded_denied_403(self, mvp_env):
        client, billing, mvp, _ = mvp_env
        with mvp.app.test_request_context():
            _insert_order(mvp, "VAULT-F", self.EMAIL, "failed")
            _insert_order(mvp, "VAULT-R", self.EMAIL, "refunded")
        for oid in ("VAULT-F", "VAULT-R"):
            r = client.get(
                "/admin/training-vault",
                query_string={"email": self.EMAIL, "order_id": oid},
            )
            assert r.status_code == 403, (oid, r.status_code)

    def test_paid_order_served_200(self, mvp_env):
        client, billing, mvp, _ = mvp_env
        with mvp.app.test_request_context():
            _insert_order(mvp, "VAULT-OK", self.EMAIL, "completed")
        r = client.get(
            "/admin/training-vault",
            query_string={"email": self.EMAIL, "order_id": "VAULT-OK"},
        )
        assert r.status_code == 200, r.status_code
        assert b"Starter" in r.data

    def test_download_guide_denies_unpaid(self, mvp_env):
        client, billing, mvp, _ = mvp_env
        with mvp.app.test_request_context():
            _insert_order(mvp, "PEND-9", self.EMAIL, "pending")
            _insert_order(mvp, "FAIL-9", self.EMAIL, "failed")
        r = client.get(
            "/files/guides/sincor-starter-guide-PEND-9.pdf",
            query_string={"email": self.EMAIL, "order_id": "PEND-9"},
        )
        assert r.status_code == 402, r.status_code
        r = client.get(
            "/files/guides/sincor-starter-guide-FAIL-9.pdf",
            query_string={"email": self.EMAIL, "order_id": "FAIL-9"},
        )
        assert r.status_code == 403, r.status_code

    def test_download_guide_serves_paid(self, mvp_env, tmp_path):
        client, billing, mvp, _ = mvp_env
        with mvp.app.test_request_context():
            _insert_order(mvp, "PAID-9", self.EMAIL, "completed")
        guides = tmp_path / "guides"
        (guides / "sincor-starter-guide-PAID-9.pdf").write_bytes(b"%PDF-1.4 fake")
        r = client.get(
            "/files/guides/sincor-starter-guide-PAID-9.pdf",
            query_string={"email": self.EMAIL, "order_id": "PAID-9"},
        )
        assert r.status_code == 200, r.status_code
        assert r.data.startswith(b"%PDF-1.4")

    def test_vault_unknown_order_404(self, mvp_env):
        client, billing, mvp, _ = mvp_env
        r = client.get(
            "/admin/training-vault",
            query_string={"email": self.EMAIL, "order_id": "NOPE-0"},
        )
        assert r.status_code == 404
