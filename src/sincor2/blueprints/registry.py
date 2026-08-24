"""Additive Registry API: A2A schema gate + canonical on-chain addresses."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from marketplace.registry_sim import (
    DEMO_SKILLS,
    rpc_invalid_params,
    run_address_scenario,
    run_all,
    run_probe_scenario,
    run_schema_scenario,
)
from sincor2.onchain.constants import catalog
from sincor2.onchain.probe import validate_at_startup
from sincor2.schema_gate import engine_name, validate_skill_input

registry_bp = Blueprint("registry", __name__, url_prefix="/api/registry")


@registry_bp.get("/health")
def health():
    live = catalog()
    return jsonify(
        {
            "ok": True,
            "surface": "registry",
            "engine": engine_name(),
            "axiom": live["axiom"]["address"],
            "sinc": live["sinc"]["address"],
            "treasury": live["treasury"]["address"],
            "skills": [s["id"] for s in DEMO_SKILLS],
        }
    )


@registry_bp.get("/catalog")
def catalog_view():
    return jsonify({"catalog": catalog(), "scenario": run_address_scenario()})


@registry_bp.get("/skills")
def skills():
    return jsonify({"skills": DEMO_SKILLS, "engine": engine_name()})


@registry_bp.post("/validate")
def validate():
    body = request.get_json(silent=True) or {}
    skill_id = str(body.get("skillId") or body.get("skill_id") or "")
    schema = body.get("schema")
    if schema is None:
        match = next((s for s in DEMO_SKILLS if s["id"] == skill_id), None)
        schema = (match or {}).get("input_schema") or {}
    params = body.get("params") or {}
    msg_obj = body.get("message") or body.get("msg_obj") or {}
    input_text = str(body.get("input_text") or body.get("inputText") or "")
    if not input_text and isinstance(msg_obj, dict):
        input_text = " ".join(
            part.get("text", "")
            for part in (msg_obj.get("parts") or [])
            if isinstance(part, dict)
        )
    gate = validate_skill_input(
        skill_id=skill_id,
        schema=schema or None,
        params=params if isinstance(params, dict) else {},
        msg_obj=msg_obj,
        input_text=input_text,
    )
    payload = gate.to_dict()
    if schema and not gate.ok:
        payload["rpc"] = rpc_invalid_params(payload)
    return jsonify(payload)


@registry_bp.post("/probe")
def probe():
    body = request.get_json(silent=True) or {}
    rpc_url = body.get("rpc_url")
    report = validate_at_startup(rpc_url=rpc_url or "")
    return jsonify(report.to_dict())


@registry_bp.post("/simulate")
def simulate():
    which = (request.get_json(silent=True) or {}).get("scenario") or "all"
    if which == "schema":
        return jsonify(run_schema_scenario())
    if which in ("addresses", "catalog"):
        return jsonify(run_address_scenario())
    if which == "probe":
        return jsonify(run_probe_scenario())
    return jsonify(run_all())
