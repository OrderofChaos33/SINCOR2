"""AP2 discovery document — GET /.well-known/ap2.json (and /.well-known/ap2).

The bare root alias GET /ap2 serves the exact same document (verified
byte-identical) so resolvers that try the short path do not hit a 404.

Mirrors the agent-card pattern in test_a2a_smoke.py: the document is
served by the A2A Flask blueprint (a2a_integration.A2ARouter) as
jsonify(factory()) and must return HTTP 200 with a JSON content type.

Honesty contract (from the staged document in ap2-seal-adapter and its
INTEGRATION.md): the SincorSettlementHub is NOT deployed on Base
mainnet, the fee canon is 500 bps, and third-party market figures are
UNVERIFIED — none of that may be upgraded by serving the document.
Per INTEGRATION.md step 2, the one field that changes on a
founder-approved deploy is "status": it may no longer claim the
document is staged / NOT deployed once it is in fact being served.
"""

USDC_ON_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _assert_ap2_document(payload):
    assert payload is not None
    assert payload.get("protocol") == "ap2"
    assert payload.get("issuer") == "SINCOR Business Solutions"
    assert payload.get("discovery_url_intended") == (
        "https://getsincor.com/.well-known/ap2.json"
    )
    assert set(payload.get("mandates_accepted") or []) == {
        "IntentMandate",
        "CartMandate",
        "PaymentMandate",
    }
    settlement = payload.get("settlement") or {}
    # The honesty fields: hub is NOT on mainnet, fee canon is locked.
    assert settlement.get("hub_deployed_on_mainnet") is False
    assert settlement.get("fee_bps") == 500
    assert settlement.get("fee_bps_is_canon") is True
    assert settlement.get("chain_id") == 8453
    assert settlement.get("network") == "base"
    assert settlement.get("asset") == "USDC"
    assert settlement.get("asset_address") == USDC_ON_BASE
    assert settlement.get("scheme") == "sincor-seal-v1"
    assert settlement.get("eip712_domain") == {
        "name": "SINCOR_SEAL",
        "version": "1",
    }
    assert payload.get("verification_enforced_offchain") is True
    assert "UNVERIFIED" in (payload.get("market_claims") or "")
    sigil = payload.get("sigil") or {}
    assert sigil.get("commitment") == "keccak256(taskId || canonical-JSON output)"
    # Status must be truthful for a served document: no longer staged/DRAFT,
    # and it must not claim the settlement hub is deployed.
    status = payload.get("status") or ""
    assert not status.startswith("DRAFT")
    assert "NOT deployed" not in status


def test_ap2_discovery_json_endpoint(client):
    response = client.get("/.well-known/ap2.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    _assert_ap2_document(response.get_json())


def test_ap2_discovery_bare_endpoint(client):
    """The founder-greenlit path /.well-known/ap2 serves the same document."""
    response = client.get("/.well-known/ap2")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    _assert_ap2_document(response.get_json())


def test_ap2_discovery_factory_matches_served_document(client):
    from sincor2.a2a_integration import build_ap2_discovery_document

    served = client.get("/.well-known/ap2.json").get_json()
    assert build_ap2_discovery_document() == served


def test_ap2_bare_root_alias_serves_identical_document(client):
    """GET /ap2 serves the same document as GET /.well-known/ap2.

    Byte-identical body (not just equal JSON) so the alias cannot drift
    from the canonical document it mirrors.
    """
    canonical = client.get("/.well-known/ap2")
    assert canonical.status_code == 200
    alias = client.get("/ap2")
    assert alias.status_code == 200
    assert alias.mimetype == "application/json"
    assert alias.data == canonical.data
    _assert_ap2_document(alias.get_json())
