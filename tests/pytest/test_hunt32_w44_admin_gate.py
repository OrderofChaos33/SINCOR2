"""Hunt-32 W-44 (extension): the production admin gate must not admit the
two retired credential shapes, on ANY surface the gate guards.

CONFIRMED finding (red-team hunt 32, PoC poc32c_cc_admin.py): on
sincor2.mvp_app, ``_check_admin_key`` accepted the admin key from the
JSON request BODY (the secret-in-body shape retired elsewhere), and
``_check_admin_token`` accepted any JWT whose ``sub`` equals the admin
username with NO role check. Both reached the #563 Command Center APIs
through the ``_require_admin`` before_request guard — a body-key POST
to the killswitch actually flipped the controller to killed — and the
role-less JWT also passed on /api/waitlist/stats.

Fixed contract:
  * admin key: ``X-Admin-Key`` header ONLY (plus the admin session).
    Body (JSON or form), query string, cookies, and alternate header
    names never authenticate.
  * admin token: Bearer / ``access_token`` cookie JWT must carry
    ``sub`` == admin username AND ``role == 'admin'`` (the claim every
    real admin login mints: mvp_blueprints/auth.py, auth_system.py,
    mvp_app._admin_cookie_response). Sub alone is never sufficient.

The waitlist handler's own ``_is_admin_request`` mirror
(blueprints/waitlist.py) is the ONLY gate on the legacy app mount, so
the role-less JWT refusal is asserted there too.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

CC = "/api/command-center"
STATS = "/api/waitlist/stats"
PROBE_AGENT = "E-w44-probe-01"

CC_GET_PATHS = [
    f"{CC}/agents",
    f"{CC}/market-snapshot",
    f"{CC}/killswitch",
    f"{CC}/agents/{PROBE_AGENT}/memory",
]


def _admin_user() -> str:
    return os.environ.get("ADMIN_USERNAME", "a")


def _admin_pw() -> str:
    return os.environ.get("ADMIN_PASSWORD", "b")


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app.test_client()


@pytest.fixture
def isolated_controller(tmp_path, monkeypatch):
    """Throwaway token-budget controller so killswitch state assertions
    never touch the repo's data dir (mirrors test_command_center_auth)."""
    import sincor2.token_budget_controller as tbc

    ctrl = tbc.TokenBudgetController(ledger_path=tmp_path / "tb_ledger.json")
    monkeypatch.setattr(tbc, "_controller", ctrl)
    return ctrl


def _mint(mvp_client, identity, claims=None):
    from flask_jwt_extended import create_access_token
    from sincor2.mvp_app import app as mvp_app

    with mvp_app.app_context():
        if claims is None:
            return create_access_token(identity=identity)
        return create_access_token(identity=identity, additional_claims=claims)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _assert_no_counts(response):
    body = response.get_data(as_text=True)
    assert "total_signups" not in body
    assert "verified_signups" not in body
    assert "latest_signup_at" not in body


@pytest.fixture
def seeded_mvp_manager(mvp_client, tmp_path):
    """Swap the production app's waitlist manager for a seeded tmp one so
    stats positive controls see exact aggregates (mirrors
    test_waitlist_stats' production pattern)."""
    from sincor2 import waitlist_system
    from sincor2.mvp_app import app as mvp_app

    seeded = waitlist_system.WaitlistManager(db_path=str(tmp_path / "w44_waitlist.db"))
    with sqlite3.connect(seeded.db_path) as conn:
        conn.executemany(
            "INSERT INTO waitlist"
            " (email_hash, encrypted_email, product_interest, is_verified, signup_date)"
            " VALUES (?, 'encrypted-seed', 'sincor', ?, ?)",
            [
                ("seedhash-alice", 1, "2026-10-20 09:15:00"),
                ("seedhash-bob", 0, "2026-10-25 12:00:00"),
            ],
        )
    original = mvp_app.extensions.get("waitlist_manager")
    mvp_app.extensions["waitlist_manager"] = seeded
    try:
        yield seeded
    finally:
        if original is None:
            mvp_app.extensions.pop("waitlist_manager", None)
        else:
            mvp_app.extensions["waitlist_manager"] = original


# ---------------------------------------------------------------------------
# Body admin_key: the retired secret-in-body shape must authenticate NOTHING
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", CC_GET_PATHS)
def test_body_key_get_routes_refused(mvp_client, isolated_controller, path):
    r = mvp_client.get(path, json={"admin_key": _admin_pw()})
    assert r.status_code == 401, path


def test_body_key_market_feed_refused(mvp_client, isolated_controller):
    r = mvp_client.get(f"{CC}/market-feed", json={"admin_key": _admin_pw()})
    try:
        assert r.status_code == 401
        assert "text/event-stream" not in (r.content_type or "")
    finally:
        r.close()


def test_body_key_killswitch_post_refused_and_state_unchanged(
    mvp_client, isolated_controller
):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False

    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "w44-poc", "admin_key": _admin_pw()},
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_body_key_grade_task_refused(mvp_client, isolated_controller):
    r = mvp_client.post(
        f"{CC}/grade-task",
        json={
            "task_id": "t-1",
            "agent_id": PROBE_AGENT,
            "grade": 9.0,
            "admin_key": _admin_pw(),
        },
    )
    assert r.status_code == 401


