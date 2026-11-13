"""Lane 3 — "Break the Zeros" site features (2026-11-12).

Covers:
* GET /x402-live, GET /register, GET /audit-express → 200 on the
  production (mvp) app, with the proof wall's sourcing rules honored.
* POST /ap2/register → validation 400s, storage through the existing
  registration path, and the first-20 introductory-bounty flag + pool
  ledger note (21st external registration is NOT flagged).
* POST /x402-live/quick-scan → the sincor-x402 package's 402 challenge
  shape; paid-but-unconfigured → 402 facilitator_gated, work never runs;
  paid with the package's local reference facilitator (test-only) →
  the real verified flow returns a scan result.
* POST /audit-express/order → same challenge pattern at the 500 USDC
  entry tier.
"""
from __future__ import annotations

import json

import pytest

from sincor2 import lane3

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
BUYER = "0x1111111111111111111111111111111111111111"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mvp_app():
    from sincor2.mvp_app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture(scope="module")
def mvp_client(mvp_app):
    return mvp_app.test_client()


@pytest.fixture(autouse=True)
def _no_facilitator(monkeypatch):
    """Default deployment state: no x402 facilitator configured."""
    monkeypatch.delenv("SINCOR_X402_FACILITATOR_URL", raising=False)
    lane3._ENDPOINT_CACHE.clear()
    yield
    lane3._ENDPOINT_CACHE.clear()


@pytest.fixture
def fabric_isolated():
    """Snapshot the shared agent fabric and restore it after the test,
    so registration ordinals are deterministic regardless of suite
    order."""
    from sincor2.a2a_inbound import _save_agents, get_fabric

    fabric = get_fabric()
    with fabric.lock:
        snapshot = {k: dict(v) for k, v in fabric.agents.items()}
    try:
        yield fabric
    finally:
        with fabric.lock:
            fabric.agents.clear()
            fabric.agents.update(snapshot)
        _save_agents(fabric)


@pytest.fixture
def tmp_pool(tmp_path, monkeypatch):
    """A real BountyPool on a throwaway path, wired in place of the
    module singleton so allocations land in an isolated ledger."""
    from sincor2.a2a_bounty_pool import BountyPool

    pool = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=1000.0)
    pool.fund()
    monkeypatch.setattr(
        "sincor2.a2a_bounty_pool.bounty_pool", lambda: pool)
    return pool


def _clear_externals(fabric):
    from sincor2.a2a_inbound import _save_agents

    with fabric.lock:
        for agent_id in [k for k, v in fabric.agents.items()
                         if v.get("origin") == "external"]:
            del fabric.agents[agent_id]
    _save_agents(fabric)


def _payment_header(accepted: dict, value: int) -> str:
    return json.dumps({
        "x402Version": 1,
        "scheme": "exact",
        "network": "base",
        "accepted": accepted,
        "payload": {
            "signature": "0x" + "ab" * 65,
            "authorization": {
                "from": BUYER,
                "to": TREASURY,
                "value": value,
                "validAfter": 0,
                "validBefore": 9999999999,
                "nonce": "0x" + "00" * 32,
            },
        },
    })


# ── pages ───────────────────────────────────────────────────────────────────

def test_x402_live_page_200(mvp_client):
    resp = mvp_client.get("/x402-live")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "as of 2026-11-12" in body
    # Sourced MoebiusX tallies: v3 69 + v2 12 = 81 confirmed trades.
    assert "81 confirmed trades" in body
    assert "69 successful" in body
    # The unsourceable mission figure must never appear.
    assert "79/79" not in body
    # Skill count rendered live from the agent card.
    from sincor2.a2a_integration import build_agent_card

    card = build_agent_card()
    assert f"{len(card.skills)} skills" in body
    assert card.skills[0].id in body
    # Module honesty markers + endorsement link.
    assert "not deployed" in body
    assert "https://x.com/D0xedDevi0" in body


def test_register_page_200(mvp_client):
    resp = mvp_client.get("/register")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/ap2/register" in body
    assert "First 20" in body


def test_audit_express_page_200(mvp_client):
    resp = mvp_client.get("/audit-express")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "48-hour" in body
    assert "500" in body and "1,500" in body
    assert "not a formal audit" in body.lower()
    # Banned language: no certification claims anywhere on the page.
    assert "certified" not in body.lower()


