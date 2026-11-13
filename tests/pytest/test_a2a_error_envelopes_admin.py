"""Wave 17: JSON error envelopes (G2.11), unified admin credential (G2.13),
surfaced pool-release failures (G2.12).

Acceptance:
* garbage ?limit=abc / ?page=abc -> JSON 400, never an HTML 500
* one admin credential surface: ADMIN_PASSWORD via X-Admin-Key header only;
  body keys rejected; legacy SINCOR_BOUNTY_POOL_ADMIN_KEY retired
* task delete with a failing pool release -> error surfaces, never silent
"""
from __future__ import annotations

import pytest
from flask import Blueprint, Flask

from sincor2.a2a_errors import register_a2a_error_handlers
from sincor2.a2a_inbound import register as register_inbound, reset_fabric

ADMIN_PW = "test-admin-pw-17"


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.delenv("SINCOR_BOUNTY_POOL_ADMIN_KEY", raising=False)
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    app = Flask(__name__)
    register_inbound(app)
    from sincor2.a2a_integration import A2ARouter

    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    return app.test_client()


def _admin_headers(key=ADMIN_PW):
    return {"X-Admin-Key": key}


# -- G2.11: JSON envelopes ------------------------------------------------------


def test_limit_garbage_is_json_400(app_client):
    r = app_client.get("/api/a2a/leaderboard", query_string={"limit": "abc"})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.content_type.startswith("application/json")
    body = r.get_json()
    assert body["status"] == 400
    assert "limit" in body["error"]
    assert "<html" not in r.get_data(as_text=True).lower()


def test_limit_valid_still_works(app_client):
    r = app_client.get("/api/a2a/leaderboard", query_string={"limit": "5"})
    assert r.status_code == 200
    assert len(r.get_json()["leaderboard"]) <= 5


