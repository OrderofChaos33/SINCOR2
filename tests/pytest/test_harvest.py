"""
tests/pytest/test_harvest.py — Harvest Moon claim API test suite.

Covers:
  - /api/harvest/status
  - /api/harvest/eligibility
  - /api/harvest/claim
  - /api/harvest/confirm
  - /api/harvest/conversion_event
  - HARVEST_PAGE_ENABLED kill switch
  - Double-claim prevention
  - Missing / invalid address handling
  - DB record persistence
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def proofs_file(tmp_path: Path):
    """Write a minimal proofs.json and return its path."""
    data = {
        "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B": {
            "amount": "100000000000000000000",   # 100 SINC in wei
            "amount_ether": "100.0",
            "proof": ["0xdeadbeef" * 4 + "00000000"],
        },
        "0x1111111111111111111111111111111111111111": {
            "amount": "200000000000000000000",
            "amount_ether": "200.0",
            "proof": ["0xbeefdead" * 4 + "00000000"],
        },
    }
    p = tmp_path / "proofs.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


@pytest.fixture()
def db_file(tmp_path: Path):
    return str(tmp_path / "harvest_test.db")


@pytest.fixture()
def app(proofs_file, db_file, monkeypatch):
    monkeypatch.setenv("HARVEST_PROOFS_PATH", proofs_file)
    monkeypatch.setenv("HARVEST_DB_PATH", db_file)
    monkeypatch.setenv("HARVEST_PAGE_ENABLED", "true")
    monkeypatch.setenv("HARVEST_CONTRACT_ADDRESS", "0x1234567890abcdef1234567890abcdef12345678")

    # Reset proof cache between tests
    import sincor2.blueprints.harvest as hmod
    hmod._PROOF_CACHE = None

    try:
        from sincor2.app import create_app
    except ImportError:
        pytest.skip("sincor2.app not importable in this environment")

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        with app.app_context():
            from sincor2.blueprints.harvest import init_harvest_db
            import os
            init_harvest_db(os.environ.get("HARVEST_DB_PATH"))
            yield c


# ─────────────────────────────────────────────────────────────────────────────
# /api/harvest/status
# ─────────────────────────────────────────────────────────────────────────────

class TestHarvestStatus:
    def test_status_returns_200(self, client):
        r = client.get("/api/harvest/status")
        assert r.status_code == 200

    def test_status_contains_keys(self, client):
        data = r = client.get("/api/harvest/status").get_json()
        assert "claim_window_open" in data
        assert "total_claims" in data
        assert "eligible_count" in data

    def test_status_eligible_count_matches_proofs(self, client):
        data = client.get("/api/harvest/status").get_json()
        assert data["eligible_count"] == 2

    def test_status_disabled_when_kill_switch(self, client, monkeypatch):
        monkeypatch.setenv("HARVEST_PAGE_ENABLED", "false")
        r = client.get("/api/harvest/status")
        assert r.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# /api/harvest/eligibility
# ─────────────────────────────────────────────────────────────────────────────

class TestEligibility:
    VALID_ADDR = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    UNKNOWN_ADDR = "0x0000000000000000000000000000000000000001"

    def test_eligible_address(self, client):
        r = client.get(f"/api/harvest/eligibility?address={self.VALID_ADDR}")
        assert r.status_code == 200
        data = r.get_json()
        assert data["eligible"] is True
        assert data["already_claimed"] is False

    def test_unknown_address_not_eligible(self, client):
        r = client.get(f"/api/harvest/eligibility?address={self.UNKNOWN_ADDR}")
        assert r.status_code == 200
        data = r.get_json()
        assert data["eligible"] is False

    def test_missing_address_param(self, client):
        r = client.get("/api/harvest/eligibility")
        assert r.status_code == 400

    def test_case_insensitive_lookup(self, client):
        r = client.get(f"/api/harvest/eligibility?address={self.VALID_ADDR.lower()}")
        assert r.status_code == 200
        data = r.get_json()
        assert data["eligible"] is True

    def test_disabled_when_kill_switch(self, client, monkeypatch):
        monkeypatch.setenv("HARVEST_PAGE_ENABLED", "false")
        r = client.get(f"/api/harvest/eligibility?address={self.VALID_ADDR}")
        assert r.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# /api/harvest/claim
# ─────────────────────────────────────────────────────────────────────────────

class TestClaimInitiation:
    VALID_ADDR = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    UNKNOWN_ADDR = "0x0000000000000000000000000000000000000001"

    def _post(self, client, address):
        return client.post(
            "/api/harvest/claim",
            json={"address": address},
            content_type="application/json",
        )

    def test_valid_claim_returns_proof(self, client):
        r = self._post(client, self.VALID_ADDR)
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "proof" in data
        assert isinstance(data["proof"], list)
        assert "amount_wei" in data

    def test_not_eligible_address_returns_403(self, client):
        r = self._post(client, self.UNKNOWN_ADDR)
        assert r.status_code == 403

    def test_missing_address_returns_400(self, client):
        r = client.post("/api/harvest/claim", json={})
        assert r.status_code == 400

    def test_double_claim_returns_409(self, client):
        # First claim
        r1 = self._post(client, self.VALID_ADDR)
        assert r1.status_code == 200
        # Manually mark as claimed_onchain
        from sincor2.blueprints.harvest import _get_db
        with client.application.test_request_context():
            db = _get_db()
            db.execute(
                "UPDATE harvest_claims SET claimed_onchain=1 WHERE lower(address)=?",
                (self.VALID_ADDR.lower(),),
            )
            db.commit()
        # Second attempt
        r2 = self._post(client, self.VALID_ADDR)
        assert r2.status_code == 409
        assert r2.get_json()["error"] == "already_claimed"

    def test_eligibility_false_after_claim(self, client):
        self._post(client, self.VALID_ADDR)
        # Mark on-chain
        from sincor2.blueprints.harvest import _get_db
        with client.application.test_request_context():
            db = _get_db()
            db.execute(
                "UPDATE harvest_claims SET claimed_onchain=1 WHERE lower(address)=?",
                (self.VALID_ADDR.lower(),),
            )
            db.commit()
        r = client.get(f"/api/harvest/eligibility?address={self.VALID_ADDR}")
        data = r.get_json()
        assert data["already_claimed"] is True
        assert data["eligible"] is False

    def test_disabled_when_kill_switch(self, client, monkeypatch):
        monkeypatch.setenv("HARVEST_PAGE_ENABLED", "false")
        r = self._post(client, self.VALID_ADDR)
        assert r.status_code == 503


# ─────────────────────────────────────────────────────────────────────────────
# /api/harvest/confirm
# ─────────────────────────────────────────────────────────────────────────────

class TestConfirm:
    VALID_ADDR = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"

    def test_confirm_after_claim(self, client):
        # Serve proof first
        client.post("/api/harvest/claim", json={"address": self.VALID_ADDR})
        # Confirm
        r = client.post(
            "/api/harvest/confirm",
            json={"address": self.VALID_ADDR, "tx_hash": "0xabc123"},
        )
        assert r.status_code == 200
        assert r.get_json()["success"] is True

    def test_confirm_unknown_address_returns_404(self, client):
        r = client.post(
            "/api/harvest/confirm",
            json={"address": "0x9999999999999999999999999999999999999999", "tx_hash": "0x1"},
        )
        assert r.status_code == 404

    def test_confirm_missing_fields_returns_400(self, client):
        r = client.post("/api/harvest/confirm", json={"address": self.VALID_ADDR})
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# /api/harvest/conversion_event
# ─────────────────────────────────────────────────────────────────────────────

class TestConversionEvent:
    VALID_ADDR = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"

    def test_log_conversion_event(self, client):
        r = client.post(
            "/api/harvest/conversion_event",
            json={
                "address": self.VALID_ADDR,
                "event_type": "plan_upgrade",
                "metadata": {"plan": "pro"},
            },
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["success"] is True
        assert "event_id" in data

    def test_missing_fields_returns_400(self, client):
        r = client.post(
            "/api/harvest/conversion_event",
            json={"address": self.VALID_ADDR},
        )
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Harvest route
# ─────────────────────────────────────────────────────────────────────────────

class TestHarvestPage:
    def test_harvest_route_enabled(self, client):
        r = client.get("/harvest")
        assert r.status_code == 200

    def test_early_route_alias(self, client):
        r = client.get("/early")
        assert r.status_code == 200

    def test_harvest_route_disabled(self, client, monkeypatch):
        monkeypatch.setenv("HARVEST_PAGE_ENABLED", "false")
        r = client.get("/harvest")
        assert r.status_code == 404