def test_lane3_routes_exist_on_production_app(mvp_app):
    rules = {(r.rule, m) for r in mvp_app.url_map.iter_rules()
             for m in r.methods}
    assert ("/x402-live", "GET") in rules
    assert ("/register", "GET") in rules
    assert ("/audit-express", "GET") in rules
    assert ("/ap2/register", "POST") in rules
    assert ("/x402-live/quick-scan", "POST") in rules
    assert ("/audit-express/order", "POST") in rules


# ── POST /ap2/register ──────────────────────────────────────────────────────

def _register_payload(**overrides):
    payload = {
        "name": "lane3-test-agent",
        "endpoint": "https://agents.example.com/lane3/a2a",
        "contact": "ops@example.com",
    }
    payload.update(overrides)
    return payload


def test_register_validation_errors(client, fabric_isolated):
    for payload in (
        {"endpoint": "https://a.example.com", "contact": "c@x.io"},
        {"name": "x", "contact": "c@x.io"},
        {"name": "x", "endpoint": "https://a.example.com"},
        _register_payload(endpoint="not-a-url"),
        _register_payload(endpoint="ftp://a.example.com/x"),
        _register_payload(name="   "),
    ):
        resp = client.post("/ap2/register", json=payload)
        assert resp.status_code == 400, payload
        assert resp.get_json()["ok"] is False


def test_register_success_storage_and_first20_flag(
        client, fabric_isolated, tmp_pool):
    _clear_externals(fabric_isolated)
    resp = client.post("/ap2/register", json=_register_payload())
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["ok"] is True
    agent_id = data["agent"]["agent_id"]
    assert data["agent"]["origin"] == "external"

    # Stored through the existing fabric/registration path.
    record = fabric_isolated.agents[agent_id]
    assert record["rpc_callback"] == "https://agents.example.com/lane3/a2a"
    assert record["origin"] == "external"
    assert "ops@example.com" in record["description"]

    # First external registration → flagged + ledger note via allocate().
    flag = data["intro_bounty"]
    assert flag["eligible"] is True
    assert flag["ordinal"] == 1
    assert flag["status"] == "reserved"
    assert flag["allocation_id"]
    assert record["intro_bounty"]["eligible"] is True
    allocations = tmp_pool.allocations_for_task(f"intro-bounty:{agent_id}")
    assert len(allocations) == 1
    verdict = tmp_pool.verify_ledger()
    assert verdict["inconclusive"] is None
    assert verdict["ok"], verdict


def test_register_21st_external_not_flagged(
        client, fabric_isolated, tmp_pool):
    _clear_externals(fabric_isolated)
    fabric = fabric_isolated
    from sincor2.a2a_inbound import _save_agents

    with fabric.lock:
        for i in range(20):
            fabric.agents[f"seed-ext-{i:02d}"] = {
                "agent_id": f"seed-ext-{i:02d}",
                "origin": "external",
                "registered_at": 1_000 + i,
                "name": f"seed {i}",
            }
    _save_agents(fabric)

    resp = client.post(
        "/ap2/register", json=_register_payload(name="lane3-late-agent"))
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["intro_bounty"]["eligible"] is False
    assert data["intro_bounty"]["ordinal"] == 21
    agent_id = data["agent"]["agent_id"]
    assert tmp_pool.allocations_for_task(f"intro-bounty:{agent_id}") == []


def test_register_flag_survives_unfunded_pool(
        client, fabric_isolated, monkeypatch):
    """Pool that cannot reserve → flag still lands, marked
    flagged_unfunded; no allocation is faked."""
    from sincor2.a2a_bounty_pool import InsufficientPoolFunds

    class _BrokePool:
        def allocate(self, **kwargs):
            raise InsufficientPoolFunds("empty")

    monkeypatch.setattr(
        "sincor2.a2a_bounty_pool.bounty_pool", lambda: _BrokePool())
    _clear_externals(fabric_isolated)
    resp = client.post(
        "/ap2/register", json=_register_payload(name="lane3-broke-agent"))
    assert resp.status_code == 201
    flag = resp.get_json()["intro_bounty"]
    assert flag["eligible"] is True
    assert flag["status"] == "flagged_unfunded"
    assert flag["allocation_id"] is None


