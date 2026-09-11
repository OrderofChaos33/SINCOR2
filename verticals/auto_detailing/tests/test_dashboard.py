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
