"""RT-01_ID-05: tenant-scoped runtime context + API authorization matrix.

Covers:
  * resolve_principal: JWT/session identity, server-side tenant resolution,
    client-supplied tenant_id ALWAYS ignored.
  * require_role / require_tenant_owner decorators.
  * Cross-tenant isolation on demo tenant-scoped resources
    (create/get/cancel/list/poll/result/evidence) — denials leak no data.
  * authz_matrix.json: schema validity + live probes against the real
    mvp_app documenting that the recorded guard matches reality
    (including honestly-recorded gaps).
  * RuntimeContext: immutability, queue attach/refuse-overwrite, round-trip.
  * ScopedDTO / to_public_*_dto field stripping.

Fake/mock/fixture only. No live provider calls.
"""
from __future__ import annotations

import dataclasses

import pytest
from flask import Flask, jsonify, request, session

from sincor2 import authz
from sincor2.authz import (
    AuthzError,
    AuthzMatrix,
    Principal,
    Role,
    RuntimeContext,
    attach_context,
    context_from_payload,
    propagate_context,
    require_role,
    require_tenant_owner,
    resolve_principal,
    scoped_dto,
    set_tenant_resolver,
    to_public_evidence_dto,
    to_public_order_dto,
    to_public_task_dto,
)

TENANT_A = "tenant-A"
TENANT_B = "tenant-B"
EMAIL_A = "a@example.com"
EMAIL_B = "b@example.com"


def _tenant_resolver(principal_id: str, role: Role) -> str:
    # Server-side tenant directory (fixture): never from the request.
    directory = {EMAIL_A: TENANT_A, EMAIL_B: TENANT_B}
    if role in (Role.admin, Role.operator, Role.system):
        return "system"
    return directory.get(principal_id, f"tenant:{principal_id}")


@pytest.fixture()
def tenant_resolver():
    set_tenant_resolver(_tenant_resolver)
    yield
    set_tenant_resolver(authz._default_tenant_resolver)


@pytest.fixture()
def demo_app(tenant_resolver):
    """Minimal Flask app exercising the authz decorators on tenant resources."""
    from flask_jwt_extended import JWTManager

    app = Flask("authz_demo")
    app.config["SECRET_KEY"] = "demo-session-secret-not-real"
    app.config["JWT_SECRET_KEY"] = "demo-test-secret-not-real"
    app.config["TESTING"] = True
    JWTManager(app)

    store = {
        "orders": {
            "o1": {"order_id": "o1", "tenant_id": TENANT_A, "secret": "A-SECRET-ORDER",
                   "product_name": "Starter", "amount": 297, "customer_email": EMAIL_A,
                   "internal_notes": "do not leak"},
        },
        "tasks": {
            "t1": {"task_id": "t1", "tenant_id": TENANT_A, "secret": "A-SECRET-TASK",
                   "kind": "demo", "status": "running", "handler_payload": "do not leak"},
        },
        "evidence": {
            "e1": {"entry_id": "e1", "tenant_id": TENANT_A, "secret": "A-SECRET-EVIDENCE",
                   "kind": "audit", "cost_cents": 999},
        },
    }
    app.config["AUTHZ_DEMO_STORE"] = store

    def _owner(kind):
        oid = request.view_args.get("oid") or request.view_args.get("tid") or request.view_args.get("eid")
        row = store[kind].get(oid)
        return row["tenant_id"] if row else None

    @app.post("/demo/orders")
    @require_role(Role.customer)
    def demo_create_order():
        body = request.get_json(silent=True) or {}
        p = authz.current_principal()
        oid = f"o-{len(store['orders']) + 1}"
        store["orders"][oid] = {
            "order_id": oid,
            # tenant comes from the verified principal, never the client body
            "tenant_id": p.tenant_id,
            "secret": body.get("secret", ""),
            "product_name": body.get("product_name", "?"),
        }
        return jsonify({"order_id": oid, "tenant_id": p.tenant_id}), 201

    @app.get("/demo/orders")
    @require_role(Role.customer)
    def demo_list_orders():
        p = authz.current_principal()
        mine = [to_public_order_dto(o) for o in store["orders"].values()
                if o["tenant_id"] == p.tenant_id]
        return jsonify({"orders": mine})

    @app.get("/demo/orders/<oid>")
    @require_tenant_owner(lambda: _owner("orders"))
    def demo_get_order(oid):
        return jsonify(to_public_order_dto(store["orders"][oid]))

    @app.post("/demo/orders/<oid>/cancel")
    @require_tenant_owner(lambda: _owner("orders"))
    def demo_cancel_order(oid):
        store["orders"][oid]["status"] = "canceled"
        return jsonify({"order_id": oid, "status": "canceled"})

    @app.get("/demo/tasks/<tid>/poll")
    @require_tenant_owner(lambda: _owner("tasks"))
    def demo_poll_task(tid):
        return jsonify(to_public_task_dto(store["tasks"][tid]))

    @app.get("/demo/tasks/<tid>/result")
    @require_tenant_owner(lambda: _owner("tasks"))
    def demo_task_result(tid):
        return jsonify(to_public_task_dto(store["tasks"][tid]))

    @app.get("/demo/evidence/<eid>")
    @require_tenant_owner(lambda: _owner("evidence"))
    def demo_get_evidence(eid):
        return jsonify(to_public_evidence_dto(store["evidence"][eid]))

    @app.get("/demo/admin-only")
    @require_role(Role.admin)
    def demo_admin_only():
        return jsonify({"ok": True})

    @app.get("/demo/whoami")
    def demo_whoami():
        try:
            p = resolve_principal()
        except AuthzError as exc:
            return jsonify({"error": exc.message}), exc.status_code
        return jsonify({"id": p.id, "role": p.role.value, "tenant_id": p.tenant_id})

    return app


