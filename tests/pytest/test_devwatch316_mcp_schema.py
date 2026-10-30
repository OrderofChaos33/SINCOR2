"""Dev-watch item 316 — MCP tool-schema strictness (Skyfire postmortem).

Skyfire's MCP payment tools broke across frameworks because of OPTIONAL /
NULLABLE tool parameters: Anthropic callers passed the literal string
"null" for omitted fields, AutoGen mishandles ``type: ["string", "null"]``
unions, and servers silently coerced whatever arrived.

The SINCOR2 rule enforced here, on BOTH MCP servers (the marketplace
discovery server ``sincor2.mcp_server`` and Swarm Command
``sincor2.swarm_command_mcp``):

1. No nullable union types (``["string", "null"]``, ``anyOf``-with-null)
   in any published tool input schema.
2. Payment-adjacent parameters are always ``required`` — no optional
   payment fields in a published schema.
3. Server-side strict validation on every ``tools/call``: a literal
   string ``"null"``, a JSON ``null``, a missing required field, or a
   wrong-typed value is REJECTED with a loud typed error (-32602 naming
   the field) — never coerced, never silently mis-shaped.

Call-shape tests drive Anthropic-style (string "null"), AutoGen-style
(union-shaped values), and OpenAI-style (correctly typed) arguments:
each must either succeed identically or fail loudly.
"""

from __future__ import annotations

import io
import json

import pytest

import sincor2.mcp_server as ms
from sincor2 import mcp_schema_guard as guard
from sincor2 import swarm_command_mcp as swarm


# ---------------------------------------------------------------------------
# Schema lint — both servers' published schemas
# ---------------------------------------------------------------------------

def _all_tools():
    return list(ms.TOOLS) + list(swarm._tool_defs())


def test_lint_clean_on_both_servers():
    violations = guard.find_schema_violations(_all_tools())
    assert violations == [], violations


def _walk_schemas(node, path="$"):
    """Yield (path, subschema-dict) for every dict in a schema tree."""
    if isinstance(node, dict):
        yield path, node
        for key, val in node.items():
            yield from _walk_schemas(val, f"{path}.{key}")
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from _walk_schemas(val, f"{path}[{i}]")


def test_no_nullable_unions_raw_walk():
    """Independent of the guard: no published schema may declare nullability."""
    for tool in _all_tools():
        for path, sub in _walk_schemas(tool["inputSchema"]):
            t = sub.get("type")
            assert not (isinstance(t, list) and "null" in t), (
                f"{tool['name']} {path}: nullable union type {t}")
            assert t != "null", f"{tool['name']} {path}: bare null type"


def test_lint_flags_nullable_union_type():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"note": {"type": ["string", "null"]}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("note" in v and "null" in v for v in violations), violations


def test_lint_flags_anyof_null_branch():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"note": {"anyOf": [{"type": "string"},
                                          {"type": "null"}]}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("note" in v for v in violations), violations


def test_lint_flags_optional_payment_field():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"bid_axm": {"type": "number"}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("bid_axm" in v for v in violations), violations

    good = [{"name": "t", "inputSchema": {
        "type": "object", "required": ["bid_axm"],
        "properties": {"bid_axm": {"type": "number"},
                       "memo": {"type": "string"}},
    }}]
    assert guard.find_schema_violations(good) == []


# ---------------------------------------------------------------------------
# Validator unit tests
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "required": ["task_id", "bid_axm"],
    "properties": {
        "task_id": {"type": "string"},
        "bid_axm": {"type": "number"},
        "estimated_seconds": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "flag": {"type": "boolean"},
    },
}


def test_validator_accepts_valid_and_omitted_optional():
    guard.validate_arguments("t", _SCHEMA, {"task_id": "a", "bid_axm": 1.5})
    guard.validate_arguments("t", _SCHEMA, {
        "task_id": "a", "bid_axm": 2, "estimated_seconds": 30,
        "tags": ["x"], "flag": True,
    })


