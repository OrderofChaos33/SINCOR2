"""Discovery Step 0 — card scrub + Smithery prep + Bazaar top-level extension.

Three precise items (branch xioix/card-scrub-bazaar):

1. CARD SCRUB: the live agent card's ``market-forecast`` example carried a
   speculative token-price claim ("Forecast SINC token price trajectory
   given the $0.15 floor and volume."). The token is platform access, NOT
   an investment — no price-floor language may appear anywhere in the
   serialized agent card, and the catalogue stays at 17 skills.

2. SMITHERY PREP: ``smithery.yaml`` exists at the repo root and describes
   the REAL MCP server (src/sincor2/mcp_server.py): stdio transport,
   ``python -m sincor2.mcp_server``, PYTHONPATH=src, and the server's one
   config env var SINCOR_MCP_BASE_URL.

3. BAZAAR TOP LEVEL: payload-level ``extensions.bazaar`` already lands on
   every 402 via build_payment_payload (dev-watch 317), but the HTTP 402
   response BODY carried it only nested under ``payment``/``accepts``.
   The x402 discovery shape puts ``extensions`` at the TOP LEVEL of the
   402 body (sibling of ``accepts``). The billing blueprint's single
   paid-surface after_request hook now mirrors it there for every
   paid-surface 402 (challenge + wallet-less discovery offer).
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

WALLET = "0x" + "11" * 20
RESOURCE = "wallet-sanctions-screen"


# ---------------------------------------------------------------------------
# 1. Card scrub
# ---------------------------------------------------------------------------

def _base_skills():
    from sincor2.a2a_integration import SINCOR_SKILLS

    by_id = {s.id: s for s in SINCOR_SKILLS}
    assert "market-forecast" in by_id
    return by_id


def test_market_forecast_example_has_no_price_floor_claim():
    skill = _base_skills()["market-forecast"]
    joined = " ".join(skill.examples)
    assert "$0.15" not in joined
    assert not re.search(r"\bfloor\b", joined, re.IGNORECASE), joined
    assert skill.examples, "market-forecast must keep worked examples"
    for ex in skill.examples:
        assert len(ex.strip()) >= 20, f"example too thin: {ex!r}"


def test_serialized_agent_card_has_no_price_floor_language():
    from sincor2.a2a_integration import build_agent_card

    blob = json.dumps(dataclasses.asdict(build_agent_card()))
    assert "$0.15" not in blob
    assert "0.15 floor" not in blob
    assert not re.search(r"price\s+floor", blob, re.IGNORECASE)
    assert not re.search(r"\bfloor\b[^.]{0,40}\b(price|token)\b", blob,
                         re.IGNORECASE)


def test_card_still_has_17_skills_and_market_forecast_intact():
    from sincor2.a2a_integration import SINCOR_SKILLS, build_agent_card

    card = build_agent_card()
    assert len(card.skills) == 17
    ids = [s.id for s in SINCOR_SKILLS]
    assert len(ids) == len(set(ids))
    mf = next(s for s in card.skills if s.id == "market-forecast")
    assert mf.name and len(mf.description.strip()) >= 40
    assert mf.input_schema and mf.output_schema


# ---------------------------------------------------------------------------
# 2. Smithery prep
# ---------------------------------------------------------------------------

def test_smithery_yaml_at_root_matches_mcp_server():
    path = ROOT / "smithery.yaml"
    assert path.is_file(), "smithery.yaml missing at repo root"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    start = cfg["startCommand"]
    assert start["type"] == "stdio"
    schema = start["configSchema"]
    assert schema["type"] == "object"
    # The server's ONLY required-at-runtime config is its base URL env;
    # it must be optional in the schema (server has a default).
    assert not schema.get("required")
    assert "baseUrl" in schema["properties"]
    fn = start["commandFunction"]
    assert "sincor2.mcp_server" in fn
    assert "SINCOR_MCP_BASE_URL" in fn
    assert "PYTHONPATH" in fn and "src" in fn
    # Cross-check against the server module itself: stdio entry + env name.
    server_src = (ROOT / "src" / "sincor2" / "mcp_server.py").read_text(
        encoding="utf-8")
    assert 'os.environ.get("SINCOR_MCP_BASE_URL"' in server_src
    assert "newline-delimited" in server_src  # stdio transport, as declared


# ---------------------------------------------------------------------------
# 3. Bazaar declaration at the TOP LEVEL of HTTP 402 bodies
# ---------------------------------------------------------------------------

@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    import sincor2.x402_payments as x402

    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def paid_client():
    import sincor2.mvp_app  # noqa: F401  (import order matters)
    from flask import Flask

    from sincor2.mvp_blueprints import billing as billing_mod

    app = Flask("card_scrub_bazaar_toplevel")
    app.register_blueprint(billing_mod.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _assert_toplevel_bazaar(body):
    from sincor2.x402_payments import validate_bazaar_extension

    ext = body.get("extensions")
    assert ext is not None, f"402 body has no TOP-LEVEL extensions: {sorted(body)}"
    assert validate_bazaar_extension(ext) == []
    # Top level must mirror the envelope's extension exactly — one shape,
    # no drift between the nested and top-level copies.
    assert ext == body["payment"]["extensions"]
    assert ext == body["accepts"][0]["extensions"]
    info_input = ext["bazaar"]["info"]["input"]
    assert info_input["type"] == "http"
    body_schema = ext["bazaar"]["schema"]["properties"]["input"][
        "properties"]["body"]
    assert "address" in body_schema["properties"]  # wallet-sanctions-screen


def test_walletless_offer_402_has_toplevel_bazaar(paid_client, x402_db):
    resp = paid_client.get(f"/api/paid/{RESOURCE}")
    assert resp.status_code == 402
    _assert_toplevel_bazaar(resp.get_json())


def test_challenge_402_has_toplevel_bazaar(paid_client, x402_db):
    resp = paid_client.get(f"/x402/{RESOURCE}?wallet={WALLET}")
    assert resp.status_code == 402
    _assert_toplevel_bazaar(resp.get_json())
