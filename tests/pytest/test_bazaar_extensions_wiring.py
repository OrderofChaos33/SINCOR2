"""Bazaar extensions wiring — 2026-11-13 audit + completion pass.

State of the surfaces before this pass:

* ``/x402/<resource>`` (sincor2.x402_payments): the extension was
  already attached by build_payment_payload (dev-watch 317), but the
  generated content had drifted from the current spec and from the
  handlers — the schema did not require bodyType/body (the spec's POST
  example does), healthcare-credential-check had NO input schema (a
  freeform object, so an agent could not construct its call), two
  resources lacked their handler enums, and the output examples were
  stubs ({"ok": True, "resource": id}) for every resource.
* Lane-3 endpoints (POST /x402-live/quick-scan, POST
  /audit-express/order, built on the in-repo sincor-x402 package): the
  package's challenge carried NO extensions at all.

This suite pins: every paid endpoint's unauthenticated 402 carries a
schema-valid extensions.bazaar (validated against the documented
shape), the example bodies validate against their declared schemas,
descriptions respect the CDP Facilitator's 500-char limit, the payment
fields of the challenges are untouched, and the recorded declarations
in docs/distribution/mcp-directories/bazaar-extensions.json equal the
code-generated ones (no doc/code drift).

Indexing itself additionally requires a paid call settled through the
CDP Facilitator (founder-gated) — out of scope here by design.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sincor2.x402_payments as x402

ROOT = Path(__file__).resolve().parents[2]
DOCS_DECLARATIONS = (
    ROOT / "docs" / "distribution" / "mcp-directories"
    / "bazaar-extensions.json")

#: The eight catalog resources from the 2026-10-26 prepared
#: declarations, with the output fields their serve handlers return.
CATALOG_OUTPUT_KEYS = {
    "wallet-sanctions-screen": {"address", "match", "status"},
    "kya-trust-score": {"score", "components"},
    "polymarket-whale-signal": {"signals", "status"},
    "toa-micro-decision": {"ranked_options", "confidence"},
    "market-data-read": {"pair", "price", "round_id", "stale"},
    "local-business-site-check": {"url", "verdict", "deficiencies"},
    "web-search-read": {"results", "count"},
    "healthcare-credential-check": {"npi_valid", "licence_status"},
}


def _payload(resource):
    return x402.build_payment_payload(
        resource, resource["id"], "bazaar-wiring-test")


# ---------------------------------------------------------------------------
# /x402 surface: every resource's 402 declaration is complete + valid
# ---------------------------------------------------------------------------

def test_every_resource_declaration_is_complete_and_valid():
    resources = x402.list_resources()
    assert resources
    for resource in resources:
        rid = resource["id"]
        payload = _payload(resource)
        ext = payload.get("extensions")
        assert ext is not None, f"{rid}: no extensions on 402 payload"
        assert x402.validate_bazaar_extension(ext) == [], rid
        bazaar = ext["bazaar"]
        info_input = bazaar["info"]["input"]
        assert info_input["type"] == "http"
        assert info_input["method"] == "POST"
        assert info_input["bodyType"] == "json"
        assert isinstance(info_input["body"], dict)
        # Output declaration: type + a real example, per the spec.
        info_output = bazaar["info"]["output"]
        assert info_output["type"] == "json"
        assert isinstance(info_output["example"], dict)
        assert info_output["example"], f"{rid}: empty output example"
        # The declared schema requires what the spec's POST example
        # requires.
        input_schema = bazaar["schema"]["properties"]["input"]
        assert set(input_schema["required"]) == {
            "type", "method", "bodyType", "body"}


def test_info_and_example_body_validate_against_declared_schemas():
    jsonschema = pytest.importorskip("jsonschema")
    for resource in x402.list_resources():
        bazaar = _payload(resource)["extensions"]["bazaar"]
        jsonschema.validate(bazaar["info"], bazaar["schema"])
        body_schema = bazaar["schema"]["properties"]["input"][
            "properties"]["body"]
        jsonschema.validate(bazaar["info"]["input"]["body"], body_schema)


def test_descriptions_fit_the_facilitator_limit():
    # CDP's Facilitator rejects verify/settle requests whose resource
    # description exceeds 500 chars — every challenge description must
    # stay under it so part (b) can ever settle.
    for resource in x402.list_resources():
        payload = _payload(resource)
        assert payload["description"], resource["id"]
        assert len(payload["description"]) <= 500, resource["id"]


def test_payment_fields_of_the_challenge_are_untouched():
    for resource in x402.list_resources():
        payload = _payload(resource)
        assert payload["x402Version"] == 1
        assert payload["scheme"] == "exact"
        assert payload["network"] == "base"
        assert payload["maxAmountRequired"] == resource["amount_atomic"]
        assert payload["payTo"] == resource["treasury"]
        assert payload["asset"] == resource["token_address"]
        assert payload["resource"] == f"/x402/{resource['id']}"


def test_catalog_resources_carry_real_output_examples():
    for rid, keys in CATALOG_OUTPUT_KEYS.items():
        resource = x402.get_resource(rid)
        assert resource is not None, f"{rid} missing from pricing config"
        example = _payload(resource)["extensions"]["bazaar"]["info"][
            "output"]["example"]
        missing = keys - set(example)
        assert not missing, f"{rid}: output example missing {missing}"


def test_healthcare_credential_check_has_a_real_input_schema():
    resource = x402.get_resource("healthcare-credential-check")
    bazaar = _payload(resource)["extensions"]["bazaar"]
    body_schema = bazaar["schema"]["properties"]["input"][
        "properties"]["body"]
    assert "provider_npi" in body_schema["properties"]
    assert "provider_npi" in body_schema["required"]
    example = bazaar["info"]["input"]["body"]
    assert example["provider_npi"] == "1234567890"


def test_handler_enums_are_declared():
    wsr = x402.bazaar_input_schema("web-search-read")
    assert wsr["properties"]["mode"]["enum"] == ["search", "read"]
    mdr = x402.bazaar_input_schema("market-data-read")
    assert mdr["properties"]["pair"]["enum"] == ["ETH/USD", "USDC/USD"]
    assert mdr["properties"]["mode"]["enum"] == ["price"]


# ---------------------------------------------------------------------------
# Lane-3 endpoints (sincor-x402 package): challenges now declare Bazaar
# ---------------------------------------------------------------------------

def _lane3():
    from sincor2 import lane3

    return lane3


def test_quick_scan_challenge_carries_valid_bazaar(monkeypatch):
    monkeypatch.delenv("SINCOR_X402_FACILITATOR_URL", raising=False)
    lane3 = _lane3()
    sx = lane3._load_sincor_x402()
    status, body = lane3.quick_scan({}, {})
    assert status == 402
    ext = body.get("extensions")
    assert ext is not None, "quick-scan 402 has no extensions"
    assert sx.validate_bazaar_extension(ext) == []
    assert x402.validate_bazaar_extension(ext) == []
    # Payment terms unchanged: the package's own accepts entry.
    accepts = body["accepts"][0]
    assert accepts["maxAmountRequired"] == lane3.QUICK_SCAN_AMOUNT_ATOMIC
    assert accepts["payTo"] == lane3.TREASURY_WALLET
    assert accepts["asset"] == sx.USDC_BASE_ADDRESS
    body_schema = ext["bazaar"]["schema"]["properties"]["input"][
        "properties"]["body"]
    assert "address" in body_schema["properties"]
    assert "address" in body_schema["required"]


def test_audit_order_challenge_carries_valid_bazaar(monkeypatch):
    monkeypatch.delenv("SINCOR_X402_FACILITATOR_URL", raising=False)
    lane3 = _lane3()
    sx = lane3._load_sincor_x402()
    status, body = lane3.audit_order({}, {})
    assert status == 402
    ext = body.get("extensions")
    assert ext is not None, "audit-express 402 has no extensions"
    assert sx.validate_bazaar_extension(ext) == []
    accepts = body["accepts"][0]
    assert accepts["maxAmountRequired"] == lane3.AUDIT_ORDER_AMOUNT_ATOMIC
    assert accepts["payTo"] == lane3.TREASURY_WALLET
    body_schema = ext["bazaar"]["schema"]["properties"]["input"][
        "properties"]["body"]
    assert {"contact", "repo"} <= set(body_schema["properties"])


def test_facilitator_gated_reserve_keeps_the_declaration(monkeypatch):
    # A payment-bearing call with no facilitator configured re-serves
    # the challenge with the facilitator_gated marker — the discovery
    # declaration must ride along unchanged.
    monkeypatch.delenv("SINCOR_X402_FACILITATOR_URL", raising=False)
    lane3 = _lane3()
    status, body = lane3.quick_scan({"X-Payment": "e30="}, {})
    assert status == 402
    assert body["settlement"] == "facilitator_gated"
    assert body.get("extensions") is not None
    sx = lane3._load_sincor_x402()
    assert sx.validate_bazaar_extension(body["extensions"]) == []


def test_lane3_descriptions_fit_the_facilitator_limit():
    lane3 = _lane3()
    for kind in ("quick-scan", "audit-order"):
        endpoint, _configured = lane3._get_endpoint(kind)
        assert endpoint.requirement.description
        assert len(endpoint.requirement.description) <= 500


# ---------------------------------------------------------------------------
# Recorded declarations agree with the code (no doc/code drift)
# ---------------------------------------------------------------------------

def test_docs_declarations_equal_code_generated():
    doc = json.loads(DOCS_DECLARATIONS.read_text(encoding="utf-8"))
    entries = doc["resources"]
    for rid in CATALOG_OUTPUT_KEYS:
        resource = x402.get_resource(rid)
        generated = _payload(resource)["extensions"]["bazaar"]
        entry = entries[rid]
        assert entry["bazaar"] == generated, f"{rid}: docs drifted"
        assert entry["description"] == resource["description"]
        assert entry["resource"] == f"https://getsincor.com/x402/{rid}"
        assert len(entry["description"]) <= 500
    lane3 = _lane3()
    for kind, key in (("quick-scan", "quick-scan"),
                      ("audit-order", "audit-express")):
        endpoint, _configured = lane3._get_endpoint(kind)
        served = endpoint.challenge().body["extensions"]["bazaar"]
        assert entries[key]["bazaar"] == served, f"{key}: docs drifted"