@pytest.mark.parametrize("args,fragment", [
    ({"task_id": "null", "bid_axm": 1.0}, "task_id"),          # Anthropic "null"
    ({"bid_axm": 1.0}, "task_id"),                             # missing required
    ({"task_id": None, "bid_axm": 1.0}, "task_id"),            # JSON null
    ({"task_id": "a", "bid_axm": "null"}, "bid_axm"),          # "null" for number
    ({"task_id": "a", "bid_axm": "5.5"}, "bid_axm"),           # string, no coercion
    ({"task_id": "a", "bid_axm": 1.0, "estimated_seconds": True},
     "estimated_seconds"),                                     # bool is not int
    ({"task_id": "a", "bid_axm": 1.0, "estimated_seconds": "300"},
     "estimated_seconds"),                                     # string, no coercion
    ({"task_id": "a", "bid_axm": 1.0, "tags": "lead"}, "tags"),  # str not array
    ({"task_id": "a", "bid_axm": 1.0, "tags": ["ok", 5]}, "tags"),  # bad item
    ({"task_id": "a", "bid_axm": 1.0, "tags": ["null"]}, "tags"),   # "null" item
    ({"task_id": ["a"], "bid_axm": 1.0}, "task_id"),           # union-shaped list
])
def test_validator_rejects_loudly(args, fragment):
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA, args)
    assert fragment in str(excinfo.value)


# ---------------------------------------------------------------------------
# Call shapes — marketplace discovery server (sincor2.mcp_server)
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_upstream(monkeypatch):
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        return {"ok": True, "status": 200,
                "data": {"price_axm": "1.0", "auctions": [], "cards": []}}

    monkeypatch.setattr(ms, "_request", fake_request)
    return calls


def _call_rpc(name, arguments):
    return ms.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def _assert_loud_rejection(resp, field):
    assert resp is not None and "error" in resp, resp
    assert resp["error"]["code"] == -32602, resp
    assert field in resp["error"]["message"], resp


def test_discovery_anthropic_string_null_bid_rejected(stub_upstream):
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": "null"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []  # nothing reached the platform


def test_discovery_anthropic_string_null_task_id_rejected(stub_upstream):
    resp = _call_rpc("get_task", {"task_id": "null"})
    _assert_loud_rejection(resp, "task_id")
    assert stub_upstream == []


def test_discovery_autogen_union_shaped_values_rejected(stub_upstream):
    # AutoGen-style: a value shaped by a ["string", "null"] union arrives
    # as a list (or a stringified number) — must fail loudly, not coerce.
    resp = _call_rpc("get_quote", {"skill_id": ["lead-enrichment"]})
    _assert_loud_rejection(resp, "skill_id")

    resp = _call_rpc("list_tasks", {"limit": "50"})
    _assert_loud_rejection(resp, "limit")
    assert stub_upstream == []


def test_discovery_string_bid_never_coerced(stub_upstream):
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": "5.5"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