def test_tasks_page_garbage_is_json_400(app_client):
    r = app_client.get("/v1/a2a/tasks", query_string={"page": "abc"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["status"] == 400
    assert "page" in body["error"]


def test_jsonrpc_pagesize_garbage_is_invalid_params(app_client):
    # tasks/list now requires caller auth (main-side P3 item 14); sign the
    # list message, then garbage pageSize must still surface as -32602.
    import time
    import uuid

    from eth_account import Account
    from eth_account.messages import encode_defunct

    from sincor2.a2a_integration import _auth_task_message

    acct = Account.from_key("0x" + "cc" * 32)
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = "0x" + acct.sign_message(
        encode_defunct(text=_auth_task_message("list", "", ts, nonce))
    ).signature.hex()
    r = app_client.post(
        "/api/a2a",
        json={"jsonrpc": "2.0", "id": 7, "method": "tasks/list",
              "params": {"pageSize": "abc",
                         "ownerWallet": acct.address,
                         "authSignature": sig,
                         "authTimestamp": ts,
                         "authNonce": nonce}},
    )
    assert r.status_code == 200  # JSON-RPC errors ride HTTP 200
    payload = r.get_json()
    assert payload["error"]["code"] == -32602
    assert "pageSize" in payload["error"]["message"]


def test_unhandled_exception_is_json_500_without_leak():
    app = Flask(__name__)
    bp = Blueprint("boom", __name__)
    register_a2a_error_handlers(bp)

    @bp.get("/boom")
    def boom():
        raise RuntimeError("super-secret-internal-detail-xyz")

    app.register_blueprint(bp)
    app.config["TESTING"] = True
    r = app.test_client().get("/boom")
    assert r.status_code == 500
    assert r.content_type.startswith("application/json")
    text = r.get_data(as_text=True)
    assert "super-secret-internal-detail-xyz" not in text
    assert "Traceback" not in text
    body = r.get_json()
    assert body == {"error": "internal server error", "status": 500}


def test_http_exception_is_json_envelope():
    from werkzeug.exceptions import Forbidden

    app = Flask(__name__)
    bp = Blueprint("deny", __name__)
    register_a2a_error_handlers(bp)

    @bp.get("/deny")
    def deny():
        raise Forbidden("no entry")

    app.register_blueprint(bp)
    app.config["TESTING"] = True
    r = app.test_client().get("/deny")
    assert r.status_code == 403
    assert r.get_json() == {"error": "no entry", "status": 403}


# -- G2.13: single admin credential ---------------------------------------------


def test_pool_body_key_rejected(app_client):
    # admin_key in the body no longer authenticates (secret-persistence risk)
    r = app_client.post("/v1/a2a/pool/fund", json={"admin_key": ADMIN_PW})
    assert r.status_code == 401
    assert r.get_json()["status"] == 401


def test_pool_header_key_accepted(app_client):
    r = app_client.post("/v1/a2a/pool/fund", json={},
                        headers=_admin_headers())
    # 503: no reserve configured — but NOT 401: the header credential works
    assert r.status_code == 503, r.get_json()


def test_pool_wrong_key_rejected(app_client):
    r = app_client.post("/v1/a2a/pool/fund", json={},
                        headers=_admin_headers("wrong-key"))
    assert r.status_code == 401


def test_legacy_pool_key_retired(app_client, monkeypatch):
    # SINCOR_BOUNTY_POOL_ADMIN_KEY alone no longer enables the admin surface
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_ADMIN_KEY", "legacy-key")
    r = app_client.post("/v1/a2a/pool/fund", json={},
                        headers={"X-Admin-Key": "legacy-key"})
    assert r.status_code == 503, r.get_json()


def test_pool_admin_disabled_without_password(app_client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    r = app_client.post("/v1/a2a/pool/fund", json={},
                        headers=_admin_headers())
    assert r.status_code == 503  # deny-by-default, not 401


def test_sponsored_stake_body_key_rejected(app_client):
    r = app_client.post("/v1/a2a/admin/sponsored-stake",
                        json={"admin_key": ADMIN_PW, "agent_id": "x"})
    assert r.status_code == 401


def test_sponsored_stake_header_key_envelope(app_client):
    # register a real agent so the request reaches the mechanism gate
    # (unknown agents 404 before the enablement check)
    r = app_client.post("/v1/a2a/register", json={
        "agent_card": {
            "name": "W17 Agent", "description": "envelope probe",
            "version": "1.0.0",
            "skills": [{"id": "probe", "name": "Probe", "tags": ["t"]}],
        },
        "agent_url": "https://w17.example.com",
    })
    assert r.status_code == 201, r.get_json()
    agent_id = r.get_json()["agent_id"]
    # header credential passes auth; mechanism is default-off -> 403 envelope
    r = app_client.post("/v1/a2a/admin/sponsored-stake",
                        json={"agent_id": agent_id, "amount_axm": 1},
                        headers=_admin_headers())
    assert r.status_code == 403
    body = r.get_json()
    assert body["status"] == 403 and "error" in body


# -- G2.12: release failures surface --------------------------------------------


def test_task_delete_release_failure_surfaces(app_client, monkeypatch):
    from sincor2 import a2a_bounty_pool as pool_mod
    from sincor2.a2a_inbound_market import create_task

    task = create_task(skill="probe", bounty_axm=1.0,
                       seed_key="w17-release-fail",
                       title="T", description="D")
    task_id = task["task_id"]

    monkeypatch.setattr(
        pool_mod.BountyPool, "allocations_for_task",
        lambda self, tid: [{"allocation_id": "alloc-1"}]
        if tid == task_id else [])

    def _boom(self, allocation_id):
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr(pool_mod.BountyPool, "release", _boom)

    r = app_client.delete(f"/v1/a2a/tasks/{task_id}",
                          headers=_admin_headers())
    assert r.status_code == 500, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == 500
    assert "release failed" in body["error"]
    assert body["released_allocations"] == []
    assert len(body["release_failures"]) == 1
    assert body["release_failures"][0]["allocation_id"] == "alloc-1"
    assert "simulated ledger failure" in body["release_failures"][0]["error"]
    # the task row itself is still deleted
    assert app_client.get(f"/v1/a2a/tasks/{task_id}").status_code == 404


def test_task_delete_clean_still_200(app_client):
    from sincor2.a2a_inbound_market import create_task

    task = create_task(skill="probe", bounty_axm=1.0,
                       seed_key="w17-release-clean",
                       title="T", description="D")
    r = app_client.delete(f"/v1/a2a/tasks/{task['task_id']}",
                          headers=_admin_headers())
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["deleted"] == task["task_id"]
    assert body["released_allocations"] == []
