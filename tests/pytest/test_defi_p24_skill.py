"""Tests for the P24 issue_creator_token agent skill (wave 11).

The skill is transport-injected, so error mapping is tested against a fake
transport speaking the exact POST /v1/a2a/socialfi/issue contract from the
w08 branch. One suite additionally runs the skill end-to-end against a Flask
test app carrying a stub route that implements that contract (status codes
and envelope shapes), proving the wiring works over real HTTP.
"""

from __future__ import annotations

import pytest
from flask import Flask, jsonify, request

from sincor2.a2a_sdk import SDKError
from sincor2.defi.p24 import skill as p24_skill
from sincor2.defi.p24.skill import (
    ISSUE_PATH,
    SKILL_ID,
    IssuanceSkillError,
    IssueResult,
    issue_creator_token,
)


# ---------------------------------------------------------------------------
# Fake transport speaking the w08 route contract
# ---------------------------------------------------------------------------

class FakeTransport:
    """Scripted transport: post() raises SDKError(status, body) or returns body."""

    def __init__(self):
        self.calls = []
        self.script = []  # list of ("raise", status, body) | ("return", body)

    def post(self, path, payload):
        self.calls.append((path, payload))
        kind = self.script.pop(0)
        if kind[0] == "raise":
            raise SDKError(kind[1], kind[2])
        return kind[1]

    def ok_201(self, **over):
        body = {
            "status": "issued",
            "creator_id": "agent-1",
            "agent_id": "agent-1",
            "name": "Auriga",
            "symbol": "AURIGA",
            "issued_at": "2026-09-30T00:00:00Z",
            "total_supply_wei": "1000000000000000000000000000",
            "policy_version": "1.0.0",
            "screened": True,
            "dry_run": True,
            "live_blocked": True,
        }
        body.update(over)
        self.script.append(("return", body))
        return self

    def err(self, status, body):
        self.script.append(("raise", status, body))
        return self


@pytest.fixture
def tx():
    return FakeTransport()


BASE_KW = {"agent_id": "agent-1", "name": "Auriga", "symbol": "AURIGA"}


# ---------------------------------------------------------------------------
# Happy path + SDK preference
# ---------------------------------------------------------------------------

def test_happy_path_dry_run_returns_symbol(tx):
    tx.ok_201()
    result = issue_creator_token(tx, **BASE_KW)
    assert isinstance(result, IssueResult)
    assert result.symbol == "AURIGA"
    assert result.dry_run is True
    assert result.policy_version == "1.0.0"
    path, payload = tx.calls[0]
    assert path == ISSUE_PATH
    assert payload["dry_run"] is True
    assert payload["agent_id"] == "agent-1"


def test_sdk_issue_method_preferred_over_post():
    """Once SincorAgentSDK grows issue_creator_token, the skill uses it."""
    seen = {}

    class FakeSDK:
        def issue_creator_token(self, **kw):
            seen.update(kw)
            return {
                "status": "issued", "creator_id": "c", "agent_id": "a",
                "name": "N", "symbol": "SYM", "issued_at": "t",
                "total_supply_wei": "1", "policy_version": "1.0.0",
                "screened": True, "dry_run": True, "live_blocked": True,
            }

    sdk = FakeSDK()
    result = issue_creator_token(sdk, agent_id="a", name="N", symbol="SYM")
    assert result.symbol == "SYM"
    assert seen["dry_run"] is True


def test_client_without_post_or_sdk_method_rejected():
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(object(), **BASE_KW)
    assert exc.value.code == "validation"


# ---------------------------------------------------------------------------
# Error mapping: clean skill errors, no metadata echo
# ---------------------------------------------------------------------------

def test_policy_denial_names_ruleset_version_and_never_echoes_text(tx):
    nasty = "guaranteed 10x price pump moon"
    tx.err(400, {
        "error": "content-policy violation",
        "status": 400,
        "field": "description",
        "matched_phrase": "price pump",
        "ruleset_version": "1.0.0",
    })
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW, description=nasty)
    err = exc.value
    assert err.code == "policy"
    assert err.ruleset_version == "1.0.0"
    assert "1.0.0" in str(err)
    assert nasty not in str(err), "submitted text must never be echoed"


def test_live_block_refusal_is_clear(tx):
    tx.err(403, {"error": "P24 is live-blocked", "status": 403})
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW, dry_run=False)
    assert exc.value.code == "live_blocked"
    assert "not live yet" in str(exc.value)
    assert "dry_run=True" in str(exc.value)


def test_unauthenticated_agent(tx):
    tx.err(401, {"error": "agent authentication required", "status": 401})
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW)
    assert exc.value.code == "auth"


