"""DeFi catalog UI tests: /catalog page + GET /api/defi/catalog.

Read-only surface: the catalog page renders, the JSON API returns all 26
SKUs with honest stages/scores/evidence, ?stage= filters, and no write
method is accepted.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def arm_data(tmp_path, monkeypatch):
    """Isolated DeFi-arm data dir: one SKU at test + one proof entry."""
    state = {
        "SINCOR-DEFI-P01-VAULT": {"stage": "test", "version": "0.1.1"},
    }
    ledger = {
        "entries": [
            {
                "entry_id": "ev_test0001",
                "sku": "SINCOR-DEFI-P01-VAULT",
                "kind": "test_run",
                "timestamp": 1790000000.0,
                "commit": "deadbeef",
                "recorded_by": "pytest",
                "details": {"suite": "tests/pytest/test_x.py", "passed": 1},
            }
        ]
    }
    (tmp_path / "products_state.json").write_text(json.dumps(state))
    (tmp_path / "proof_ledger.json").write_text(json.dumps(ledger))
    monkeypatch.setenv("SINCOR_DEFI_ARM_DATA_DIR", str(tmp_path))
    return tmp_path


def _get(client, arm_data, path, **kw):
    return client.get(path, **kw)


def test_catalog_api_returns_all_26_skus(client, arm_data):
    r = client.get("/api/defi/catalog")
    assert r.status_code == 200
    body = r.get_json()
    assert body["count"] == 26
    assert len(body["skus"]) == 26
    skus = {s["sku"] for s in body["skus"]}
    assert "SINCOR-DEFI-P01-VAULT" in skus


def test_catalog_api_item_shape(client, arm_data):
    body = client.get("/api/defi/catalog").get_json()
    p01 = next(s for s in body["skus"] if s["sku"] == "SINCOR-DEFI-P01-VAULT")
    for key in ("sku", "name", "protocol_id", "category", "stage", "version",
                "compliance_score", "evidence_count", "evidence", "pricing_status"):
        assert key in p01, key
    assert p01["stage"] == "test"
    assert p01["category"] == "yield"
    assert p01["evidence_count"] == 1
    assert p01["evidence"][0]["entry_id"] == "ev_test0001"
    assert p01["evidence"][0]["suite"] == "tests/pytest/test_x.py"
    assert isinstance(p01["compliance_score"], (int, float))
    # untouched SKUs stay honest: build stage (reference implementation
    # exists in repo), no evidence, no fake numbers
    other = next(s for s in body["skus"] if s["sku"] != "SINCOR-DEFI-P01-VAULT")
    assert other["stage"] == "build"
    assert other["evidence_count"] == 0
    assert other["evidence"] == []


def test_catalog_api_stage_filter(client, arm_data):
    body = client.get("/api/defi/catalog?stage=test").get_json()
    assert body["count"] == 1
    assert all(s["stage"] == "test" for s in body["skus"])
    body = client.get("/api/defi/catalog?stage=SPEC").get_json()
    # Fixture seeds P01 at test; every product with a real implementation
    # (all 26 post-integration, per gates.IMPLEMENTATIONS) starts at build,
    # so nothing is left at spec in a fresh registry.
    assert body["count"] == 0
    assert body["skus"] == []
    body = client.get("/api/defi/catalog?stage=build").get_json()
    assert body["count"] == 25
    assert all(s["stage"] == "build" for s in body["skus"])


def test_catalog_api_unknown_stage_is_empty(client, arm_data):
    body = client.get("/api/defi/catalog?stage=live").get_json()
    assert body["count"] == 0
    assert body["skus"] == []


def test_catalog_api_get_only(client, arm_data):
    # The app's global error handler maps 405s to 500s (pre-existing behavior,
    # same for POST /agents). What matters: writes are rejected, nothing mutates.
    before = client.get("/api/defi/catalog").get_json()
    for method in ("post", "put", "delete", "patch"):
        r = getattr(client, method)("/api/defi/catalog")
        assert r.status_code != 200, method
    after = client.get("/api/defi/catalog").get_json()
    assert after == before


def test_catalog_page_renders(client, arm_data):
    r = client.get("/catalog")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "DeFi product catalog" in html
    assert "/api/defi/catalog" in html
    assert 'href="/catalog"' in html  # sidebar nav link
    # honest framing: no live-product language anywhere on the page
    assert "spec-stage entries are design" in html.lower() or "design\n  specifications" in html


def test_catalog_page_on_mvp_app(arm_data):
    import os

    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    r = mvp_app.test_client().get("/catalog")
    assert r.status_code == 200
    assert "/api/defi/catalog" in r.get_data(as_text=True)
    r = mvp_app.test_client().get("/api/defi/catalog?stage=test")
    assert r.status_code == 200
    assert r.get_json()["count"] == 1