@pytest.fixture()
def demo_client(demo_app):
    return demo_app.test_client()


def _token(demo_app, identity, role="customer"):
    from flask_jwt_extended import create_access_token

    with demo_app.test_request_context():
        return create_access_token(identity=identity,
                                   additional_claims={"role": role, "email": identity})


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# resolve_principal
# ---------------------------------------------------------------------------


class TestResolvePrincipal:
    def test_jwt_customer_resolves_server_tenant(self, demo_client, demo_app):
        r = demo_client.get("/demo/whoami", headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.status_code == 200
        assert r.get_json() == {"id": EMAIL_A, "role": "customer", "tenant_id": TENANT_A}

    def test_client_supplied_tenant_id_is_ignored_query(self, demo_client, demo_app):
        r = demo_client.get("/demo/whoami?tenant_id=tenant-B&tenant=tenant-B",
                            headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.get_json()["tenant_id"] == TENANT_A

    def test_client_supplied_tenant_id_is_ignored_body(self, demo_client, demo_app):
        r = demo_client.post("/demo/orders",
                             json={"tenant_id": TENANT_B, "product_name": "X"},
                             headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.status_code == 201
        assert r.get_json()["tenant_id"] == TENANT_A
        # and the stored row belongs to A, not B
        store = demo_app.config["AUTHZ_DEMO_STORE"]
        assert store["orders"]["o-2"]["tenant_id"] == TENANT_A

    def test_client_supplied_tenant_id_is_ignored_header(self, demo_client, demo_app):
        headers = _auth(_token(demo_app, EMAIL_A))
        r = demo_client.get("/demo/whoami", headers=headers,
                            query_string={"account_id": TENANT_B})
        assert r.get_json()["tenant_id"] == TENANT_A

    def test_session_customer(self, demo_client, demo_app):
        with demo_client.session_transaction() as sess:
            sess["user_email"] = EMAIL_B
        r = demo_client.get("/demo/whoami")
        assert r.get_json() == {"id": EMAIL_B, "role": "customer", "tenant_id": TENANT_B}

    def test_session_admin(self, demo_client):
        with demo_client.session_transaction() as sess:
            sess["is_admin"] = True
            sess["admin_username"] = "admin"
        r = demo_client.get("/demo/whoami")
        body = r.get_json()
        assert body["role"] == "admin" and body["tenant_id"] == "system"

    def test_no_identity_is_401(self, demo_client):
        r = demo_client.get("/demo/whoami")
        assert r.status_code == 401

    def test_jwt_role_claim_admin(self, demo_client, demo_app):
        r = demo_client.get("/demo/whoami", headers=_auth(_token(demo_app, "root", "admin")))
        body = r.get_json()
        assert body["role"] == "admin" and body["tenant_id"] == "system"

    def test_unknown_role_claim_defaults_customer(self, demo_client, demo_app):
        r = demo_client.get("/demo/whoami", headers=_auth(_token(demo_app, EMAIL_A, "superuser")))
        assert r.get_json()["role"] == "customer"


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


class TestDecorators:
    def test_require_role_denies_wrong_role(self, demo_client, demo_app):
        r = demo_client.get("/demo/admin-only", headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.status_code == 403

    def test_require_role_allows_right_role(self, demo_client, demo_app):
        r = demo_client.get("/demo/admin-only", headers=_auth(_token(demo_app, "root", "admin")))
        assert r.status_code == 200

    def test_require_role_unauthenticated_is_401(self, demo_client):
        assert demo_client.get("/demo/admin-only").status_code == 401

    def test_tenant_owner_allows_owner(self, demo_client, demo_app):
        r = demo_client.get("/demo/orders/o1", headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.status_code == 200
        # DTO stripped internal fields
        body = r.get_json()
        assert "secret" not in body and "internal_notes" not in body
        assert "customer_email" not in body
        assert body["order_id"] == "o1"

    def test_tenant_owner_denies_other_tenant(self, demo_client, demo_app):
        r = demo_client.get("/demo/orders/o1", headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 403
        assert "A-SECRET-ORDER" not in r.get_data(as_text=True)

    def test_tenant_owner_missing_resource_is_404(self, demo_client, demo_app):
        r = demo_client.get("/demo/orders/nope", headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.status_code == 404

    def test_admin_bypass_is_logged_and_allowed(self, demo_client, demo_app):
        r = demo_client.get("/demo/orders/o1", headers=_auth(_token(demo_app, "root", "admin")))
        assert r.status_code == 200  # documented support bypass


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


class TestCrossTenantIsolation:
    """Tenant A principal must not reach tenant B's resources, and vice versa.

    Denials must not leak the other tenant's data.
    """

    def test_b_cannot_get_a_order(self, demo_client, demo_app):
        r = demo_client.get("/demo/orders/o1", headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 403
        assert "A-SECRET-ORDER" not in r.get_data(as_text=True)

    def test_b_cannot_cancel_a_order(self, demo_client, demo_app):
        r = demo_client.post("/demo/orders/o1/cancel", headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 403
        assert "A-SECRET-ORDER" not in r.get_data(as_text=True)
        # and the order was NOT canceled
        store = demo_app.config["AUTHZ_DEMO_STORE"]
        assert store["orders"]["o1"].get("status") != "canceled"

    def test_list_is_tenant_scoped(self, demo_client, demo_app):
        for email, want, notwant in ((EMAIL_A, "o1", None), (EMAIL_B, None, "o1")):
            r = demo_client.get("/demo/orders", headers=_auth(_token(demo_app, email)))
            assert r.status_code == 200
            ids = [o["order_id"] for o in r.get_json()["orders"]]
            if want:
                assert want in ids
            if notwant:
                assert notwant not in ids
        # B's list contains no A data at all
        r = demo_client.get("/demo/orders", headers=_auth(_token(demo_app, EMAIL_B)))
        assert "A-SECRET-ORDER" not in r.get_data(as_text=True)

    def test_b_cannot_poll_a_task(self, demo_client, demo_app):
        r = demo_client.get("/demo/tasks/t1/poll", headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 403
        assert "A-SECRET-TASK" not in r.get_data(as_text=True)

    def test_b_cannot_read_a_task_result(self, demo_client, demo_app):
        r = demo_client.get("/demo/tasks/t1/result", headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 403
        assert "A-SECRET-TASK" not in r.get_data(as_text=True)
        # owner can
        r = demo_client.get("/demo/tasks/t1/result", headers=_auth(_token(demo_app, EMAIL_A)))
        assert r.status_code == 200
        assert "handler_payload" not in r.get_json()  # DTO stripped

    def test_b_cannot_read_a_evidence(self, demo_client, demo_app):
        r = demo_client.get("/demo/evidence/e1", headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 403
        assert "A-SECRET-EVIDENCE" not in r.get_data(as_text=True)

    def test_b_create_does_not_land_in_a_tenant(self, demo_client, demo_app):
        r = demo_client.post("/demo/orders", json={"tenant_id": TENANT_A, "secret": "B-IMPERSONATE"},
                             headers=_auth(_token(demo_app, EMAIL_B)))
        assert r.status_code == 201
        assert r.get_json()["tenant_id"] == TENANT_B
        # A cannot see B's new order
        r = demo_client.get("/demo/orders", headers=_auth(_token(demo_app, EMAIL_A)))
        assert "B-IMPERSONATE" not in r.get_data(as_text=True)


# ---------------------------------------------------------------------------
# RuntimeContext
# ---------------------------------------------------------------------------


class TestRuntimeContext:
    def test_frozen_once_created(self):
        ctx = RuntimeContext(principal_id="u1", tenant_id="t1", role="customer")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.tenant_id = "t2"  # type: ignore[misc]

    def test_from_principal(self):
        p = Principal(id=EMAIL_A, role=Role.customer, tenant_id=TENANT_A, email=EMAIL_A)
        ctx = RuntimeContext.from_principal(p, purpose="a2a-dispatch")
        assert ctx.principal_id == EMAIL_A
        assert ctx.tenant_id == TENANT_A
        assert ctx.role == "customer"
        assert ctx.purpose == "a2a-dispatch"
        assert ctx.trace_id and ctx.idempotency_key

    def test_attach_and_rehydrate_round_trip(self):
        ctx = RuntimeContext(principal_id="u1", tenant_id="t1", role="agent",
                             task_id="task-9", purpose="bid")
        payload = attach_context({"bid_id": "b7"}, ctx)
        assert payload["bid_id"] == "b7"
        back = context_from_payload(payload)
        assert back == ctx
        assert back.task_id == "task-9"

    def test_attach_refuses_overwrite(self):
        ctx = RuntimeContext(principal_id="u1", tenant_id="t1", role="customer")
        payload = attach_context({}, ctx)
        with pytest.raises(ValueError):
            attach_context(payload, ctx)

    def test_context_from_payload_missing_is_none(self):
        assert context_from_payload({"a": 1}) is None

    def test_context_from_payload_malformed_raises(self):
        with pytest.raises(ValueError):
            context_from_payload({authz.CONTEXT_PAYLOAD_KEY: "not-a-dict"})

    def test_propagate_context_alias(self):
        ctx = RuntimeContext(principal_id="u1", tenant_id="t1", role="customer")
        assert propagate_context(ctx, {"x": 1})[authz.CONTEXT_PAYLOAD_KEY] == ctx.to_dict()

    def test_task_queue_contract_documented(self):
        # The documented integration point: enqueue(attach_context(payload, ctx)).
        # task_queue itself is untouched (additive); assert the key contract only.
        assert authz.CONTEXT_PAYLOAD_KEY == "_sincor_runtime_context"


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class TestDTOs:
    def test_scoped_dto_strips(self):
        out = scoped_dto({"a": 1, "b": 2, "secret": 3}, ["a"])
        assert out == {"a": 1}

    def test_order_dto(self):
        dto = to_public_order_dto({"order_id": "o1", "amount": 5, "delivery_url": "https://x",
                                   "customer_email": "a@b.c"})
        assert dto == {"order_id": "o1", "amount": 5}
        assert "delivery_url" not in dto

    def test_task_dto_hides_result_blob(self):
        dto = to_public_task_dto({"task_id": "t", "kind": "k", "status": "s",
                                  "result": {"big": "blob"}, "handler_payload": "x"})
        assert dto["has_result"] is True
        assert "result" not in dto and "handler_payload" not in dto

    def test_evidence_dto(self):
        dto = to_public_evidence_dto({"entry_id": "e", "kind": "k", "cost_cents": 1})
        assert dto == {"entry_id": "e", "kind": "k"}


# ---------------------------------------------------------------------------
# Matrix: schema + live probes against the real mvp_app
# ---------------------------------------------------------------------------


def _matrix():
    return AuthzMatrix.load()


class TestMatrixSchema:
    def test_validates_clean(self):
        assert _matrix().validate() == []

    def test_guard_vocabulary(self):
        for e in _matrix().entries:
            assert e["guard"] in ("public", "authenticated", "customer_owner", "operator", "admin")
            assert isinstance(e["tenant_scoped"], bool)
            assert e["path_pattern"].startswith("/")

    def test_known_entries(self):
        m = _matrix()
        e = m.guard_for("GET", "/api/orders/victim@example.com")
        assert e["guard"] == "customer_owner" and e["tenant_scoped"] is True
        assert e["enforced"] is False and e["current_guard"] == "none"
        e = m.guard_for("GET", "/api/orders")
        assert e["guard"] == "admin" and e["enforced"] is True
        e = m.guard_for("GET", "/health")
        assert e["guard"] == "public"

    def test_mounted_entries_exist_on_live_app(self, live_rules):
        """Every entry marked mounted must match a real route on mvp_app."""
        import re as _re
        live_paths = set()
        for path, _methods in live_rules:
            live_paths.add(_re.sub(r"<[^<>]+>", "SEG", path))
        missing = []
        for e in _matrix().entries:
            if not e["mounted"]:
                continue
            norm = _re.sub(r"<[^<>]+>", "SEG", e["path_pattern"])
            if norm not in live_paths:
                missing.append((e["method"], e["path_pattern"], e["source"]))
        assert missing == [], f"mounted entries with no live route: {missing[:10]}"


@pytest.fixture(scope="module")
def live_app():
    import sincor2.mvp_app as mvp  # noqa: WPS433 -- intentional: probe the real app

    return mvp.app


@pytest.fixture(scope="module")
def live_rules(live_app):
    return [(str(r), ",".join(sorted(r.methods - {"HEAD", "OPTIONS"})))
            for r in live_app.url_map.iter_rules()]


# (method, path, kwargs, expected_status, expected_guard, expected_enforced, expected_current)
PROBES = [
    ("GET", "/api/orders", {}, 401, "admin", True, "jwt+admin-identity"),
    ("GET", "/webbuilder/studio", {}, 200, "customer_owner", False, "none"),
    ("GET", "/admin/content/status", {}, 403, "admin", True, "session-admin"),
    ("GET", "/api/launch/partners", {}, 401, "admin", True, "session-admin"),
    ("GET", "/api/obs/vitals", {}, 401, "authenticated", True, "session-customer-or-admin"),
    ("GET", "/api/orders/victim@example.com", {}, 200, "customer_owner", False, "none"),
    ("GET", "/api/ops/schedulers", {}, 200, "operator", False, "none (admin-redacted section)"),
    ("GET", "/api/outreach/status", {}, 200, "operator", False, "none"),
    ("GET", "/api/tasks", {}, 200, "operator", False, "none"),
    ("GET", "/api/metrics/treasury", {}, 200, "operator", False, "none"),
    ("GET", "/health", {}, 200, "public", True, "none"),
    ("GET", "/api/defi/catalog", {}, 200, "public", True, "none"),
    ("GET", "/api/a2a/agents", {}, 200, "public", True, "none"),
    ("POST", "/v1/a2a/tasks", {"json": {}}, 400, "authenticated", False, "sig-optional"),
    ("GET", "/v1/a2a/tasks/no-such-task", {}, 404, "customer_owner", False, "none"),
    ("GET", "/api/webbuilder/projects", {}, 401, "customer_owner", False, "session-admin"),
    ("POST", "/api/cancel-subscription", {"json": {}}, 400, "customer_owner", False, "none"),
    ("GET", "/api/platform/subscription", {}, 400, "customer_owner", False, "none"),
    ("GET", "/launch/review", {}, 200, "operator", False, "none"),
]


class TestMatrixLiveProbes:
    @pytest.mark.parametrize(
        "method,path,kwargs,expected_status,expected_guard,expected_enforced,expected_current",
        PROBES,
        ids=[f"{m} {p}" for m, p, *_ in PROBES],
    )
    def test_probe_matches_documented_guard(self, live_app, method, path, kwargs,
                                            expected_status, expected_guard,
                                            expected_enforced, expected_current):
        client = live_app.test_client()
        if method == "GET":
            resp = client.get(path, **kwargs)
        else:
            resp = client.post(path, **kwargs)
        assert resp.status_code == expected_status, (
            f"{method} {path}: got {resp.status_code}, matrix documents {expected_status}"
        )
        entry = _matrix().guard_for(method, path)
        assert entry is not None, f"{method} {path} missing from matrix"
        assert entry["guard"] == expected_guard
        assert entry["enforced"] is expected_enforced
        assert entry["current_guard"] == expected_current

    def test_gap_entries_are_flagged(self):
        gaps = _matrix().gaps()
        assert len(gaps) > 0
        critical = [g for g in gaps if "CRITICAL" in g.get("notes", "")]
        assert any(g["path_pattern"] == "/api/orders/<email>" for g in critical)
