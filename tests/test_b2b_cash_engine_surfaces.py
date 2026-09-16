import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(rel_path: str) -> dict:
    return json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8"))


def _load_text(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_compliance_example_agent_card_uses_getsincor_production_endpoints():
    card = _load_json("examples/agent_cards/compliance_agent.json")

    assert card["supportedInterfaces"][0]["url"] == "https://getsincor.com/api/a2a"
    assert card["provider"]["url"] == "https://getsincor.com"
    assert card["documentationUrl"] == "https://getsincor.com/docs/api"

    skill_ids = {skill["id"] for skill in card["skills"]}
    assert {"compliance-sbom", "compliance-filing", "n8n-workflow-bridge"} <= skill_ids


def test_static_agent_card_advertises_healthcare_and_compliance_skills():
    card = _load_json("static/.well-known/agent-card.json")
    skill_ids = {skill["id"] for skill in card["skills"]}
    assert "healthcare-credential-check" in skill_ids
    assert "compliance-sbom" in skill_ids


def test_buy_and_pricing_pages_expose_b2b_wallet_rail_and_priced_skus():
    buy_html = _load_text("templates/buy.html")
    pricing_html = _load_text("templates/pricing.html")

    assert "USDC on Base" in buy_html
    assert "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac" in buy_html
    assert "stripe=not_configured" in buy_html
    assert "Start paid inquiry" in buy_html

    for price_text in ("297", "997", "2,997"):
        assert price_text in pricing_html
    assert "credentialing" in pricing_html.lower()
    assert "compliance" in pricing_html.lower()
    assert "/buy#b2b-packs" in pricing_html
