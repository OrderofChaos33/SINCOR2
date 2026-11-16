"""VENOM page tests (founder-ordered deploy, 2026-11-16).

/venom serves the VENOM security-review arm's landing page
(templates/venom.html): the approved page export with the
founder-supplied banner inlined at the top as a base64 data URI.
These tests pin the route, the banner, and the page's CTAs.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def client():
    from sincor2.mvp_app import app

    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture(scope="module")
def venom_html(client):
    resp = client.get("/venom")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_venom_page_serves(client):
    resp = client.get("/venom")
    assert resp.status_code == 200
    assert "VENOM" in resp.get_data(as_text=True)


def test_venom_page_has_founder_banner(venom_html):
    assert 'alt="VENOM Security by SINCOR Business Solutions"' in venom_html
    assert "data:image/jpeg;base64," in venom_html


def test_venom_page_keeps_approved_content(venom_html):
    assert "<video" in venom_html  # hero video, inlined in the export
    assert "Coverage" in venom_html
    # CTAs are pre-filled emails to the founder's inbox
    assert "court@getsincor.com" in venom_html
    assert len(venom_html) > 1_000_000  # the full page, not a stub