def test_form_encoded_admin_key_refused(mvp_client, isolated_controller):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        data={"action": "kill", "admin_key": _admin_pw()},
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_query_admin_key_refused(mvp_client, isolated_controller):
    r = mvp_client.get(f"{CC}/agents?admin_key={_admin_pw()}")
    assert r.status_code == 401


def test_alternate_header_name_refused(mvp_client, isolated_controller):
    # Only the exact X-Admin-Key header carries the key; a lookalike
    # header name is not a credential channel.
    r = mvp_client.get(f"{CC}/agents", headers={"Admin-Key": _admin_pw()})
    assert r.status_code == 401


def test_cookie_admin_key_refused(mvp_client, isolated_controller):
    mvp_client.set_cookie("admin_key", _admin_pw())
    r = mvp_client.get(f"{CC}/agents")
    assert r.status_code == 401


def test_body_key_waitlist_stats_refused(mvp_client, seeded_mvp_manager):
    r = mvp_client.get(STATS, json={"admin_key": _admin_pw()})
    assert r.status_code == 401
    _assert_no_counts(r)


# ---------------------------------------------------------------------------
# Role-less JWT: sub == admin username is never sufficient on its own
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", CC_GET_PATHS)
def test_roleless_jwt_get_routes_refused(mvp_client, isolated_controller, path):
    token = _mint(mvp_client, _admin_user())
    r = mvp_client.get(path, headers=_bearer(token))
    assert r.status_code == 401, path


def test_roleless_jwt_killswitch_refused_and_state_unchanged(
    mvp_client, isolated_controller
):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    token = _mint(mvp_client, _admin_user())
    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "w44-poc"},
        headers=_bearer(token),
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_roleless_jwt_cookie_refused(mvp_client, isolated_controller):
    token = _mint(mvp_client, _admin_user())
    mvp_client.set_cookie("access_token", token)
    r = mvp_client.get(f"{CC}/agents")
    assert r.status_code == 401


def test_roleless_jwt_waitlist_stats_refused_mvp(mvp_client, seeded_mvp_manager):
    token = _mint(mvp_client, _admin_user())
    r = mvp_client.get(STATS, headers=_bearer(token))
    assert r.status_code == 401
    _assert_no_counts(r)


def test_roleless_jwt_waitlist_stats_refused_legacy(client, app):
    # On the legacy app the waitlist handler's own _is_admin_request is
    # the only gate over the stats route; it must refuse too.
    from flask_jwt_extended import create_access_token

    with app.app_context():
        token = create_access_token(identity=_admin_user())
    r = client.get(STATS, headers=_bearer(token))
    assert r.status_code in (401, 403)
    _assert_no_counts(r)


def test_non_admin_role_jwt_refused(mvp_client, isolated_controller):
    token = _mint(mvp_client, _admin_user(), claims={"role": "user"})
    r = mvp_client.get(f"{CC}/agents", headers=_bearer(token))
    assert r.status_code == 401


def test_admin_role_jwt_wrong_sub_refused(mvp_client, isolated_controller):
    # role='admin' does not rescue a token for a different identity.
    token = _mint(mvp_client, "somebody-else", claims={"role": "admin"})
    r = mvp_client.get(f"{CC}/agents", headers=_bearer(token))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Positive controls: every legitimate admin path keeps working
# ---------------------------------------------------------------------------

def test_header_key_cc_agents_ok(mvp_client, isolated_controller):
    r = mvp_client.get(f"{CC}/agents", headers={"X-Admin-Key": _admin_pw()})
    assert r.status_code == 200


def test_header_key_waitlist_stats_ok(mvp_client, seeded_mvp_manager):
    r = mvp_client.get(STATS, headers={"X-Admin-Key": _admin_pw()})
    assert r.status_code == 200
    assert r.get_json()["total_signups"] == 2


def test_role_jwt_cc_agents_ok(mvp_client, isolated_controller):
    token = _mint(mvp_client, _admin_user(), claims={"role": "admin"})
    r = mvp_client.get(f"{CC}/agents", headers=_bearer(token))
    assert r.status_code == 200


def test_role_jwt_cookie_cc_agents_ok(mvp_client, isolated_controller):
    token = _mint(mvp_client, _admin_user(), claims={"role": "admin"})
    mvp_client.set_cookie("access_token", token)
    r = mvp_client.get(f"{CC}/agents")
    assert r.status_code == 200


def test_role_jwt_waitlist_stats_ok(mvp_client, seeded_mvp_manager):
    token = _mint(mvp_client, _admin_user(), claims={"role": "admin"})
    r = mvp_client.get(STATS, headers=_bearer(token))
    assert r.status_code == 200
    assert r.get_json()["total_signups"] == 2
    assert r.get_json()["verified_signups"] == 1


def test_admin_session_cc_agents_ok(mvp_client, isolated_controller):
    with mvp_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = _admin_user()
    r = mvp_client.get(f"{CC}/agents")
    assert r.status_code == 200


