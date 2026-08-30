from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from marketplace.registry_sim import (  # noqa: E402
    FORECAST_SCHEMA,
    LEAD_SCHEMA,
    SBOM_SCHEMA,
    run_all,
    run_schema_scenario,
)
from sincor2.schema_gate import (  # noqa: E402
    compile_schema,
    compile_skill_schemas,
    extract_payload,
    validate_skill_input,
)

import pytest  # noqa: E402


def test_subset_required_and_type() -> None:
    compiled = compile_schema(LEAD_SCHEMA)
    missing = compiled.iter_errors({"segment": "x"})
    assert any(e.validator == "required" for e in missing)
    wrong = compiled.iter_errors({"company": 9})
    assert any(e.validator == "type" for e in wrong)
    ok = compiled.iter_errors({"company": "Acme"})
    assert ok == []


def test_subset_enum_minmax_pattern() -> None:
    schema = {
        "type": "object",
        "required": ["n", "code", "tag"],
        "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 3},
            "code": {"type": "string", "pattern": "^[A-Z]{3}$"},
            "tag": {"type": "string", "enum": ["a", "b"]},
        },
    }
    compiled = compile_schema(schema)
    errors = compiled.iter_errors({"n": 0, "code": "no", "tag": "z"})
    names = {e.validator for e in errors}
    assert "minimum" in names
    assert "pattern" in names
    assert "enum" in names


def test_pollution_keys_rejected() -> None:
    compiled = compile_schema(LEAD_SCHEMA)
    errors = compiled.iter_errors({"company": "Acme", "__proto__": {"x": 1}})
    assert any(e.validator == "security" for e in errors)


def test_freeform_promotes_single_required_string() -> None:
    gate = validate_skill_input(
        skill_id="lead-enrichment",
        schema=LEAD_SCHEMA,
        params={},
        msg_obj={"parts": [{"text": "Enrich Acme Corp"}]},
        input_text="Enrich Acme Corp",
    )
    assert gate.ok
    assert gate.source == "message.parts.text.promoted"
    assert gate.payload == {"company": "Enrich Acme Corp"}


def test_multi_required_does_not_promote() -> None:
    gate = validate_skill_input(
        skill_id="market-forecast",
        schema=FORECAST_SCHEMA,
        params={},
        msg_obj={"parts": [{"text": "Forecast SINC"}]},
        input_text="Forecast SINC",
    )
    assert not gate.ok
    assert any(e.validator == "required" for e in gate.errors)


def test_data_part_and_params_data_win() -> None:
    payload, source = extract_payload(
        params={"data": {"company": "Globex"}},
        msg_obj={"parts": [{"kind": "data", "data": {"company": "ignored"}}]},
        input_text="ignored",
        schema=LEAD_SCHEMA,
    )
    assert source == "params.data"
    assert payload == {"company": "Globex"}

    payload, source = extract_payload(
        params={},
        msg_obj={"parts": [{"kind": "data", "data": {"company": "ViaPart"}}]},
        input_text="",
        schema=LEAD_SCHEMA,
    )
    assert source == "message.parts.data"
    assert payload["company"] == "ViaPart"


def test_json_text_parsed() -> None:
    raw = '{"company":"JsonCo"}'
    gate = validate_skill_input(
        skill_id="lead-enrichment",
        schema=LEAD_SCHEMA,
        params={},
        msg_obj={"parts": [{"text": raw}]},
        input_text=raw,
    )
    assert gate.ok
    assert gate.source == "message.parts.text.json"


def test_empty_schema_is_freeform() -> None:
    compile_skill_schemas([{"id": "healthcare-rcm", "input_schema": {}}])
    gate = validate_skill_input(
        skill_id="healthcare-rcm",
        schema=None,
        params={},
        msg_obj={"parts": [{"text": '{"task_type":"x"}'}]},
        input_text='{"task_type":"x"}',
    )
    assert gate.ok


def test_sbom_enum_rejects_pdf() -> None:
    gate = validate_skill_input(
        skill_id="compliance-sbom",
        schema=SBOM_SCHEMA,
        params={"data": {"repository": "a/b", "format": "pdf"}},
        msg_obj={},
        input_text="",
    )
    assert not gate.ok
    assert any(e.validator == "enum" for e in gate.errors)


def test_sim_scenario_flags() -> None:
    scenario = run_schema_scenario()
    assert scenario["freeform"]["ok"] is True
    assert scenario["structured"]["ok"] is True
    assert scenario["missing"]["ok"] is False
    assert scenario["type_error"]["ok"] is False
    assert scenario["pollution"]["ok"] is False
    assert scenario["multi_required_freeform"]["ok"] is False
    assert scenario["vertical_empty_schema"]["ok"] is True
    assert scenario["invalid_params_code"] == -32602


def test_run_all_has_three_surfaces() -> None:
    payload = run_all()
    assert set(payload) == {"schema", "addresses", "probe"}
    assert payload["probe"]["matching"]["ok"] is True
    assert payload["probe"]["mismatch"]["ok"] is False
    assert payload["addresses"]["stale_env_ignored"] is True


def _rpc_send(skill_id: str, message: dict, extra: dict | None = None) -> dict:
    import os

    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.a2a_integration import _handle_send

    params = {"skillId": skill_id, "callerId": "schema-test", "message": message}
    if extra:
        params.update(extra)
    return _handle_send({"jsonrpc": "2.0", "id": 7, "method": "message/send", "params": params})


def test_handle_send_freeform_lead_enrichment_still_works() -> None:
    body = _rpc_send("lead-enrichment", {"role": "user", "parts": [{"text": "Enrich Acme Corp"}]})
    assert "result" in body
    assert body["result"]["id"]


def test_handle_send_rejects_type_error_with_field_errors() -> None:
    body = _rpc_send(
        "lead-enrichment",
        {"role": "user", "parts": [{"kind": "data", "data": {"company": 123}}]},
    )
    err = body["error"]
    assert err["code"] == -32602
    assert err["data"]["skillId"] == "lead-enrichment"
    paths = {e["path"] for e in err["data"]["errors"]}
    assert any("company" in p for p in paths)


def test_handle_send_rejects_pollution() -> None:
    body = _rpc_send(
        "lead-enrichment",
        {"role": "user", "parts": [{"text": ""}]},
        extra={"data": {"company": "Acme", "constructor": {"prototype": {}}}},
    )
    err = body["error"]
    assert err["code"] == -32602
    assert any(e["validator"] == "security" for e in err["data"]["errors"])


def test_handle_send_structured_data_part_accepted() -> None:
    body = _rpc_send(
        "lead-enrichment",
        {"role": "user", "parts": [{"kind": "data", "data": {"company": "Globex"}}]},
    )
    assert "result" in body


def test_handle_send_unknown_skill_still_invalid_params() -> None:
    body = _rpc_send("nonexistent", {"parts": [{"text": "x"}]})
    assert body["error"]["code"] == -32602


def test_registry_blueprint_validate_and_simulate() -> None:
    flask = pytest.importorskip("flask")
    from sincor2.blueprints.registry import registry_bp

    app = flask.Flask(__name__)
    app.register_blueprint(registry_bp)
    client = app.test_client()
    resp = client.post(
        "/api/registry/validate",
        json={"skillId": "lead-enrichment", "params": {"data": {"company": 1}}},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert body["rpc"]["error"]["code"] == -32602
    health = client.get("/api/registry/health")
    assert health.status_code == 200
    assert health.get_json()["ok"] is True
    sim = client.post("/api/registry/simulate", json={"scenario": "schema"})
    assert sim.status_code == 200
    assert sim.get_json()["freeform"]["ok"] is True