def test_discovery_missing_required_rejected(stub_upstream):
    resp = _call_rpc("submit_bid", {"task_id": "tsk_1", "agent_id": "a1"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


def test_discovery_json_null_rejected(stub_upstream):
    resp = _call_rpc("get_task", {"task_id": None})
    _assert_loud_rejection(resp, "task_id")
    assert stub_upstream == []


def test_discovery_register_agent_wrong_typed_tags_rejected(stub_upstream):
    resp = _call_rpc("register_agent", {"capability_tags": "lead-enrichment"})
    _assert_loud_rejection(resp, "capability_tags")
    resp = _call_rpc("register_agent", {"capability_tags": ["ok", 7]})
    _assert_loud_rejection(resp, "capability_tags")
    assert stub_upstream == []


def test_discovery_openai_style_valid_call_succeeds(stub_upstream):
    resp = _call_rpc("get_quote", {
        "skill_id": "lead-enrichment", "caller_id": "openai-caller"})
    assert resp is not None and "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["price_axm"] == "1.0"
    assert len(stub_upstream) == 1


def test_discovery_payment_gate_and_validation_layering(stub_upstream):
    """Item 80 layering preserved: the payment gate is outermost for
    unpaid calls (the challenge is the loud response; nothing executes).
    A call carrying a token passes the gate and THEN faces strict
    validation — an Anthropic "null" is rejected before dispatch."""
    ms.register_paid_tool("get_quote", price="0.01", pay_to="0x" + "ab" * 20)
    try:
        # No token -> payment challenge result (not an error, no execute).
        resp = _call_rpc("get_quote", {"skill_id": "lead-enrichment"})
        assert resp is not None and "result" in resp, resp
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "payment_required"
        assert stub_upstream == []
        # Token attached + invalid shape -> validation rejects loudly.
        resp = _call_rpc("get_quote", {
            "skill_id": "null", "x402_access_token": "tok"})
        _assert_loud_rejection(resp, "skill_id")
        assert stub_upstream == []
        # Token attached + valid shape -> executes identically.
        resp = _call_rpc("get_quote", {
            "skill_id": "lead-enrichment", "x402_access_token": "tok"})
        assert resp is not None and "result" in resp, resp
        assert len(stub_upstream) == 1
    finally:
        ms.PAID_TOOLS.discard("get_quote")
        ms.PAID_TOOL_PRICING.pop("get_quote", None)


# ---------------------------------------------------------------------------
# Call shapes — Swarm Command server (WSGI)
# ---------------------------------------------------------------------------

TOKEN = "dw316-test-token"


@pytest.fixture
def swarm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.delenv("SWARM_MCP_CORS_ORIGINS", raising=False)


def _swarm_call(name, arguments):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    raw = json.dumps(req).encode("utf-8")
    environ = {
        "REQUEST_METHOD": "POST", "PATH_INFO": "/mcp",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "HTTP_AUTHORIZATION": "Bearer %s" % TOKEN,
        "wsgi.input": io.BytesIO(raw),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    body = b"".join(swarm.app(environ, start_response))
    return json.loads(body.decode("utf-8"))


def _swarm_add_card(**overrides):
    args = {"lane": "brainstorm", "title": "Hello", "body": "world",
            "owner": "Muse"}
    args.update(overrides)
    return _swarm_call("board_card_add", args)


def test_swarm_anthropic_string_null_rejected_no_side_effect(swarm_env):
    resp = _swarm_add_card(title="null")
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "title" in resp["error"]["message"], resp
    # Nothing was written: the board is still empty.
    read = _swarm_call("board_lanes_read", {})
    lanes = json.loads(read["result"]["content"][0]["text"])
    assert all(not cards for cards in lanes.values()), lanes


def test_swarm_missing_required_rejected(swarm_env):
    args = {"lane": "brainstorm", "title": "Hello", "body": "world"}
    resp = _swarm_call("board_card_add", args)
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "owner" in resp["error"]["message"], resp


def test_swarm_wrong_type_rejected(swarm_env):
    resp = _swarm_add_card(lane=123)
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "lane" in resp["error"]["message"], resp


def test_swarm_optional_field_string_null_rejected(swarm_env):
    # discussion_read.topic is optional, but an explicit "null" string is
    # the Anthropic failure shape — reject, never look up a "null" thread.
    resp = _swarm_call("discussion_read", {"topic": "null"})
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "topic" in resp["error"]["message"], resp


def test_swarm_openai_style_valid_call_succeeds_identically(swarm_env):
    resp = _swarm_add_card()
    assert "result" in resp, resp
    card = json.loads(resp["result"]["content"][0]["text"])
    assert card["title"] == "Hello"
    # Omitted optional argument still works.
    read = _swarm_call("discussion_read", {})
    assert "result" in read, read
