"""Shop dashboard routes — no network."""

from __future__ import annotations

import pytest


@pytest.fixture
def chroma_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DEMO", "true")
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma.db"))
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    from verticals.auto_detailing.seed import seed
    from verticals.auto_detailing.store import reset_store

    store = reset_store(tmp_path / "chroma.db")
    seed(store=store, reset=True)
    from sincor2.chroma_app import create_chroma_app

    app = create_chroma_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health(chroma_client):
    r = chroma_client.get("/chroma/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["live_send"] is False
    assert body["leads"] >= 10


def test_five_pages_render(chroma_client):
    for path in (
        "/chroma/",
        "/chroma/quotes",
        "/chroma/bookings",
        "/chroma/outreach",
        "/chroma/settings",
        "/chroma/books",
    ):
        r = chroma_client.get(path)
        assert r.status_code == 200, path
        html = r.get_data(as_text=True)
        assert len(html) > 400
        assert "Clinton Auto Detailing" in html



def test_logout_clears_chroma_and_admin_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DEMO", "false")
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma.db"))
    monkeypatch.setenv("ADMIN_USERNAME", "shopowner")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    from verticals.auto_detailing.store import reset_store
    from sincor2.chroma_app import create_chroma_app

    reset_store(tmp_path / "chroma.db")
    app = create_chroma_app()
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-not-shared"
    client = app.test_client()
    login = client.post(
        "/chroma/login",
        data={"identifier": "shopowner", "password": "correct-horse"},
        follow_redirects=False,
    )
    assert login.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess.get("chroma_shop") == "shopowner"
        assert sess.get("admin_username") == "shopowner"
    out = client.get("/chroma/logout", follow_redirects=False)
    assert out.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert "chroma_shop" not in sess
        assert "admin_username" not in sess
        assert "is_admin" not in sess
    blocked = client.get("/chroma/leads", follow_redirects=False)
    assert blocked.status_code in (302, 303)
    assert "/chroma/login" in (blocked.headers.get("Location") or "")


def test_login_uses_chroma_template_not_platform(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_DEMO", "false")
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma.db"))
    monkeypatch.delenv("CHROMA_LIVE_SEND", raising=False)
    from verticals.auto_detailing.store import reset_store
    from sincor2.chroma_app import create_chroma_app

    reset_store(tmp_path / "chroma.db")
    app = create_chroma_app()
    app.config["TESTING"] = True
    r = app.test_client().get("/chroma/login")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Open the bay board" in html
    assert "CHROMA" in html


def test_approve_stays_dry_run(chroma_client):
    from verticals.auto_detailing.store import get_store

    store = get_store()
    pending = store.list_outbound("pending_approval")
    assert pending
    item_id = pending[0]["id"]
    r = chroma_client.post(f"/chroma/outreach/{item_id}/approve", follow_redirects=True)
    assert r.status_code == 200
    updated = store.get_outbound(item_id)
    assert updated["status"] == "approved_dry_run"
    assert updated["live"] is False


def test_books_logs_money_in(chroma_client):
    from verticals.auto_detailing.store import get_store

    r = chroma_client.post(
        "/chroma/books",
        data={"direction": "in", "amount": "125", "method": "cash", "note": "Walk-in wash"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Walk-in wash" in html
    totals = get_store().books_totals()
    assert totals["money_in"] >= 125


def test_lead_has_shop_actions_not_pipeline(chroma_client):
    r = chroma_client.get("/chroma/leads/LD-AVA001")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Run the pipeline" not in html
    assert "Price this job" in html
    assert "Queue a text" in html
    assert "Put it on the books" in html


def test_quote_then_paid_on_lead(chroma_client):
    from verticals.auto_detailing.store import get_store

    r = chroma_client.post(
        "/chroma/leads/LD-JEN003/quote",
        data={"package_id": "express_wash"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Priced at" in html
    before = get_store().books_totals()["money_in"]
    r = chroma_client.post(
        "/chroma/leads/LD-JEN003/paid",
        data={"amount": "79", "method": "cash"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert get_store().books_totals()["money_in"] >= before + 79
    lead = get_store().get_lead("LD-JEN003")
    assert lead["status"] == "paid"


def test_queue_text_needs_quote_first(chroma_client):
    r = chroma_client.post("/chroma/leads/LD-PAT006/text", follow_redirects=True)
    assert r.status_code == 200
    assert "Price the job first" in r.get_data(as_text=True)