def test_duplicate_creator(tx):
    tx.err(409, {"error": "creator already registered", "status": 409})
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW)
    assert exc.value.code == "duplicate"


def test_route_missing_degrades_honestly(tx):
    tx.err(404, {"error": "not found", "status": 404})
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW)
    assert exc.value.code == "route_missing"


def test_unexpected_response_shape_rejected(tx):
    tx.script.append(("return", {"status": "weird"}))
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW)
    assert exc.value.code == "server"


# ---------------------------------------------------------------------------
# Client-side shape validation (fail fast; policy stays server-side)
# ---------------------------------------------------------------------------

def test_bad_symbol_fails_before_any_http_call(tx):
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, agent_id="a", name="N", symbol="bad sym!")
    assert exc.value.code == "validation"
    assert tx.calls == []


def test_non_bool_dry_run_rejected(tx):
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(tx, **BASE_KW, dry_run="yes")
    assert exc.value.code == "validation"
    assert tx.calls == []


# ---------------------------------------------------------------------------
# Catalogue registration
# ---------------------------------------------------------------------------

def test_skill_registered_in_catalogue_with_valid_schemas():
    from sincor2.a2a_integration import SINCOR_SKILLS
    from sincor2.schema_gate import compile_schema

    entry = next(s for s in SINCOR_SKILLS if s.id == SKILL_ID)
    assert entry.name == "P24 Creator Token Issuance"
    assert entry.input_schema == p24_skill.CATALOG_INPUT_SCHEMA
    assert entry.output_schema == p24_skill.CATALOG_OUTPUT_SCHEMA
    compiled = compile_schema(dict(entry.input_schema))
    assert compiled is not None
    # dry-run-only honesty is advertised, not hidden
    assert "dry-run" in entry.description


# ---------------------------------------------------------------------------
# End-to-end against a Flask test app with a stub route implementing the
# exact w08 contract (status codes + envelope shapes).
# ---------------------------------------------------------------------------

_REGISTERED = {"agent-1"}


def _stub_issue_app():
    from sincor2.defi.p24 import live_block, policy

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.post(ISSUE_PATH)
    def stub_issue():
        body = request.get_json(silent=True) or {}

        def err(message, status, **extra):
            payload = {"error": message, "status": status}
            payload.update(extra)
            return jsonify(payload), status

        agent_id = str(body.get("agent_id") or "").strip()
        if agent_id not in _REGISTERED:
            return err("agent authentication required", 401)
        name = str(body.get("name") or "").strip()
        symbol = str(body.get("symbol") or "").strip().upper()
        description = str(body.get("description") or "")
        bio = str(body.get("bio") or "")
        dry_run = body.get("dry_run", True)
        if not isinstance(dry_run, bool):
            return err("dry_run must be a boolean", 400)
        try:
            version = policy.require_clean(name, symbol, description, bio)
        except policy.PolicyViolation as pve:
            return err(
                f"content-policy violation in {pve.field!r}: "
                f"matched phrase {pve.matched_phrase!r}",
                400, field=pve.field,
                matched_phrase=pve.matched_phrase,
                ruleset_version=pve.ruleset_version)
        if not dry_run and live_block.LIVE_BLOCKED:
            return err("P24 is live-blocked by catalog design", 403)
        return jsonify({
            "status": "issued",
            "creator_id": agent_id,
            "agent_id": agent_id,
            "name": name,
            "symbol": symbol,
            "issued_at": "2026-09-30T00:00:00Z",
            "total_supply_wei": "1000000000000000000000000000",
            "policy_version": version,
            "screened": True,
            "dry_run": True,
            "live_blocked": live_block.LIVE_BLOCKED,
        }), 201

    return app


@pytest.fixture
def app_client():
    from sincor2.a2a_sdk import FlaskTestTransport
    return FlaskTestTransport(_stub_issue_app().test_client())


def test_e2e_dry_run_against_test_app(app_client):
    result = issue_creator_token(app_client, **BASE_KW)
    assert result.symbol == "AURIGA"
    assert result.screened is True


def test_e2e_policy_denial_never_echoes_submitted_text(app_client):
    nasty = "guaranteed 10x price pump moon"
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(app_client, **BASE_KW, description=nasty)
    assert exc.value.code == "policy"
    assert exc.value.ruleset_version == "1.0.0"
    assert nasty not in str(exc.value)


def test_e2e_live_refusal_and_unknown_agent(app_client):
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(app_client, **BASE_KW, dry_run=False)
    assert exc.value.code == "live_blocked"
    with pytest.raises(IssuanceSkillError) as exc:
        issue_creator_token(app_client, agent_id="ghost",
                            name="N", symbol="SYM")
    assert exc.value.code == "auth"
