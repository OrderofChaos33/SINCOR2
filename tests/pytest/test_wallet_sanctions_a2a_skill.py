"""Wallet Sanctions Screen as an A2A agent-card skill (micro-read).

The screening machinery already existed as the x402 paid resource
``wallet-sanctions-screen`` (sincor2.sanctions_screen, screened against
the vendored OFAC SDN snapshot). This file proves the A2A registration:

* the skill is listed in SINCOR_SKILLS and on the built agent card,
  priced as a micro-read in AXM (0.1 AXM — the card's currency; the
  x402 resource keeps its own separate USDC pricing);
* its implementation reference resolves to the real OFAC handler;
* the screening verdicts behind the skill are correct against a
  synthetic fresh snapshot (flagged hit, clean, bad address) and
  fail closed (unknown) on a stale snapshot;
* WIRED, not just unit-green: through the real A2ARouter Flask routes
  (the same serve path as every existing skill) the card lists it,
  the quote endpoint prices it at the registered micro price, and
  the free peek route serves its labelled metadata.
"""
from __future__ import annotations

import datetime

import pytest
from flask import Flask

import sincor2.sanctions_screen as ss
from sincor2.a2a_integration import (
    SINCOR_SKILLS,
    build_agent_card,
    skill_implementation_ref,
)
from sincor2.free_ration import DAILY_RATION_LIMIT

SKILL_ID = "wallet-sanctions-screen"
MICRO_PRICE_WEI = int(1 * 10**17)  # 0.1 AXM
FLAGGED_ADDR = "0x1111111111111111111111111111111111111111"
CLEAN_ADDR = "0x000000000000000000000000000000000000dEaD"


def _fresh_snapshot() -> dict:
    return {
        "source": "OFAC SDN List", "source_url": "https://x",
        "publication_date": "10/21/2026", "record_count": 1,
        "fetched_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "entry_count": 1,
        "entries": [{
            "uid": "999", "name": "TEST, Flagged", "type": "Individual",
            "programs": ["TESTPROG"],
            "digital_currency_addresses": [
                {"currency": "ETH", "address": FLAGGED_ADDR}],
            "aliases": [], "ids": [],
        }],
    }


def _skill():
    matches = [s for s in SINCOR_SKILLS if s.id == SKILL_ID]
    assert len(matches) == 1, "wallet-sanctions-screen must be registered once"
    return matches[0]


# ---------------------------------------------------------------------------
# Screening verdicts behind the skill (real OFAC handler, synthetic snapshot)
# ---------------------------------------------------------------------------

class TestScreenVerdicts:
    def test_flagged_hit(self):
        res = ss.screen_wallet(FLAGGED_ADDR, snapshot=_fresh_snapshot())
        assert res["ok"] is True
        assert res["verdict"] == "flagged"
        assert res["screened"] is True
        assert res["match"]["uid"] == "999"
        assert res["resource"] == SKILL_ID

    def test_clean(self):
        res = ss.screen_wallet(CLEAN_ADDR, snapshot=_fresh_snapshot())
        assert res["ok"] is True
        assert res["verdict"] == "clean"
        assert res["match"] is None

    def test_bad_address(self):
        for bad in ("", "0x123", "not-an-address", None, 42):
            res = ss.screen_wallet(bad, snapshot=_fresh_snapshot())
            assert res["ok"] is False, bad
            assert res["error"] == "invalid_address", bad

    def test_stale_snapshot_unknown_never_clean(self):
        snap = _fresh_snapshot()
        old = datetime.datetime.now(datetime.timezone.utc) - \
            datetime.timedelta(days=10)
        snap["fetched_at"] = old.isoformat()
        res = ss.screen_wallet(CLEAN_ADDR, snapshot=snap)
        assert res["verdict"] == "unknown"
        assert res["stale"] is True
        assert res["screened"] is False


# ---------------------------------------------------------------------------
# Registration: card listing, pricing, implementation reference
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_card_lists_skill_with_micro_price(self):
        card = build_agent_card().to_dict()
        entries = [s for s in card["skills"] if s["id"] == SKILL_ID]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "Wallet Sanctions Screen"
        # AXM micro price on the card: 0.1 AXM, below every agent-task
        # skill (cheapest is 1 AXM); no USDC price is advertised here.
        assert entry["axmPriceWei"] == str(MICRO_PRICE_WEI)
        assert entry["axmPriceDisplay"] == "0.1000 AXM"
        assert entry["sincPrice"] == 1
        assert "usdc" not in {k.lower() for k in entry}
        # Wave 1 hook convention: universal daily free ration applies.
        assert entry["freeDailyRation"] == DAILY_RATION_LIMIT
        assert entry["freeQuota"] == 0
        assert entry["quoteUrl"].endswith(f"skill_id={SKILL_ID}")
        assert entry["inputSchema"]["required"] == ["address"]

    def test_price_below_cheapest_task_skill(self):
        others = [s.axm_price_wei for s in SINCOR_SKILLS
                  if s.id != SKILL_ID and s.axm_price_wei > 0]
        # axiom-payment registers 0 in source but __post_init__ backfills
        # it to the 1 AXM default, so every other skill is >= 1 AXM.
        assert _skill().axm_price_wei == MICRO_PRICE_WEI
        assert _skill().axm_price_wei < min(others)

    def test_implementation_ref_resolves_to_ofac_handler(self):
        ref = skill_implementation_ref(SKILL_ID)
        assert ref == "sincor2.sanctions_screen:screen_wallet"
        import importlib
        module_name, _, qual = ref.partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, qual))


# ---------------------------------------------------------------------------
# Integration: reachable through the same A2A serve path as existing skills
# ---------------------------------------------------------------------------

@pytest.fixture()
def a2a_client():
    from sincor2.a2a_integration import A2ARouter

    app = Flask("sanctions_skill_a2a")
    app.register_blueprint(A2ARouter().blueprint)
    app.config.update(TESTING=True)
    return app.test_client()


class TestServePath:
    def test_agent_card_route_lists_skill(self, a2a_client):
        resp = a2a_client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.get_json()["skills"]]
        assert SKILL_ID in ids

    def test_quote_route_prices_skill_at_micro_price(self, a2a_client):
        resp = a2a_client.get(f"/api/a2a/quote?skill_id={SKILL_ID}")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["skill_id"] == SKILL_ID
        assert body["axm_price_wei"] == str(MICRO_PRICE_WEI)
        assert body["axm_price_display"] == "0.1000 AXM"
        assert body["primary_token"] == "AXIOM"
        assert body["free_daily_ration_limit"] == DAILY_RATION_LIMIT

    def test_peek_route_serves_labelled_metadata(self, a2a_client):
        resp = a2a_client.get(f"/api/a2a/peek/{SKILL_ID}")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["ok"] is True
        assert body["skill_id"] == SKILL_ID
        assert body["free"] is True
        assert body["peek_kind"] == "metadata"
        assert body["metadata"]["id"] == SKILL_ID
        assert body["metadata"]["axmPriceWei"] == str(MICRO_PRICE_WEI)
