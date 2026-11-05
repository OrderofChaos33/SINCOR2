"""Wren PR 8 — adversarial-fix regressions.

The independent adversarial pass on PR 8's gate fix returned
REJECTED with three holes; each test below pins one hole's exact
interleaving/leak (red on the pre-fix code, green after):

  * Hole A — a buyer-first execution claim could be reclaimed as
    "stale" while the claim holder's kickoff body was still
    running (the 120s staleness window cannot tell a slow body
    from a dead worker), letting a second delivery execute the
    kickoff mid-body. The holder now heartbeats its claim, so a
    LIVE claim never goes stale; only a dead holder's claim does.
  * Hole B — the claim release popped this process's claim
    registry by intake id alone, so after a same-process reclaim
    overwrote the registry entry, a stale holder's release cleared
    the FRESHER claim's marker (CAS-compared against the wrong —
    newer — timestamp). Releases are now strictly
    ownership-checked per claim.
  * Hole C — the pack_email_failed alert's constructed message no
    longer named the buyer, but the interpolated exception text
    could still carry the buyer's email (SMTP errors routinely
    echo the recipient). Exception text interpolated into the
    alert is now scrubbed of email-shaped strings.

Plus the item-4 audit: _paid_skill_dispatch's transport stripping
covered access_token/payer_wallet but NOT the replay keys
(replay_signature, replay_ts_ms) — every branch forwarded them to
the skill handler. The parametrized test proves no branch, on
either transport, lets a transport/replay key reach its handler.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

# Production import order matters (same pattern as
# test_wren_pr8_polish.py): mvp_app first for binding side effects.
import sincor2.mvp_app  # noqa: F401
from sincor2.mvp_blueprints import billing

import sincor2.rail_routes as rr
from sincor2 import payment_ledger as pl

BUYER_EMAIL = "buyer@example.com"
PAYER = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
INTAKE_TOKEN = "tok-pr8adv-unguessable-1"
EVENT_ID = "rail-x402-pr8adv001"
INTAKE_ID = f"intake-{EVENT_ID}"

ANSWERS = {"business_name": "Acme Dental LLC"}

EMAIL_SHAPE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@pytest.fixture()
def rail_env(tmp_path, monkeypatch):
    """Hermetic rail ledger (the pack intakes + alerts store)."""
    db = tmp_path / "revenue.db"
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(db))
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    return db


def _seed(db, *, status="created", details=None, intake_id=INTAKE_ID):
    base_details = {
        "pack_name": rr.PACK_SKUS["credentialing_launch"]["name"],
        "module": rr.PACK_SKUS["credentialing_launch"].get("module", ""),
        "payer_wallet": PAYER.lower(),
        "rail": True,
        "intake_token": INTAKE_TOKEN,
    }
    if details:
        base_details.update(details)
    intake, _is_new = pl.record_pack_intake(
        db,
        intake_id=intake_id,
        customer_email=BUYER_EMAIL,
        pack_sku="credentialing_launch",
        event_id=EVENT_ID,
        intake_url=f"https://getsincor.com/intake/{intake_id}"
                   f"?token={INTAKE_TOKEN}",
        details=base_details,
    )
    if status != "created":
        pl.update_pack_intake_status(db, intake_id, status)
    return intake


def _marker(intake_id=INTAKE_ID):
    _status, details = rr._pack_intake_state(intake_id)
    return details.get(rr._KICKOFF_EXECUTION_CLAIM_KEY)


def _stale_ts(seconds=130.0):
    return (datetime.now(timezone.utc)
            - timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# Hole A: a LIVE execution claim must not be reclaimable mid-body
# ---------------------------------------------------------------------------

def test_live_execution_claim_cannot_be_reclaimed_mid_body(
        rail_env, monkeypatch):
    db = rail_env
    _seed(db, status="intake_complete",
          details={"intake_answers": ANSWERS})
    # Fast heartbeat so the holder's liveness lands promptly (the
    # constant only exists on the fixed code; raising=False keeps
    # this a clean assertion failure — not a collection error — on
    # the unfixed code).
    monkeypatch.setattr(
        rr, "_KICKOFF_EXECUTION_CLAIM_HEARTBEAT_SECONDS", 0.05,
        raising=False)
    # Delivery 1 wins the execution claim. Its claim is stamped
    # already older than the staleness window: the worst case,
    # a body that has been running longer than 120s.
    stale_ts = _stale_ts()
    real_now_iso = rr._utc_now_iso
    monkeypatch.setattr(rr, "_utc_now_iso", lambda: stale_ts)
    gate, prior = rr._begin_pack_kickoff(INTAKE_ID)
    assert (gate, prior) == ("begun", "intake_complete")
    monkeypatch.setattr(rr, "_utc_now_iso", real_now_iso)

    # The holder is ALIVE: its heartbeat refreshes the marker, so
    # the claim never actually goes stale. (On the unfixed code
    # nothing refreshes it and this poll simply lapses.)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if _marker() not in (None, stale_ts):
            break
        time.sleep(0.02)

    # A duplicate delivery arriving now must stand down — on the
    # unfixed code it reclaims the "stale" marker and begins a
    # SECOND execution while the first body is still running.
    gate2, _prior2 = rr._begin_pack_kickoff(INTAKE_ID)
    assert gate2 == "already"

    # The live holder's own release still works afterwards.
    assert rr._release_pack_kickoff_run(INTAKE_ID, prior) is True
    assert _marker() is None


# ---------------------------------------------------------------------------
# Hole B: a stale release must never clear a fresher claim
# ---------------------------------------------------------------------------

def test_stale_release_cannot_clear_fresher_claim(rail_env):
    import threading

    db = rail_env
    _seed(db, status="intake_complete",
          details={"intake_answers": ANSWERS})
    try:
        # Claim 1 is won by a holder on its OWN delivery thread —
        # its claim token lives on that thread, exactly as in
        # production.
        box = {}
        claimed = threading.Event()
        release_now = threading.Event()
        released = threading.Event()

        def _stale_holder():
            _status, details = rr._pack_intake_state(INTAKE_ID)
            box["won1"] = rr._claim_pack_kickoff_execution(
                INTAKE_ID, details)
            claimed.set()
            release_now.wait(timeout=10)
            box["release_result"] = rr._release_pack_kickoff_execution(
                INTAKE_ID)
            released.set()

        holder = threading.Thread(target=_stale_holder, daemon=True)
        holder.start()
        assert claimed.wait(timeout=10)
        assert box["won1"] is True
        ts1 = _marker()
        assert ts1

        # The first claim is presumed orphaned (its marker aged
        # past the staleness window) and a second delivery reclaims
        # the execution — the same-process reclaim that used to
        # overwrite the claim registry entry.
        pl.update_pack_intake_status(
            db, INTAKE_ID, "intake_complete",
            details={rr._KICKOFF_EXECUTION_CLAIM_KEY: _stale_ts()})
        _status, details2 = rr._pack_intake_state(INTAKE_ID)
        assert rr._claim_pack_kickoff_execution(INTAKE_ID, details2) is True
        ts2 = _marker()
        assert ts2 and ts2 != ts1

        # The stale holder's body now fails and it releases: on the
        # unfixed code its release popped the registry's
        # (overwritten) entry and CAS-cleared the FRESHER claim's
        # marker. It must now be refused, and the fresher marker
        # must survive untouched.
        release_now.set()
        assert released.wait(timeout=10)
        holder.join(timeout=10)
        assert box["release_result"] is False
        assert _marker() == ts2
    finally:
        # Drop this test's registry residue so any claim heartbeat
        # threads idle out instead of probing later tests' rows.
        rr._kickoff_execution_claims.pop(INTAKE_ID, None)


# ---------------------------------------------------------------------------
# Hole C: exception text must not carry the buyer's email into the alert
# ---------------------------------------------------------------------------

def test_pack_email_failed_alert_scrubs_exception_email(
        rail_env, monkeypatch):
    db = rail_env

    def _boom(**kwargs):
        # SMTP-style failure text echoing the recipient — the shape
        # real mail errors take, and the leak the review reproduced.
        raise RuntimeError(
            f"550 5.1.1 <{BUYER_EMAIL}> recipient rejected; "
            f"retry to {BUYER_EMAIL} refused by remote host")

    monkeypatch.setattr(rr, "_send_pack_email", _boom)
    rr._fulfill_rail_pack({
        "event_id": EVENT_ID,
        "customer_email": BUYER_EMAIL,
        "customer_name": "Test Buyer",
        "pack_sku": "credentialing_launch",
        "payer_wallet": PAYER,
    })

    alerts = [a for a in pl.list_alerts(db, payment_id=EVENT_ID)
              if a.kind == "pack_email_failed"]
    assert len(alerts) == 1
    alert = alerts[0]
    assert INTAKE_ID in alert.message
    assert BUYER_EMAIL not in alert.message
    # No email-shaped string from the exception survives either.
    assert EMAIL_SHAPE.search(alert.message) is None
    assert alert.details.get("intake_id") == INTAKE_ID
    assert BUYER_EMAIL not in str(alert.details)
    assert EMAIL_SHAPE.search(str(alert.details)) is None


# ---------------------------------------------------------------------------
# Item 4 audit: replay keys stripped on EVERY dispatch branch,
# on both transports (JSON body and query args), in any case
# ---------------------------------------------------------------------------

_BRANCH_HANDLERS = {
    "web-search-read": "sincor2.web_search_read.serve_web_search_read",
    "local-business-site-check": "sincor2.site_check.serve_site_check",
    "market-data-read": "sincor2.market_data_read.serve_market_data_read",
    "polymarket-whale-signal": "sincor2.whale_signal.serve_whale_signal",
    "kya-trust-score": "sincor2.kya_trust_score.serve_kya_trust_score",
    "wallet-sanctions-screen":
        "sincor2.sanctions_screen.serve_sanctions_screen",
    "toa-micro-decision":
        "sincor2.toa_micro_decision.serve_toa_micro_decision",
}

_TRANSPORT_PAYLOAD = {
    "access_token": "tok",
    "Access_Token": "tok2",
    "payer_wallet": "0xabc",
    "replay_signature": "0xreplay",
    "Replay_Signature": "0xreplay2",
    "replay_ts_ms": "1762348800000",
    "Replay_Ts_Ms": "1762348800001",
    "query": "dental implants",
}

_FORBIDDEN_LOWERED = {
    "access_token", "payer_wallet", "replay_signature", "replay_ts_ms",
}


def _install_recorder(monkeypatch, dotted, seen):
    import importlib

    module_name, _, attr = dotted.rpartition(".")
    module = importlib.import_module(module_name)

    def _fake(payload):
        seen["payload"] = payload
        return 200, {"ok": True}

    monkeypatch.setattr(module, attr, _fake)


@pytest.mark.parametrize("resource_id", sorted(_BRANCH_HANDLERS))
@pytest.mark.parametrize("transport", ["json", "args"])
def test_dispatch_strips_replay_keys_all_branches(
        monkeypatch, resource_id, transport):
    seen = {}
    _install_recorder(monkeypatch, _BRANCH_HANDLERS[resource_id], seen)
    app = Flask(__name__)
    if transport == "json":
        ctx = app.test_request_context(json=dict(_TRANSPORT_PAYLOAD))
    else:
        from urllib.parse import urlencode
        ctx = app.test_request_context(
            "/api/skill?" + urlencode(_TRANSPORT_PAYLOAD))
    with ctx:
        status, _body = billing._paid_skill_dispatch(resource_id)
    assert status == 200
    lowered = {str(k).lower() for k in seen["payload"]}
    assert not (_FORBIDDEN_LOWERED & lowered), (
        f"{resource_id} via {transport} leaked "
        f"{sorted(_FORBIDDEN_LOWERED & lowered)}")
    # Domain input still flows.
    assert seen["payload"]["query"] == "dental implants"


@pytest.mark.parametrize("transport", ["json", "args"])
def test_dispatch_default_branch_strips_replay_keys(
        monkeypatch, transport):
    import sincor2.x402_payments as x402

    seen = {}

    def _fake_execute(resource_id, payload):
        seen["payload"] = payload
        return 200, {"ok": True}

    monkeypatch.setattr(x402, "execute_paid_resource", _fake_execute)
    app = Flask(__name__)
    if transport == "json":
        ctx = app.test_request_context(json=dict(_TRANSPORT_PAYLOAD))
    else:
        from urllib.parse import urlencode
        ctx = app.test_request_context(
            "/api/skill?" + urlencode(_TRANSPORT_PAYLOAD))
    with ctx:
        status, _body = billing._paid_skill_dispatch(
            "unlisted-resource-pr8adv")
    assert status == 200
    lowered = {str(k).lower() for k in seen["payload"]}
    assert not (_FORBIDDEN_LOWERED & lowered)
    assert seen["payload"]["query"] == "dental implants"
