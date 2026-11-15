"""Bazaar discovery extension on package 402 challenges.

Coinbase's x402 Bazaar indexes a paid endpoint only when its 402
declares ``extensions.bazaar`` (info.input + info.output + the schema
validating info) AND a paid call settles through the CDP Facilitator.
The settle side is out of scope here; this suite pins the declaration
side:

* ``build_bazaar_extension`` emits the documented shape and the
  package's own validator accepts it;
* a ``VerifiedEndpoint`` configured with extensions advertises them
  verbatim on its 402 challenge (and on the accepts-mismatch 402),
  while the payment terms (accepts[] / escrow) stay byte-identical to
  the no-extensions challenge;
* an endpoint WITHOUT extensions serves the exact legacy challenge —
  discovery stays opt-in per endpoint;
* the validator rejects the known failure shapes (schema missing,
  info.input missing, insane input-schema property types).
"""
import copy

import pytest

from sincor_x402 import (VerifiedEndpoint, build_bazaar_extension,
                         validate_bazaar_extension)

from x402pkg_helpers import good_work, make_payment_payload, payment_header

BODY_SCHEMA = {
    "type": "object",
    "properties": {
        "address": {"type": "string"},
        "window_minutes": {"type": "integer"},
    },
    "required": ["address"],
}
EXAMPLE_BODY = {
    "address": "0x0000000000000000000000000000000000000000",
    "window_minutes": 60,
}
OUTPUT_EXAMPLE = {"address": "0x0000000000000000000000000000000000000000",
                  "is_contract": False}


def _extension():
    return build_bazaar_extension(
        method="POST", body_schema=BODY_SCHEMA,
        example_body=EXAMPLE_BODY, output_example=OUTPUT_EXAMPLE)


def _endpoint(requirement, platform, facilitator, **kwargs):
    return VerifiedEndpoint(
        good_work, requirement=requirement, spec={"required_keys": ["ok"]},
        platform=platform, facilitator=facilitator, **kwargs)


# ---------------------------------------------------------------------------
# Builder + validator
# ---------------------------------------------------------------------------

def test_builder_output_is_valid_and_spec_shaped():
    ext = _extension()
    assert validate_bazaar_extension(ext) == []
    bazaar = ext["bazaar"]
    info_input = bazaar["info"]["input"]
    assert info_input["type"] == "http"
    assert info_input["method"] == "POST"
    assert info_input["bodyType"] == "json"
    assert info_input["body"] == EXAMPLE_BODY
    assert bazaar["info"]["output"] == {
        "type": "json", "example": OUTPUT_EXAMPLE}
    schema = bazaar["schema"]
    assert schema["$schema"] == \
        "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert "input" in schema["required"]
    input_schema = schema["properties"]["input"]
    # The resource's real body schema is embedded, not a placeholder.
    assert input_schema["properties"]["body"] == BODY_SCHEMA
    assert set(input_schema["required"]) == {
        "type", "method", "bodyType", "body"}
    # No external $ref/$id anywhere (spec: same-document fragments only).
    assert "$ref" not in str(schema) and "$id" not in str(schema)


def test_example_body_validates_against_embedded_schema():
    bazaar = _extension()["bazaar"]
    body_schema = bazaar["schema"]["properties"]["input"][
        "properties"]["body"]
    for key in body_schema.get("required", []):
        assert key in EXAMPLE_BODY
    for key, value in EXAMPLE_BODY.items():
        ptype = body_schema["properties"][key]["type"]
        if ptype == "string":
            assert isinstance(value, str)
        elif ptype == "integer":
            assert isinstance(value, int)


def test_builder_defaults_to_empty_body_declaration():
    ext = build_bazaar_extension()
    assert validate_bazaar_extension(ext) == []
    assert ext["bazaar"]["info"]["input"]["body"] == {}


def test_builder_rejects_non_object_body_schema():
    with pytest.raises(ValueError):
        build_bazaar_extension(body_schema={"type": "array"})


def test_validator_rejects_schemaless_extension():
    ext = _extension()
    del ext["bazaar"]["schema"]  # the SWARM failure shape, exactly
    assert any("schema" in e for e in validate_bazaar_extension(ext))


def test_validator_rejects_missing_info_input():
    ext = _extension()
    del ext["bazaar"]["info"]["input"]
    assert any("input" in e for e in validate_bazaar_extension(ext))


def test_validator_rejects_insane_property_type():
    ext = _extension()
    ext["bazaar"]["schema"]["properties"]["input"]["properties"][
        "body"]["properties"]["address"] = {"type": "banana"}
    assert any("banana" in e for e in validate_bazaar_extension(ext))


def test_validator_rejects_body_method_without_body():
    ext = _extension()
    del ext["bazaar"]["info"]["input"]["body"]
    assert validate_bazaar_extension(ext)


# ---------------------------------------------------------------------------
# Challenge wiring
# ---------------------------------------------------------------------------

def test_challenge_carries_extensions_verbatim(
        requirement, platform, facilitator):
    ext = _extension()
    endpoint = _endpoint(requirement, platform, facilitator,
                         extensions=copy.deepcopy(ext))
    resp = endpoint.challenge()
    assert resp.status == 402
    assert resp.body["extensions"] == ext
    assert validate_bazaar_extension(resp.body["extensions"]) == []


def test_challenge_payment_terms_unchanged_by_extensions(
        requirement, platform, facilitator):
    plain = _endpoint(requirement, platform, facilitator)
    with_ext = _endpoint(requirement, platform, facilitator,
                         extensions=_extension())
    plain_body = plain.challenge().body
    ext_body = with_ext.challenge().body
    # The load-bearing terms are byte-identical; extensions are additive.
    assert ext_body["accepts"] == plain_body["accepts"]
    assert ext_body["escrow"] == plain_body["escrow"]
    assert ext_body["x402Version"] == plain_body["x402Version"]
    assert ext_body["error"] == plain_body["error"]


def test_challenge_without_extensions_is_the_legacy_shape(
        requirement, platform, facilitator):
    endpoint = _endpoint(requirement, platform, facilitator)
    body = endpoint.challenge().body
    assert "extensions" not in body
    assert set(body) == {"x402Version", "error", "accepts", "escrow"}


def test_accepts_mismatch_402_also_carries_extensions(
        requirement, platform, facilitator):
    ext = _extension()
    endpoint = _endpoint(requirement, platform, facilitator,
                         extensions=copy.deepcopy(ext))
    claimed = requirement.to_accepts_dict()
    claimed["maxAmountRequired"] = "1"  # tampered price -> mismatch 402
    header = payment_header(make_payment_payload(requirement,
                                                 claimed=claimed))
    resp = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert resp.status == 402
    assert resp.body["error"] == "accepts_mismatch"
    assert resp.body["extensions"] == ext
    assert facilitator.calls == 0  # still rejected before any settlement