# ── POST /x402-live/quick-scan ──────────────────────────────────────────────

def test_quick_scan_challenge_shape(client):
    resp = client.post("/x402-live/quick-scan", json={"address": USDC})
    assert resp.status_code == 402
    body = resp.get_json()
    assert body["x402Version"] == 1
    assert body["error"] == "payment_required"
    (accepts,) = body["accepts"]
    assert accepts["scheme"] == "exact"
    assert accepts["network"] == "base"
    assert accepts["maxAmountRequired"] == "10000"  # 0.01 USDC
    assert accepts["asset"] == USDC
    assert accepts["payTo"] == TREASURY


def test_quick_scan_paid_but_facilitator_gated(client):
    _, challenge = lane3.quick_scan({}, {"address": USDC})
    header = _payment_header(challenge["accepts"][0], 10000)
    resp = client.post(
        "/x402-live/quick-scan",
        json={"address": USDC},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 402
    body = resp.get_json()
    assert body["settlement"] == "facilitator_gated"
    assert body["error"] == "facilitator_not_configured"
    # The paid work must not have executed: no result envelope, and no
    # scan output fields at the top level. ("is_contract" legitimately
    # appears inside the challenge's escrow spec, so it is not checked.)
    assert "result" not in body
    assert "code_size_bytes" not in body
    assert "balance_wei" not in body


def test_quick_scan_paid_flow_with_reference_facilitator(monkeypatch):
    """Full verified flow using the package's LocalFacilitator — a
    local reference implementation, labelled as such; it is NOT
    mainnet settlement. The RPC transport is stubbed with public-state
    shaped responses."""
    sx = lane3._load_sincor_x402()
    monkeypatch.setattr(
        lane3, "_resolve_facilitator",
        lambda sx_: (sx.LocalFacilitator(), True))

    def fake_rpc(method, params):
        return {
            "eth_getCode": "0x60806040",
            "eth_getBalance": "0xde0b6b3a7640000",
            "eth_blockNumber": "0x100",
        }[method]

    monkeypatch.setattr(lane3, "_base_rpc", fake_rpc)

    _, challenge = lane3.quick_scan({}, {"address": USDC})
    header = _payment_header(challenge["accepts"][0], 10000)
    status, body = lane3.quick_scan(
        {"X-PAYMENT": header}, {"address": USDC})
    assert status == 200
    assert body["ok"] is True
    result = body["result"]
    assert result["is_contract"] is True
    assert result["is_usdc_contract"] is True
    assert result["chain_id"] == 8453
    assert result["block_number"] == 256


def test_quick_scan_rejects_underpayment(monkeypatch):
    """A payment header for the wrong amount fails the package's
    verification — 402, never a result."""
    sx = lane3._load_sincor_x402()
    monkeypatch.setattr(
        lane3, "_resolve_facilitator",
        lambda sx_: (sx.LocalFacilitator(), True))
    _, challenge = lane3.quick_scan({}, {"address": USDC})
    header = _payment_header(challenge["accepts"][0], 9999)
    status, body = lane3.quick_scan(
        {"X-PAYMENT": header}, {"address": USDC})
    assert status == 402
    assert body["ok"] is False
    assert "result" not in body


# ── POST /audit-express/order ───────────────────────────────────────────────

def test_audit_order_challenge_shape(client):
    resp = client.post("/audit-express/order", json={})
    assert resp.status_code == 402
    body = resp.get_json()
    assert body["x402Version"] == 1
    (accepts,) = body["accepts"]
    assert accepts["scheme"] == "exact"
    assert accepts["network"] == "base"
    assert accepts["maxAmountRequired"] == "500000000"  # 500 USDC entry
    assert accepts["asset"] == USDC
    assert accepts["payTo"] == TREASURY


def test_audit_order_paid_but_facilitator_gated(client):
    _, challenge = lane3.audit_order({}, {})
    header = _payment_header(challenge["accepts"][0], 500_000_000)
    resp = client.post(
        "/audit-express/order",
        json={"contact": "you@example.com"},
        headers={"X-PAYMENT": header},
    )
    assert resp.status_code == 402
    assert resp.get_json()["settlement"] == "facilitator_gated"
