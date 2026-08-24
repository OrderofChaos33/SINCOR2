"""Deterministic demo scenarios for the schema gate and canonical addresses.

Used by the Flask blueprint and by tests. Drives the real engines.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from sincor2.onchain.constants import (
    AXIOM_TOKEN,
    SINC_TOKEN,
    STALE_ADDRESSES,
    TREASURY,
    assert_live_not_stale,
    catalog,
    is_stale,
    resolve_address,
)
from sincor2.onchain.probe import encode_abi_string, encode_uint, validate_at_startup
from sincor2.schema_gate import compile_skill_schemas, validate_skill_input

LEAD_SCHEMA = {
    "type": "object",
    "required": ["company"],
    "properties": {
        "company": {"type": "string"},
        "segment": {"type": "string"},
        "enrichment_fields": {"type": "array", "items": {"type": "string"}},
    },
}

SBOM_SCHEMA = {
    "type": "object",
    "required": ["repository"],
    "properties": {
        "repository": {"type": "string"},
        "format": {"type": "string", "enum": ["spdx", "cyclonedx"]},
    },
}

FORECAST_SCHEMA = {
    "type": "object",
    "required": ["subject", "horizon"],
    "properties": {
        "subject": {"type": "string"},
        "horizon": {"type": "string"},
    },
}

DEMO_SKILLS = [
    {"id": "lead-enrichment", "input_schema": LEAD_SCHEMA},
    {"id": "compliance-sbom", "input_schema": SBOM_SCHEMA},
    {"id": "market-forecast", "input_schema": FORECAST_SCHEMA},
    {"id": "healthcare-rcm", "input_schema": {}},
]


def _gate(skill_id: str, schema: Dict[str, Any], **kwargs: Any):
    return validate_skill_input(
        skill_id=skill_id,
        schema=schema or None,
        params=kwargs.get("params") or {},
        msg_obj=kwargs.get("msg_obj") or {},
        input_text=kwargs.get("input_text") or "",
    )


def run_schema_scenario() -> Dict[str, Any]:
    compile_skill_schemas(DEMO_SKILLS)
    freeform = _gate(
        "lead-enrichment",
        LEAD_SCHEMA,
        input_text="Enrich Acme Corp",
        msg_obj={"parts": [{"text": "Enrich Acme Corp"}]},
    )
    structured = _gate(
        "lead-enrichment",
        LEAD_SCHEMA,
        params={"data": {"company": "Acme Corp", "segment": "enterprise"}},
    )
    missing = _gate(
        "lead-enrichment",
        LEAD_SCHEMA,
        params={"data": {"segment": "enterprise"}},
    )
    type_error = _gate(
        "lead-enrichment",
        LEAD_SCHEMA,
        params={"data": {"company": 42}},
    )
    enum_error = _gate(
        "compliance-sbom",
        SBOM_SCHEMA,
        params={"data": {"repository": "OrderofChaos33/SINCOR2", "format": "pdf"}},
    )
    pollution = _gate(
        "lead-enrichment",
        LEAD_SCHEMA,
        params={"data": {"company": "Acme", "__proto__": {"admin": True}}},
    )
    multi = _gate(
        "market-forecast",
        FORECAST_SCHEMA,
        input_text="Forecast SINC volume",
        msg_obj={"parts": [{"text": "Forecast SINC volume"}]},
    )
    vertical = _gate(
        "healthcare-rcm",
        {},
        input_text='{"task_type":"eligibility_verification","payload":{"patient_id":"P-A2A"}}',
        msg_obj={
            "parts": [
                {
                    "text": '{"task_type":"eligibility_verification","payload":{"patient_id":"P-A2A"}}'
                }
            ]
        },
    )
    return {
        "engine": "schema_gate",
        "compiled": list(s["id"] for s in DEMO_SKILLS),
        "freeform": freeform.to_dict(),
        "structured": structured.to_dict(),
        "missing": missing.to_dict(),
        "type_error": type_error.to_dict(),
        "enum_error": enum_error.to_dict(),
        "pollution": pollution.to_dict(),
        "multi_required_freeform": multi.to_dict(),
        "vertical_empty_schema": vertical.to_dict(),
        "invalid_params_code": -32602,
    }


def run_address_scenario() -> Dict[str, Any]:
    assert_live_not_stale()
    stale_ignored = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
    malformed_ignored = None
    previous = os.environ.get("SINC_CONTRACT_ADDRESS")
    os.environ["SINC_CONTRACT_ADDRESS"] = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
    try:
        stale_ignored = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
        os.environ["SINC_CONTRACT_ADDRESS"] = "not-an-address"
        malformed_ignored = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
        os.environ["SINC_CONTRACT_ADDRESS"] = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
        live_override = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
    finally:
        if previous is None:
            os.environ.pop("SINC_CONTRACT_ADDRESS", None)
        else:
            os.environ["SINC_CONTRACT_ADDRESS"] = previous
    return {
        "catalog": catalog(),
        "stale_env_ignored": stale_ignored == SINC_TOKEN,
        "malformed_env_ignored": malformed_ignored == SINC_TOKEN,
        "live_override_honored": live_override.lower()
        == "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a",
        "stale_set": sorted(STALE_ADDRESSES),
        "axiom": AXIOM_TOKEN,
        "sinc": SINC_TOKEN,
        "treasury": TREASURY,
        "retired_sinc_is_stale": is_stale("0x9C8cd8d3961F445D653713dE65C6578bE11668e7"),
    }


def _matching_eth_call(to: str, data: str) -> str:
    selector = (data or "")[:10].lower()
    addr = (to or "").lower()
    if addr == AXIOM_TOKEN.lower():
        if selector == "0x95d89b41":
            return encode_abi_string("AXM")
        if selector == "0x313ce567":
            return encode_uint(18)
    if addr == SINC_TOKEN.lower():
        if selector == "0x95d89b41":
            return encode_abi_string("SINC")
        if selector == "0x313ce567":
            return encode_uint(8)
    raise RuntimeError(f"unexpected call {to} {data}")


def _mismatch_eth_call(to: str, data: str) -> str:
    selector = (data or "")[:10].lower()
    if selector == "0x95d89b41":
        return encode_abi_string("FAKE")
    if selector == "0x313ce567":
        return encode_uint(18)
    raise RuntimeError("unexpected selector")


def run_probe_scenario() -> Dict[str, Any]:
    offline = validate_at_startup(rpc_url="")
    matching = validate_at_startup(eth_call=_matching_eth_call)
    mismatch = validate_at_startup(eth_call=_mismatch_eth_call)
    return {
        "offline": offline.to_dict(),
        "matching": matching.to_dict(),
        "mismatch": mismatch.to_dict(),
    }


def run_all() -> Dict[str, Any]:
    return {
        "schema": run_schema_scenario(),
        "addresses": run_address_scenario(),
        "probe": run_probe_scenario(),
    }


def rpc_invalid_params(gate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32602,
            "message": f"Invalid params: {len(gate.get('errors') or [])} schema error(s) "
            f"for skill '{gate.get('skill_id')}'",
            "data": {
                "skillId": gate.get("skill_id"),
                "source": gate.get("source"),
                "errors": gate.get("errors") or [],
            },
        },
    }