def test_login_minted_token_admitted(mvp_client, isolated_controller):
    # The real admin login (mvp_blueprints/auth.py) mints role='admin';
    # its token must pass the gate end to end.
    r = mvp_client.post(
        "/api/auth/login",
        json={"username": _admin_user(), "password": _admin_pw()},
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    token = r.get_json()["access_token"]
    r = mvp_client.get(f"{CC}/agents", headers=_bearer(token))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Legacy factory (sincor2.app.create_app): the Command Center mount must
# carry the SAME gate. Builder B's adversarial pass found this mount was
# bare — anonymous killswitch POST flipped the controller, anonymous GETs
# returned the roster/status, anonymous grade-task was accepted — while
# the production factory held. mvp_app's rule: never mount this blueprint
# without an equivalent guard in front of /api/command-center/*.
# ---------------------------------------------------------------------------

def _mint_legacy(app, identity, claims=None):
    from flask_jwt_extended import create_access_token

    with app.app_context():
        if claims is None:
            return create_access_token(identity=identity)
        return create_access_token(identity=identity, additional_claims=claims)


@pytest.mark.parametrize("path", CC_GET_PATHS)
def test_legacy_anon_get_routes_refused(client, isolated_controller, path):
    r = client.get(path)
    assert r.status_code == 401, path


def test_legacy_anon_market_feed_refused(client, isolated_controller):
    r = client.get(f"{CC}/market-feed")
    try:
        assert r.status_code == 401
        assert "text/event-stream" not in (r.content_type or "")
    finally:
        r.close()


def test_legacy_anon_killswitch_post_refused_and_state_unchanged(
    client, isolated_controller
):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False

    r = client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "w44-bprobe"},
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_legacy_anon_grade_task_refused(client, isolated_controller):
    r = client.post(
        f"{CC}/grade-task",
        json={"task_id": "t-1", "agent_id": PROBE_AGENT, "grade": 9.0},
    )
    assert r.status_code == 401


def test_legacy_body_key_killswitch_refused_and_state_unchanged(
    client, isolated_controller
):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    r = client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "w44-bprobe", "admin_key": _admin_pw()},
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_legacy_roleless_jwt_refused(client, app, isolated_controller):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    token = _mint_legacy(app, _admin_user())
    r = client.get(f"{CC}/agents", headers=_bearer(token))
    assert r.status_code == 401
    r = client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "w44-bprobe"},
        headers=_bearer(token),
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_legacy_header_key_cc_agents_ok(client, isolated_controller):
    r = client.get(f"{CC}/agents", headers={"X-Admin-Key": _admin_pw()})
    assert r.status_code == 200


def test_legacy_header_key_killswitch_ok(client, isolated_controller):
    # Positive control: the gate admits the real admin credential and the
    # handler actually runs (state flips) — the gate is a gate, not a wall.
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    r = client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "w44-control"},
        headers={"X-Admin-Key": _admin_pw()},
    )
    assert r.status_code == 200
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is True


def test_legacy_role_jwt_cc_agents_ok(client, app, isolated_controller):
    token = _mint_legacy(app, _admin_user(), claims={"role": "admin"})
    r = client.get(f"{CC}/agents", headers=_bearer(token))
    assert r.status_code == 200


def test_legacy_admin_session_cc_agents_ok(client, isolated_controller):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = _admin_user()
    r = client.get(f"{CC}/agents")
    assert r.status_code == 200


def test_legacy_non_ascii_admin_key_refused_not_500(client, isolated_controller):
    r = client.get(f"{CC}/agents", headers={"X-Admin-Key": _admin_pw() + "ü"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Non-ASCII X-Admin-Key: hmac.compare_digest raises TypeError on
# non-ASCII str input. Every key compare must fail CLOSED (401 / False),
# never surface the exception as a 500.
# ---------------------------------------------------------------------------

def test_non_ascii_admin_key_mvp_cc_refused_not_500(mvp_client, isolated_controller):
    r = mvp_client.get(f"{CC}/agents", headers={"X-Admin-Key": _admin_pw() + "ü"})
    assert r.status_code == 401


def test_non_ascii_admin_key_legacy_waitlist_stats_refused_not_500(client):
    # Exercises the waitlist handler's own _is_admin_request mirror on
    # the legacy mount (the mirror's header compare).
    r = client.get(STATS, headers={"X-Admin-Key": _admin_pw() + "ü"})
    assert r.status_code == 401
    _assert_no_counts(r)


def test_non_ascii_admin_key_sponsored_stake_fails_closed(app):
    # sponsored_stake._admin_key_ok is the identical one-spot compare
    # pattern (X-Admin-Key vs ADMIN_PASSWORD); it must return False,
    # not raise, on an un-comparable header value.
    from sincor2.sponsored_stake import _admin_key_ok

    with app.test_request_context(headers={"X-Admin-Key": _admin_pw() + "ü"}):
        assert _admin_key_ok() is False
    with app.test_request_context(headers={"X-Admin-Key": _admin_pw()}):
        assert _admin_key_ok() is True
